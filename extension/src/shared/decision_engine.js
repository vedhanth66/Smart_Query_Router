/**
 * Smart Query Router - Optimization Decision Interface
 * Defines explicit decision outcomes, decision schema, and the extensible decision engine interface.
 * Establishes a stable interface without implementing active optimization rules yet.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterDecision = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Explicit decision outcomes
  const DecisionOutcome = Object.freeze({
    LOCAL_ANSWER_CANDIDATE: 'LOCAL_ANSWER_CANDIDATE',
    CACHE_CANDIDATE: 'CACHE_CANDIDATE',
    BACKEND_CANDIDATE: 'BACKEND_CANDIDATE',
    NO_OPTIMIZATION: 'NO_OPTIMIZATION'
  });

  // Default baseline rule IDs
  const BaseRuleIds = Object.freeze({
    DEFAULT_PASS_THROUGH: 'RULE_DEFAULT_PASS_THROUGH',
    SENSITIVE_CONTENT_BYPASS: 'RULE_SENSITIVE_CONTENT_BYPASS',
    EMPTY_PROMPT_BYPASS: 'RULE_EMPTY_PROMPT_BYPASS'
  });

  /**
   * Factory function to create a standardized OptimizationDecision
   * @param {object} params
   * @param {string} params.outcome - One of DecisionOutcome
   * @param {string} params.ruleId - Machine-readable rule identifier (e.g. 'RULE_DEFAULT_PASS_THROUGH')
   * @param {string} params.reason - Human-readable internal explanation
   * @param {number} [params.confidence] - Confidence score [0.0 - 1.0]
   * @param {object} [params.metadata] - Optional non-sensitive rule metadata
   * @returns {object} OptimizationDecision
   */
  function createOptimizationDecision({ outcome, ruleId, reason, confidence = 1.0, metadata = {} }) {
    const validOutcomes = Object.values(DecisionOutcome);
    if (!validOutcomes.includes(outcome)) {
      throw new Error(`Invalid decision outcome: "${outcome}". Allowed: ${validOutcomes.join(', ')}`);
    }

    if (typeof ruleId !== 'string' || !ruleId.trim()) {
      throw new Error('OptimizationDecision requires a non-empty string "ruleId"');
    }

    if (typeof reason !== 'string' || !reason.trim()) {
      throw new Error('OptimizationDecision requires a non-empty string "reason"');
    }

    return {
      outcome,
      ruleId: ruleId.trim(),
      reason: reason.trim(),
      confidence: typeof confidence === 'number' ? Math.max(0, Math.min(1, confidence)) : 1.0,
      timestamp: Date.now(),
      metadata: metadata && typeof metadata === 'object' ? { ...metadata } : {}
    };
  }

  /**
   * Validates that an object conforms to the OptimizationDecision schema
   * @param {any} decision
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateOptimizationDecision(decision) {
    if (!decision || typeof decision !== 'object' || Array.isArray(decision)) {
      return { valid: false, error: 'Decision must be a non-null object' };
    }

    const validOutcomes = Object.values(DecisionOutcome);
    if (!validOutcomes.includes(decision.outcome)) {
      return {
        valid: false,
        error: `Unknown decision outcome: "${decision.outcome}". Expected one of: ${validOutcomes.join(', ')}`
      };
    }

    if (typeof decision.ruleId !== 'string' || !decision.ruleId.trim()) {
      return { valid: false, error: 'Decision requires a non-empty string ruleId' };
    }

    if (typeof decision.reason !== 'string' || !decision.reason.trim()) {
      return { valid: false, error: 'Decision requires a non-empty string reason' };
    }

    if (typeof decision.timestamp !== 'number' || !Number.isFinite(decision.timestamp)) {
      return { valid: false, error: 'Decision requires a numeric timestamp' };
    }

    return { valid: true };
  }

  /**
   * Optimization Decision Engine
   * Evaluates query events against registered rules.
   * Defaults to NO_OPTIMIZATION baseline until specific rules are registered.
   */
  class OptimizationDecisionEngine {
    constructor() {
      // Pluggable rule handlers: array of { id, evaluate: (event) => decision | null }
      this.rules = [];
    }

    /**
     * Register a new decision rule plugin
     * @param {object} rule
     * @param {string} rule.id - Unique rule identifier
     * @param {Function} rule.evaluate - (queryEvent) => OptimizationDecision | null
     */
    registerRule(rule) {
      if (!rule || typeof rule.id !== 'string' || typeof rule.evaluate !== 'function') {
        throw new Error('Rule must provide string "id" and function "evaluate"');
      }
      this.rules.push(rule);
    }

    /**
     * Evaluate a DetectedQueryEvent to produce an OptimizationDecision.
     * Evaluates registered rules in order; defaults to baseline NO_OPTIMIZATION.
     * @param {object} queryEvent
     * @returns {object} OptimizationDecision
     */
    evaluate(queryEvent) {
      // Execute any registered rules
      for (const rule of this.rules) {
        try {
          const decision = rule.evaluate(queryEvent);
          if (decision) {
            const validation = validateOptimizationDecision(decision);
            if (validation.valid) {
              return decision;
            }
          }
        } catch (err) {
          // Rule execution failure: continue to next rule or fallback
        }
      }

      // Default baseline decision: NO_OPTIMIZATION
      return createOptimizationDecision({
        outcome: DecisionOutcome.NO_OPTIMIZATION,
        ruleId: BaseRuleIds.DEFAULT_PASS_THROUGH,
        reason: 'Default baseline decision: pass-through query without modification until active optimization rules are enabled.',
        confidence: 1.0,
        metadata: {
          policy: 'BASELINE_PASS_THROUGH',
          registeredRulesCount: this.rules.length
        }
      });
    }

    /**
     * Helper to classify an event using a DeterministicRoutingPolicy
     * @param {object} queryEvent
     * @param {object} [routingPolicy]
     * @returns {object|null} RoutingClassification
     */
    classifyRouting(queryEvent, routingPolicy = null) {
      const policy = routingPolicy ||
        (typeof globalThis !== 'undefined' && globalThis.SmartQueryRouterRoutingPolicy
          ? globalThis.SmartQueryRouterRoutingPolicy.defaultRoutingPolicy
          : null);
      if (policy && typeof policy.classify === 'function') {
        return policy.classify(queryEvent);
      }
      return null;
    }

    /**
     * Reset registered rules
     */
    reset() {
      this.rules = [];
    }
  }

  // Default singleton instance
  const defaultDecisionEngine = new OptimizationDecisionEngine();

  return {
    DecisionOutcome,
    BaseRuleIds,
    createOptimizationDecision,
    validateOptimizationDecision,
    OptimizationDecisionEngine,
    defaultDecisionEngine
  };
});
