/**
 * Smart Query Router - Performance Telemetry & Correlation Tracker
 * 
 * DESIGN PRINCIPLES:
 * - Single correlation identifier across extension observation and backend response.
 * - Records ONLY metadata necessary for performance evaluation:
 *   timestamps, decision type, model route, cache outcome, latency, error category,
 *   feature summary, and version identifiers.
 * - Zero raw query text in production telemetry by default.
 * - Clear, opt-in development-only debugging mechanism (disabled by default).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterTelemetry = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const CacheOutcome = Object.freeze({
    HIT: 'HIT',
    MISS: 'MISS',
    BYPASS: 'BYPASS',
    NOT_CHECKED: 'NOT_CHECKED'
  });

  const ErrorCategory = Object.freeze({
    NONE: 'NONE',
    TIMEOUT: 'TIMEOUT',
    NETWORK_ERROR: 'NETWORK_ERROR',
    SCHEMA_ERROR: 'SCHEMA_ERROR',
    SERVER_ERROR: 'SERVER_ERROR',
    CLIENT_ERROR: 'CLIENT_ERROR'
  });

  /**
   * Generates a unique, non-sensitive correlation identifier.
   * Format: corr_<timestamp>_<random>
   * 
   * @returns {string}
   */
  function generateCorrelationId() {
    const ts = Date.now();
    const rand = Math.random().toString(36).slice(2, 9);
    return `corr_${ts}_${rand}`;
  }

  /**
   * Buckets character counts to avoid leaking exact prompt lengths in telemetry.
   * @param {number} charCount
   * @returns {string}
   */
  function bucketCharacterCount(charCount) {
    if (typeof charCount !== 'number' || charCount <= 0) return '0';
    if (charCount < 50) return '<50';
    if (charCount < 200) return '50-200';
    if (charCount < 1000) return '200-1000';
    if (charCount < 5000) return '1000-5000';
    return '>5000';
  }

  /**
   * Extracts safe, non-identifying feature metadata for performance analysis.
   * GUARANTEE: Never extracts or includes raw prompt text or tokens.
   * 
   * @param {object|null} localFeatures
   * @param {number} candidateCount
   * @returns {object}
   */
  function extractSafeFeatureSummary(localFeatures, candidateCount = 0) {
    const feats = localFeatures || {};
    return {
      char_bucket: bucketCharacterCount(feats.character_count || 0),
      has_code: Boolean(feats.has_code),
      has_math: Boolean(feats.has_math),
      has_questions: Boolean(feats.has_questions),
      has_urls: Boolean(feats.has_urls),
      is_normalized: Boolean(feats.is_normalized),
      cue_count: Array.isArray(feats.detected_cues) ? feats.detected_cues.length : 0,
      candidate_count: typeof candidateCount === 'number' ? candidateCount : 0
    };
  }

  /**
   * Evaluates if development debug mode is explicitly active.
   * Default is strictly false (production mode).
   * 
   * @param {object} [options]
   * @returns {boolean}
   */
  function isDebugModeEnabled(options = {}) {
    return options.debugMode === true || options.environment === 'development';
  }

  /**
   * Creates a structured performance evaluation telemetry record.
   * 
   * GUARANTEES:
   * - Never includes query_text, prompt strings, or conversation snippets in production records.
   * - In production, debug_metadata is strictly null.
   * - In development debug mode (opt-in), provides non-production diagnostic traces.
   * 
   * @param {object} params
   * @param {string} params.correlationId - Correlation ID matching request
   * @param {number} params.clientTimestamp - Timestamp when client observation started
   * @param {number} [params.backendTimestamp] - Timestamp from backend response
   * @param {string} params.decisionType - Outcome (e.g. 'NO_OPTIMIZATION', 'BACKEND_CANDIDATE')
   * @param {string|null} [params.modelRoute] - Model ID if recommended (e.g. 'claude-3-5-haiku')
   * @param {string} [params.cacheOutcome] - CacheOutcome enum value
   * @param {number} params.latencyMs - Total client elapsed latency in ms
   * @param {string} [params.errorCategory] - ErrorCategory enum value
   * @param {object|null} [params.localFeatures] - Non-generative features object
   * @param {number} [params.candidateCount] - Count of candidate turns considered
   * @param {object} [params.versionIdentifiers] - Version metadata
   * @param {object} [params.options] - Options controlling debug mode
   * @param {object} [params.debugTrace] - Optional diagnostic info for dev mode only
   * @returns {object} PerformanceTelemetryRecord
   */
  function createPerformanceRecord({
    correlationId,
    clientTimestamp,
    backendTimestamp = null,
    decisionType = 'NO_OPTIMIZATION',
    modelRoute = null,
    cacheOutcome = CacheOutcome.NOT_CHECKED,
    latencyMs = 0.0,
    errorCategory = ErrorCategory.NONE,
    localFeatures = null,
    candidateCount = 0,
    versionIdentifiers = {},
    options = {},
    debugTrace = null,
    coarseRoute = null,
    modelVersion = null,
    failureCategory = 'NONE',
    executionLatencyMs = null,
    escalationOccurred = false,
    escalationReason = null
  }) {
    const isDev = isDebugModeEnabled(options);

    const record = {
      correlation_id: correlationId || generateCorrelationId(),
      client_timestamp: typeof clientTimestamp === 'number' ? clientTimestamp : Date.now(),
      backend_timestamp: typeof backendTimestamp === 'number' ? backendTimestamp : null,
      decision_type: decisionType,
      coarse_route: coarseRoute || null,
      model_route: modelRoute || null,
      model_version: modelVersion || null,
      cache_outcome: Object.values(CacheOutcome).includes(cacheOutcome) ? cacheOutcome : CacheOutcome.NOT_CHECKED,
      latency_ms: Number(Number(latencyMs).toFixed(2)),
      execution_latency_ms: typeof executionLatencyMs === 'number' ? Number(executionLatencyMs.toFixed(2)) : null,
      error_category: Object.values(ErrorCategory).includes(errorCategory) ? errorCategory : ErrorCategory.NONE,
      failure_category: failureCategory || 'NONE',
      escalation_occurred: Boolean(escalationOccurred),
      escalation_reason: escalationReason || null,
      feature_identifiers: extractSafeFeatureSummary(localFeatures, candidateCount),
      version_identifiers: Object.assign(
        { extension: '0.1.0', server: '0.1.0', schema: '1.0' },
        versionIdentifiers
      ),
      debug_metadata: isDev && debugTrace && typeof debugTrace === 'object'
        ? Object.assign({}, debugTrace)
        : null
    };

    return record;
  }

  return {
    CacheOutcome,
    ErrorCategory,
    generateCorrelationId,
    bucketCharacterCount,
    extractSafeFeatureSummary,
    isDebugModeEnabled,
    createPerformanceRecord
  };
});
