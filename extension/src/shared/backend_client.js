/**
 * Smart Query Router - Backend Client with Secure Transport & Fail-Open Safety
 * 
 * DESIGN PRINCIPLES:
 * - Secure transport: Enforces https:// in production; permits http:// for 127.0.0.1/localhost dev.
 * - Timeouts & bounded retries: Uses AbortController and exponential backoff with jitter.
 * - Strict fail-open guarantee: If the backend is unreachable or times out, returns
 *   a safe fallback decision (NO_OPTIMIZATION) so Claude continues working normally.
 * - Zero privacy leakage: NEVER logs prompt queries or conversation content on failures.
 * - Configurable without secrets: Storage-backed endpoint with sensible local dev default.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterBackendClient = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_CONFIG = Object.freeze({
    endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
    timeoutMs: 1500,
    maxRetries: 2,
    initialBackoffMs: 100,
    maxBackoffMs: 500,
    backoffMultiplier: 2
  });

  /**
   * Validates that an endpoint complies with secure transport rules:
   * Requires https:// unless pointing to local loopback (127.0.0.1 or localhost).
   * 
   * @param {string} endpointUrl
   * @returns {{ valid: boolean, error?: string, sanitizedUrl?: string }}
   */
  function validateEndpoint(endpointUrl) {
    if (typeof endpointUrl !== 'string' || !endpointUrl.trim()) {
      return { valid: false, error: 'Endpoint URL must be a non-empty string' };
    }

    try {
      const parsed = new URL(endpointUrl.trim());
      const isLocal = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost';

      if (parsed.protocol === 'https:') {
        return { valid: true, sanitizedUrl: parsed.origin + parsed.pathname };
      }

      if (parsed.protocol === 'http:' && isLocal) {
        return { valid: true, sanitizedUrl: parsed.origin + parsed.pathname };
      }

      return {
        valid: false,
        error: `Insecure transport rejected: non-local endpoints must use https:// (got ${parsed.protocol})`
      };
    } catch (e) {
      return { valid: false, error: `Invalid URL format: ${e.message}` };
    }
  }

  /**
   * Safe sleep utility for backoff
   * @param {number} ms
   * @returns {Promise<void>}
   */
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Computes exponential backoff delay with random jitter
   * @param {number} attempt
   * @param {object} config
   * @returns {number} Delay in milliseconds
   */
  function calculateBackoffDelay(attempt, config) {
    const base = config.initialBackoffMs * Math.pow(config.backoffMultiplier, attempt);
    const capped = Math.min(config.maxBackoffMs, base);
    // Add 10-20% jitter
    const jitter = Math.floor(Math.random() * (capped * 0.2));
    return capped + jitter;
  }

  /**
   * Produces a strict fail-open fallback decision object
   * @param {string} requestId
   * @param {string} errorReason
   * @returns {object}
   */
  function createFailOpenDecision(requestId, errorReason, correlationId = null) {
    return {
      request_id: requestId || 'req_fail_open',
      correlation_id: correlationId || requestId || 'corr_fail_open',
      decision_type: 'NO_OPTIMIZATION',
      confidence: 0.0,
      reason_code: 'FAIL_OPEN_FALLBACK',
      optimization_instructions: null,
      failOpen: true,
      errorReason: errorReason || 'BACKEND_UNAVAILABLE',
      timestamp: Date.now()
    };
  }

  class BackendClient {
    /**
     * @param {object} [options]
     * @param {string} [options.endpoint]
     * @param {number} [options.timeoutMs]
     * @param {number} [options.maxRetries]
     * @param {object} [options.logger]
     * @param {object} [options.storage] - chrome.storage.local or mock
     */
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULT_CONFIG, options);
      this.logger = options.logger || null;
      this.storage = options.storage || (typeof chrome !== 'undefined' && chrome.storage ? chrome.storage.local : null);
    }

    /**
     * Reads configured endpoint from storage with fallback to default
     * @returns {Promise<string>}
     */
    async getEffectiveEndpoint() {
      if (this.storage && typeof this.storage.get === 'function') {
        try {
          const stored = await new Promise((resolve) => {
            this.storage.get(['backendEndpoint'], (res) => resolve(res || {}));
          });
          if (stored.backendEndpoint) {
            const val = validateEndpoint(stored.backendEndpoint);
            if (val.valid) return stored.backendEndpoint;
          }
        } catch (_) {
          // Fail open to default on storage error
        }
      }
      return this.config.endpoint;
    }

    /**
     * Sends a single HTTP POST request with AbortController timeout
     * @param {string} endpoint
     * @param {object} queryPackage
     * @param {number} timeoutMs
     * @returns {Promise<object>}
     */
    async _executeFetch(endpoint, queryPackage, timeoutMs) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);

      try {
        const fetchFn = typeof fetch !== 'undefined' ? fetch : globalThis.fetch;
        if (!fetchFn) {
          throw new Error('Fetch API not available in current runtime');
        }

        const correlationId = (queryPackage && (queryPackage.correlation_id || queryPackage.request_id)) || null;
        // Strictly minimal request headers: zero cookies, zero authorization headers, zero session tokens
        const headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        };
        if (correlationId) {
          headers['X-Correlation-ID'] = correlationId;
        }

        const response = await fetchFn(endpoint, {
          method: 'POST',
          headers,
          body: JSON.stringify(queryPackage),
          signal: controller.signal,
          credentials: 'omit' // Privacy requirement: strictly omit cookies, HTTP auth, or session credentials
        });

        clearTimeout(timer);

        if (!response.ok) {
          const err = new Error(`HTTP error ${response.status}`);
          err.status = response.status;
          throw err;
        }

        return await response.json();
      } catch (err) {
        clearTimeout(timer);
        throw err;
      }
    }

    /**
     * Sends query package to backend with timeouts, retries, and strict fail-open guarantee.
     * 
     * GUARANTEES:
     * - Never throws an unhandled error; always resolves to a decision.
     * - Does NOT block indefinitely (bounded timeout).
     * - Does NOT log full query on network failure.
     * 
     * @param {object} queryPackage - NormalizedQueryPackage
     * @param {object} [callOptions]
     * @returns {Promise<object>} OptimizationDecisionResponse or FailOpenDecision
     */
    async optimizeQuery(queryPackage, callOptions = {}) {
      const requestId = queryPackage && queryPackage.request_id ? queryPackage.request_id : 'req_unknown';
      const correlationId = (queryPackage && (queryPackage.correlation_id || queryPackage.request_id)) || 'corr_unknown';
      const timeoutMs = callOptions.timeoutMs || this.config.timeoutMs;
      const maxRetries = callOptions.maxRetries !== undefined ? callOptions.maxRetries : this.config.maxRetries;

      // 1. Resolve and validate endpoint
      const rawEndpoint = await this.getEffectiveEndpoint();
      const validation = validateEndpoint(rawEndpoint);
      if (!validation.valid) {
        if (this.logger) {
          this.logger.warn('BACKEND_CALL', 'Invalid backend endpoint configuration, failing open', {
            error: validation.error,
            requestId,
            correlationId
          });
        }
        return createFailOpenDecision(requestId, 'INVALID_ENDPOINT_CONFIGURATION', correlationId);
      }

      const endpoint = rawEndpoint;
      const startTime = Date.now();
      let lastError = null;

      // 2. Retry loop with exponential backoff
      for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
          const decision = await this._executeFetch(endpoint, queryPackage, timeoutMs);
          const latencyMs = Date.now() - startTime;

          if (this.logger) {
            this.logger.info('BACKEND_CALL', 'Backend optimization call succeeded', {
              requestId,
              latencyMs,
              decisionType: decision.decision_type,
              attempt
            });
          }

          return decision;
        } catch (err) {
          lastError = err;
          const isAbort = err.name === 'AbortError';
          const isClientError = err.status >= 400 && err.status < 500;

          // Do not retry client validation errors (e.g. 422, 400)
          if (isClientError) {
            break;
          }

          // If more retries remain, apply exponential backoff
          if (attempt < maxRetries) {
            const backoffMs = calculateBackoffDelay(attempt, this.config);
            await sleep(backoffMs);
          }
        }
      }

      // 3. Strict Fail-Open: Log failure safely (ZERO raw prompt/query text) and return fallback
      const totalLatencyMs = Date.now() - startTime;
      const isTimeout = lastError && lastError.name === 'AbortError';
      const failureReason = isTimeout ? 'TIMEOUT' : (lastError ? (lastError.message || 'NETWORK_ERROR') : 'UNKNOWN');

      if (this.logger) {
        // Privacy rule: Only log metadata, latency, and status. NEVER log the user prompt or query!
        this.logger.warn('BACKEND_CALL', 'Backend optimization unavailable, falling open', {
          requestId,
          correlationId,
          failureReason,
          latencyMs: totalLatencyMs,
          attempts: maxRetries + 1
        });
      }

      return createFailOpenDecision(requestId, failureReason, correlationId);
    }
  }

  return {
    DEFAULT_CONFIG,
    validateEndpoint,
    calculateBackoffDelay,
    createFailOpenDecision,
    BackendClient
  };
});
