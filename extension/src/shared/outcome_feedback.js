/**
 * Smart Query Router - Outcome Feedback Data Model & Store
 * 
 * Provides typed, privacy-preserving outcome feedback models for:
 * - SUCCESSFUL_COMPLETION: Generation completed stably without errors.
 * - USER_REJECTION: User rejected optimization or provided negative feedback.
 * - OPTIMIZATION_BYPASS: User or settings bypassed optimization (e.g. Alt key, disabled setting).
 * - ESCALATION: Evaluator or gateway escalated to a stronger model or human.
 * - ERROR: Generation error banner, network fault, stream aborted, or DOM exception.
 * 
 * PRIVACY GUARANTEES:
 * - Strictly references the correlation identifier and structured routing metadata.
 * - NEVER stores full conversation, user prompt, or assistant response text by default.
 * - Automatically purges any accidental session IDs, tokens, cookies, or conversation fields.
 * - Prepares typed data structures for future optional user feedback controls (rating, rejection reasons).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterOutcomeFeedback = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * Outcome feedback categories
   */
  const FeedbackOutcomeType = Object.freeze({
    SUCCESSFUL_COMPLETION: 'SUCCESSFUL_COMPLETION',
    USER_REJECTION: 'USER_REJECTION',
    OPTIMIZATION_BYPASS: 'OPTIMIZATION_BYPASS',
    ESCALATION: 'ESCALATION',
    ERROR: 'ERROR'
  });

  /**
   * Source of outcome feedback
   */
  const FeedbackSource = Object.freeze({
    SYSTEM: 'SYSTEM', // Inferred automatically via lifecycle or routing
    USER: 'USER'      // Originating from user action or future feedback control
  });

  /**
   * Standard user ratings for future optional feedback controls
   */
  const UserRating = Object.freeze({
    POSITIVE: 'POSITIVE',
    NEGATIVE: 'NEGATIVE',
    NEUTRAL: 'NEUTRAL'
  });

  /**
   * Common user rejection reasons for future optional feedback controls
   */
  const UserRejectionReason = Object.freeze({
    UNWANTED_REWRITE: 'UNWANTED_REWRITE',
    INCORRECT_ANSWER: 'INCORRECT_ANSWER',
    HIGH_LATENCY: 'HIGH_LATENCY',
    PREFER_ORIGINAL: 'PREFER_ORIGINAL',
    OTHER: 'OTHER'
  });

  // Sensitive or conversational key patterns that must NEVER be persisted in feedback events
  const SENSITIVE_OR_CONVERSATION_KEY_PATTERN = /(prompt|query_text|raw_query|querytext|rawprompt|response_text|responsetext|turn_content|conversation|cookie|token|auth|bearer|password|secret|session|jwt|credential|apikey)/i;
  const URL_PAGE_DATA_PATTERN = /(href|pathname|search|hash)/i;

  /**
   * Sanitizes routing and execution metadata objects.
   * Drops any conversational content, raw text, session IDs, or credentials.
   * 
   * @param {any} obj
   * @param {number} [depth]
   * @returns {any}
   */
  function sanitizeMetadata(obj, depth = 0) {
    if (depth > 4) return undefined;
    if (obj === null || obj === undefined) return null;
    if (typeof obj !== 'object') return obj;

    if (Array.isArray(obj)) {
      return obj
        .map((item) => sanitizeMetadata(item, depth + 1))
        .filter((item) => item !== undefined);
    }

    const clean = {};
    for (const [k, v] of Object.entries(obj)) {
      // Purge forbidden conversational and sensitive keys
      if (SENSITIVE_OR_CONVERSATION_KEY_PATTERN.test(k) || URL_PAGE_DATA_PATTERN.test(k)) {
        continue;
      }
      const sanitizedVal = sanitizeMetadata(v, depth + 1);
      if (sanitizedVal !== undefined) {
        clean[k] = sanitizedVal;
      }
    }
    return clean;
  }

  /**
   * Generates a unique, non-sensitive feedback identifier
   * @param {number} [timestamp]
   * @returns {string}
   */
  function generateFeedbackId(timestamp = Date.now()) {
    const rand = Math.random().toString(36).slice(2, 8);
    return `fb_${timestamp}_${rand}`;
  }

  /**
   * Validates an outcome feedback object against the strict specification
   * @param {any} feedback
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateOutcomeFeedback(feedback) {
    if (!feedback || typeof feedback !== 'object' || Array.isArray(feedback)) {
      return { valid: false, error: 'Feedback must be a non-null object' };
    }

    if (typeof feedback.feedbackId !== 'string' || !feedback.feedbackId.trim()) {
      return { valid: false, error: 'feedbackId must be a non-empty string' };
    }

    if (typeof feedback.correlationId !== 'string' || !feedback.correlationId.trim()) {
      return { valid: false, error: 'correlationId must be a non-empty string referencing the correlation identifier' };
    }

    if (typeof feedback.timestamp !== 'number' || !Number.isFinite(feedback.timestamp) || feedback.timestamp <= 0) {
      return { valid: false, error: 'timestamp must be a valid positive number' };
    }

    const validOutcomes = Object.values(FeedbackOutcomeType);
    if (!validOutcomes.includes(feedback.outcomeType)) {
      return { valid: false, error: `outcomeType must be one of: ${validOutcomes.join(', ')}` };
    }

    const validSources = Object.values(FeedbackSource);
    if (!validSources.includes(feedback.source)) {
      return { valid: false, error: `source must be one of: ${validSources.join(', ')}` };
    }

    if (feedback.routingMetadata !== undefined && (typeof feedback.routingMetadata !== 'object' || feedback.routingMetadata === null || Array.isArray(feedback.routingMetadata))) {
      return { valid: false, error: 'routingMetadata must be an object if provided' };
    }

    if (feedback.executionMetadata !== undefined && (typeof feedback.executionMetadata !== 'object' || feedback.executionMetadata === null || Array.isArray(feedback.executionMetadata))) {
      return { valid: false, error: 'executionMetadata must be an object if provided' };
    }

    if (feedback.userFeedback !== undefined && feedback.userFeedback !== null) {
      if (typeof feedback.userFeedback !== 'object' || Array.isArray(feedback.userFeedback)) {
        return { valid: false, error: 'userFeedback must be an object if provided' };
      }
      const uf = feedback.userFeedback;
      if (uf.rating !== undefined && uf.rating !== null && !Object.values(UserRating).includes(uf.rating)) {
        return { valid: false, error: `userFeedback.rating must be one of: ${Object.values(UserRating).join(', ')}` };
      }
    }

    return { valid: true };
  }

  /**
   * Factory function to create an immutable, privacy-sanitized OutcomeFeedback event
   * 
   * GUARANTEES:
   * 1. References correlation identifier and routing metadata.
   * 2. Raw conversation, prompt, and response texts are strictly excluded.
   * 3. Completely frozen (immutable).
   * 
   * @param {object} params
   * @param {string} params.correlationId - Non-sensitive correlation token linking to request/decision
   * @param {string} [params.requestId] - Ephemeral request identifier
   * @param {string} params.outcomeType - One of FeedbackOutcomeType
   * @param {string} [params.source] - One of FeedbackSource (defaults to SYSTEM)
   * @param {object} [params.routingMetadata] - Routing decision metadata (coarseRoute, modelRoute, etc.)
   * @param {object} [params.executionMetadata] - Execution timing, substitution status, failure reasons
   * @param {object|null} [params.userFeedback] - Optional user rating, rejection reason, or feedback notes
   * @param {number} [params.timestamp]
   * @returns {object} Frozen OutcomeFeedback event
   */
  function createOutcomeFeedback({
    correlationId,
    requestId = null,
    outcomeType,
    source = FeedbackSource.SYSTEM,
    routingMetadata = {},
    executionMetadata = {},
    userFeedback = null,
    timestamp = Date.now(),
    recordedAt
  } = {}) {
    if (!correlationId || typeof correlationId !== 'string') {
      throw new Error('createOutcomeFeedback: correlationId is required');
    }
    if (!Object.values(FeedbackOutcomeType).includes(outcomeType)) {
      throw new Error(`createOutcomeFeedback: invalid outcomeType "${outcomeType}"`);
    }

    // Sanitize routing metadata (zero conversation content)
    const sanitizedRouting = sanitizeMetadata({
      coarseRoute: routingMetadata.coarseRoute || routingMetadata.coarse_route || null,
      modelRoute: routingMetadata.modelRoute || routingMetadata.model_route || null,
      modelVersion: routingMetadata.modelVersion || routingMetadata.model_version || null,
      decisionType: routingMetadata.decisionType || routingMetadata.decision_type || null,
      taskCategory: routingMetadata.taskCategory || routingMetadata.task_category || null,
      complexityLevel: routingMetadata.complexityLevel || routingMetadata.complexity_level || null,
      cacheOutcome: routingMetadata.cacheOutcome || routingMetadata.cache_outcome || null,
      ruleId: routingMetadata.ruleId || routingMetadata.rule_id || null,
      escalationOccurred: Boolean(routingMetadata.escalationOccurred || routingMetadata.escalation_occurred),
      escalationReason: routingMetadata.escalationReason || routingMetadata.escalation_reason || null,
      dryRun: Boolean(routingMetadata.dryRun || routingMetadata.dry_run)
    });

    // Sanitize execution metadata
    const sanitizedExecution = sanitizeMetadata({
      durationMs: typeof executionMetadata.durationMs === 'number' ? executionMetadata.durationMs : null,
      substitutionStatus: executionMetadata.substitutionStatus || null,
      failureReason: executionMetadata.failureReason || null
    });

    // Structure user feedback if present (prepared for future optional UI controls)
    let sanitizedUserFeedback = null;
    if (userFeedback && typeof userFeedback === 'object') {
      sanitizedUserFeedback = {
        rating: Object.values(UserRating).includes(userFeedback.rating) ? userFeedback.rating : null,
        rejectionReason: userFeedback.rejectionReason || userFeedback.rejection_reason || null,
        notes: typeof userFeedback.notes === 'string' ? userFeedback.notes.slice(0, 200).trim() : null,
        submittedAt: typeof userFeedback.submittedAt === 'number' ? userFeedback.submittedAt : timestamp
      };
    }

    const event = {
      feedbackId: generateFeedbackId(timestamp),
      correlationId: String(correlationId).trim(),
      requestId: requestId ? String(requestId).trim() : null,
      timestamp: typeof timestamp === 'number' ? timestamp : Date.now(),
      recordedAt: recordedAt !== undefined ? recordedAt : (typeof timestamp === 'number' ? timestamp : Date.now()),
      outcomeType,
      source: Object.values(FeedbackSource).includes(source) ? source : FeedbackSource.SYSTEM,
      routingMetadata: Object.freeze(sanitizedRouting),
      executionMetadata: Object.freeze(sanitizedExecution),
      userFeedback: sanitizedUserFeedback ? Object.freeze(sanitizedUserFeedback) : null,
      privacyPreserving: true
    };

    return Object.freeze(event);
  }

  /**
   * In-Memory Bounded Outcome Feedback Store
   * Enforces FIFO bounding and TTL eviction. Never writes conversation content to storage.
   */
  class FeedbackStore {
    /**
     * @param {object} [options]
     * @param {number} [options.maxEntries] - Maximum entries in ring buffer (default: 50)
     * @param {number} [options.retentionTtlMs] - TTL in milliseconds (default: 5 minutes)
     */
    constructor(options = {}) {
      this.maxEntries = typeof options.maxEntries === 'number' ? options.maxEntries : 50;
      this.retentionTtlMs = typeof options.retentionTtlMs === 'number' ? options.retentionTtlMs : 300_000;
      this.entries = [];
    }

    /**
     * Prune entries older than retention TTL
     * @param {number} [now]
     * @returns {number} Count of pruned entries
     */
    pruneExpired(now = Date.now()) {
      if (this.retentionTtlMs <= 0) return 0;
      const cutoff = now - this.retentionTtlMs;
      const before = this.entries.length;
      this.entries = this.entries.filter((entry) => {
        const time = typeof entry.recordedAt === 'number' ? entry.recordedAt : (entry.timestamp || 0);
        return time >= cutoff;
      });
      return before - this.entries.length;
    }

    /**
     * Record an outcome feedback event
     * @param {object} feedbackEvent
     * @returns {boolean} True if recorded successfully
     */
    recordFeedback(feedbackEvent) {
      const validation = validateOutcomeFeedback(feedbackEvent);
      if (!validation.valid) {
        return false;
      }

      const now = Date.now();
      const recordedTime = typeof feedbackEvent.recordedAt === 'number' ? feedbackEvent.recordedAt : now;
      this.pruneExpired(recordedTime);

      const entry = Object.freeze({
        ...feedbackEvent,
        recordedAt: recordedTime
      });

      // FIFO insertion: newest first
      this.entries.unshift(entry);
      if (this.entries.length > this.maxEntries) {
        this.entries.length = this.maxEntries;
      }
      return true;
    }

    /**
     * Retrieve recent outcome feedback events (newest first)
     * @param {number} [now]
     * @returns {Array<object>}
     */
    getRecentFeedback(now = Date.now()) {
      this.pruneExpired(now);
      return [...this.entries];
    }

    /**
     * Find feedback event by correlationId
     * @param {string} correlationId
     * @param {number} [now]
     * @returns {object|null}
     */
    findByCorrelationId(correlationId, now = Date.now()) {
      if (!correlationId) return null;
      this.pruneExpired(now);
      return this.entries.find((e) => e.correlationId === correlationId) || null;
    }

    /**
     * Clear all recorded feedback
     */
    clear() {
      this.entries = [];
    }

    /**
     * Current count of stored feedback events
     * @param {number} [now]
     * @returns {number}
     */
    size(now = Date.now()) {
      this.pruneExpired(now);
      return this.entries.length;
    }
  }

  const defaultFeedbackStore = new FeedbackStore();

  return {
    FeedbackOutcomeType,
    FeedbackSource,
    UserRating,
    UserRejectionReason,
    generateFeedbackId,
    validateOutcomeFeedback,
    createOutcomeFeedback,
    FeedbackStore,
    defaultFeedbackStore
  };
});
