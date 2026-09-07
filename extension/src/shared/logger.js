/**
 * Smart Query Router - Diagnostic Logger
 * Provides structured, privacy-preserving diagnostic logging with levels and event categories.
 * Enforces strict redaction of tokens, cookies, session IDs, and raw query text.
 * Quiet by default in production.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterLogger = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const LogLevel = Object.freeze({
    DEBUG: 0,
    INFO: 1,
    WARN: 2,
    ERROR: 3,
    SILENT: 4
  });

  const LogLevelNames = Object.freeze({
    0: 'DEBUG',
    1: 'INFO',
    2: 'WARN',
    3: 'ERROR',
    4: 'SILENT'
  });

  const EventCategory = Object.freeze({
    STARTUP: 'STARTUP',
    PAGE_ATTACH: 'PAGE_ATTACH',
    PAGE_DETACH: 'PAGE_DETACH',
    QUERY_DETECTION: 'QUERY_DETECTION',
    OPTIMIZATION_DECISION: 'OPTIMIZATION_DECISION',
    CACHE_DECISION: 'CACHE_DECISION',
    BACKEND_CALL: 'BACKEND_CALL',
    EXPERIMENTAL_METRICS: 'EXPERIMENTAL_METRICS',
    FAILURE: 'FAILURE'
  });

  // Keys that must NEVER be logged in raw form
  const SENSITIVE_KEY_PATTERN = /^(cookie|token|auth|authorization|password|secret|session|sessionid|jwt|key|credential|conversation|prompt|query|text|input)$/i;

  const MAX_RING_BUFFER_SIZE = 100;

  /**
   * Redact identifiers for safe correlation
   * @param {string|number} id
   * @returns {string}
   */
  function redactIdentifier(id) {
    if (id === null || id === undefined) return 'anon';
    const str = String(id);
    if (str.length <= 4) {
      return `id-${str.slice(0, 1)}***`;
    }
    return `id-${str.slice(0, 2)}***${str.slice(-2)}`;
  }

  /**
   * Recursively sanitizes metadata to ensure zero raw prompt, token, or cookie leakage
   * @param {any} value
   * @param {number} depth
   * @returns {any}
   */
  function sanitizeMetadata(value, depth = 0) {
    if (depth > 4) return '[MAX_DEPTH]';
    if (value === null || value === undefined) return value;

    if (typeof value !== 'object') {
      return value;
    }

    if (Array.isArray(value)) {
      return value.map((item) => sanitizeMetadata(item, depth + 1));
    }

    const sanitized = {};
    for (const [k, v] of Object.entries(value)) {
      if (SENSITIVE_KEY_PATTERN.test(k)) {
        if (typeof v === 'string') {
          // Record non-identifying metric (length only) without logging raw text
          sanitized[`${k}_length`] = v.length;
          sanitized[k] = '[REDACTED]';
        } else {
          sanitized[k] = '[REDACTED]';
        }
      } else {
        sanitized[k] = sanitizeMetadata(v, depth + 1);
      }
    }
    return sanitized;
  }

  class DiagnosticLogger {
    constructor(options = {}) {
      // Quiet by default in production (WARN or higher only)
      this.currentLevel = options.level !== undefined ? options.level : LogLevel.WARN;
      this.prefix = options.prefix || '[Smart Query Router]';
      this.ringBuffer = [];
      this.maxBufferSize = options.maxBufferSize || MAX_RING_BUFFER_SIZE;
      this.enableConsole = options.enableConsole !== undefined ? options.enableConsole : true;
    }

    setLevel(level) {
      if (typeof level === 'number' && level >= LogLevel.DEBUG && level <= LogLevel.SILENT) {
        this.currentLevel = level;
      }
    }

    getLevel() {
      return this.currentLevel;
    }

    _record(level, category, message, metadata, correlationId) {
      // Validate category
      const validCategory = Object.values(EventCategory).includes(category)
        ? category
        : EventCategory.FAILURE;

      const entry = {
        timestamp: Date.now(),
        level: LogLevelNames[level] || 'UNKNOWN',
        category: validCategory,
        correlationId: correlationId ? redactIdentifier(correlationId) : null,
        message: String(message),
        metadata: metadata ? sanitizeMetadata(metadata) : null
      };

      // Push to in-memory diagnostic ring buffer
      this.ringBuffer.push(entry);
      if (this.ringBuffer.length > this.maxBufferSize) {
        this.ringBuffer.shift();
      }

      // Output to console only if level meets threshold and console output is active
      if (this.enableConsole && level >= this.currentLevel && this.currentLevel < LogLevel.SILENT) {
        const tag = `${this.prefix} [${entry.level}] [${entry.category}]`;
        const logArgs = [tag, entry.message];
        if (entry.correlationId) {
          logArgs.push(`[corr:${entry.correlationId}]`);
        }
        if (entry.metadata) {
          logArgs.push(entry.metadata);
        }

        switch (level) {
          case LogLevel.DEBUG:
            console.debug(...logArgs);
            break;
          case LogLevel.INFO:
            console.info(...logArgs);
            break;
          case LogLevel.WARN:
            console.warn(...logArgs);
            break;
          case LogLevel.ERROR:
            console.error(...logArgs);
            break;
        }
      }

      return entry;
    }

    debug(category, message, metadata, correlationId) {
      return this._record(LogLevel.DEBUG, category, message, metadata, correlationId);
    }

    info(category, message, metadata, correlationId) {
      return this._record(LogLevel.INFO, category, message, metadata, correlationId);
    }

    warn(category, message, metadata, correlationId) {
      return this._record(LogLevel.WARN, category, message, metadata, correlationId);
    }

    error(category, message, metadata, correlationId) {
      return this._record(LogLevel.ERROR, category, message, metadata, correlationId);
    }

    getRecentLogs() {
      return [...this.ringBuffer];
    }

    clearLogs() {
      this.ringBuffer = [];
    }
  }

  // Create default singleton logger instance (quiet by default: LogLevel.WARN)
  const defaultLogger = new DiagnosticLogger();

  return {
    LogLevel,
    LogLevelNames,
    EventCategory,
    DiagnosticLogger,
    defaultLogger,
    redactIdentifier,
    sanitizeMetadata
  };
});
