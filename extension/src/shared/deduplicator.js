/**
 * Smart Query Router - Query Submission Deduplicator
 * Provides bounded, in-memory deduplication to ensure query detection is strictly idempotent.
 * Prevents multiple optimization events caused by DOM re-renders, streaming updates,
 * rapid input triggers (Enter + click), or SPA route changes.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterDeduplicator = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_WINDOW_MS = 2500;       // 2.5 second window for identical queries
  const DEFAULT_MIN_COOLDOWN_MS = 600;   // 600ms cooldown between distinct submissions
  const DEFAULT_MAX_ENTRIES = 50;        // Bounded memory: keep at most 50 recent fingerprints

  /**
   * Fast 32-bit FNV-1a hash function for strings
   * @param {string} str
   * @returns {string} Hex hash string
   */
  function fnv1a(str) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
      hash ^= str.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16);
  }

  /**
   * Normalizes prompt text for robust comparison
   * Collapses internal whitespace, trims edges, and lowers case for fingerprinting
   * @param {string} text
   * @returns {string}
   */
  function normalizeText(text) {
    if (typeof text !== 'string') return '';
    return text.trim().replace(/\s+/g, ' ').toLowerCase();
  }

  class QueryDeduplicator {
    /**
     * @param {object} [options]
     * @param {number} [options.windowMs] - Time window for identical query deduplication
     * @param {number} [options.minCooldownMs] - Minimum duration between consecutive submissions
     * @param {number} [options.maxEntries] - Maximum memory bounds for tracked submissions
     */
    constructor(options = {}) {
      this.windowMs = options.windowMs !== undefined ? options.windowMs : DEFAULT_WINDOW_MS;
      this.minCooldownMs = options.minCooldownMs !== undefined ? options.minCooldownMs : DEFAULT_MIN_COOLDOWN_MS;
      this.maxEntries = options.maxEntries !== undefined ? options.maxEntries : DEFAULT_MAX_ENTRIES;

      // Map: fingerprint -> { timestamp, promptLength }
      this.recentEntries = new Map();
      this.lastSubmissionTime = 0;
    }

    /**
     * Generates a stable fingerprint for a query submission
     * @param {string} rawPrompt
     * @param {object} [context]
     * @returns {string}
     */
    computeFingerprint(rawPrompt, context = {}) {
      const normalized = normalizeText(rawPrompt);
      const hash = fnv1a(normalized);
      const convId = (context && context.conversationId) ? context.conversationId : 'conv_new';
      return `${convId}_${normalized.length}_${hash}`;
    }

    /**
     * Evict entries that exceed the time window
     * @param {number} now
     */
    _evictExpired(now) {
      for (const [key, entry] of this.recentEntries.entries()) {
        if (now - entry.timestamp > this.windowMs) {
          this.recentEntries.delete(key);
        } else {
          // Map maintains insertion order; once we hit an unexpired entry, earlier ones are done
          break;
        }
      }
    }

    /**
     * Evaluates whether a submission is a duplicate without recording it
     * @param {string} rawPrompt
     * @param {object} [context]
     * @param {number} [now]
     * @returns {{ duplicate: boolean, reason?: string, fingerprint: string }}
     */
    isDuplicate(rawPrompt, context = {}, now = Date.now()) {
      this._evictExpired(now);

      const fingerprint = this.computeFingerprint(rawPrompt, context);

      // Check 1: Minimum inter-submission cooldown
      if (now - this.lastSubmissionTime < this.minCooldownMs) {
        return {
          duplicate: true,
          reason: 'COOLDOWN_ACTIVE',
          fingerprint
        };
      }

      // Check 2: Identical query within deduplication window
      if (this.recentEntries.has(fingerprint)) {
        return {
          duplicate: true,
          reason: 'IDENTICAL_QUERY_IN_WINDOW',
          fingerprint
        };
      }

      return {
        duplicate: false,
        fingerprint
      };
    }

    /**
     * Attempts to record a submission. Returns accepted: true if idempotent, false if duplicate.
     * @param {string} rawPrompt
     * @param {object} [context]
     * @param {number} [now]
     * @returns {{ accepted: boolean, reason?: string, fingerprint: string }}
     */
    recordSubmission(rawPrompt, context = {}, now = Date.now()) {
      const check = this.isDuplicate(rawPrompt, context, now);
      if (check.duplicate) {
        return {
          accepted: false,
          reason: check.reason,
          fingerprint: check.fingerprint
        };
      }

      // Record new submission
      this.recentEntries.set(check.fingerprint, {
        timestamp: now,
        promptLength: typeof rawPrompt === 'string' ? rawPrompt.length : 0
      });

      this.lastSubmissionTime = now;

      // Enforce bounded memory size
      if (this.recentEntries.size > this.maxEntries) {
        const oldestKey = this.recentEntries.keys().next().value;
        if (oldestKey) {
          this.recentEntries.delete(oldestKey);
        }
      }

      return {
        accepted: true,
        fingerprint: check.fingerprint
      };
    }

    /**
     * Reset tracker state
     */
    reset() {
      this.recentEntries.clear();
      this.lastSubmissionTime = 0;
    }

    /**
     * Returns current size of memory-bounded tracker
     * @returns {number}
     */
    size() {
      return this.recentEntries.size;
    }
  }

  return {
    QueryDeduplicator,
    fnv1a,
    normalizeText,
    DEFAULT_WINDOW_MS,
    DEFAULT_MIN_COOLDOWN_MS,
    DEFAULT_MAX_ENTRIES
  };
});
