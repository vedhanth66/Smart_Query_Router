/**
 * Smart Query Router - Routing Policy Configuration
 * 
 * DESIGN PRINCIPLES:
 * - Completely decoupled from the extension UI (popup, options, devtools).
 * - Independent configuration schema with sensible, conservative defaults.
 * - Supports validation, cloning, overrides, and environment switches.
 * - Usable in Service Worker, Content Scripts, and pure Node.js test environments.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterRoutingPolicyConfig = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * Default routing policy configuration
   */
  const DEFAULT_ROUTING_POLICY_CONFIG = Object.freeze({
    version: '1.0.0',
    enabled: true,

    // Enabled target routes
    enabledRoutes: Object.freeze({
      'local-eligible': true,
      'simple-model candidate': true,
      'complex-model candidate': true,
      'needs-evaluation': true
    }),

    // Deterministic rule evaluation priority
    rulePrecedence: Object.freeze([
      'RULE_LOCAL_ELIGIBLE',
      'RULE_CONTEXT_DEPENDENCY_EVALUATION',
      'RULE_COMPLEX_RICH_CONTENT',
      'RULE_COMPLEX_CODE',
      'RULE_COMPLEX_MATH',
      'RULE_COMPLEX_REASONING_CUE',
      'RULE_COMPLEX_COMPARISON_CUE',
      'RULE_COMPLEX_ANALYSIS',
      'RULE_COMPLEX_MULTI_QUESTION',
      'RULE_COMPLEX_STRUCTURED_LIST',
      'RULE_SIMPLE_INQUIRY',
      'RULE_FALLTHROUGH_EVALUATION'
    ]),

    // Signal thresholds (not word count!)
    thresholds: Object.freeze({
      minConfidence: 0.70,
      treatMultipleQuestionsAsComplex: true,
      treatStructuredListsAsComplex: true,
      minRichContentConfidence: 0.90,
      minCodeSyntaxConfidence: 0.90,
      minMathConfidence: 0.85,
      minReasoningCueConfidence: 0.85,
      minComparisonCueConfidence: 0.85
    }),

    // Explainability options
    explainability: Object.freeze({
      includeRuleTrace: true,
      includeMatchedSignals: true,
      verboseExplanations: true
    })
  });

  /**
   * Validates a routing policy configuration object
   * @param {any} config
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateRoutingPolicyConfig(config) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      return { valid: false, error: 'Policy configuration must be a non-null object' };
    }

    if (typeof config.version !== 'string' || !config.version.trim()) {
      return { valid: false, error: 'Policy configuration requires a non-empty string "version"' };
    }

    if (typeof config.enabled !== 'boolean') {
      return { valid: false, error: 'Policy configuration "enabled" must be a boolean' };
    }

    if (!config.enabledRoutes || typeof config.enabledRoutes !== 'object') {
      return { valid: false, error: 'Policy configuration requires an "enabledRoutes" object' };
    }

    const requiredRoutes = ['local-eligible', 'simple-model candidate', 'complex-model candidate', 'needs-evaluation'];
    for (const route of requiredRoutes) {
      if (typeof config.enabledRoutes[route] !== 'boolean') {
        return { valid: false, error: `enabledRoutes["${route}"] must be a boolean` };
      }
    }

    if (!Array.isArray(config.rulePrecedence) || config.rulePrecedence.length === 0) {
      return { valid: false, error: 'Policy configuration "rulePrecedence" must be a non-empty array' };
    }

    if (!config.thresholds || typeof config.thresholds !== 'object') {
      return { valid: false, error: 'Policy configuration requires a "thresholds" object' };
    }

    if (typeof config.thresholds.minConfidence !== 'number' ||
        config.thresholds.minConfidence < 0 ||
        config.thresholds.minConfidence > 1) {
      return { valid: false, error: 'thresholds.minConfidence must be a number between 0.0 and 1.0' };
    }

    return { valid: true };
  }

  /**
   * Creates a valid routing policy configuration by deep-merging user overrides into defaults
   * @param {object} [overrides]
   * @returns {object}
   */
  function createRoutingPolicyConfig(overrides = {}) {
    const base = JSON.parse(JSON.stringify(DEFAULT_ROUTING_POLICY_CONFIG));

    if (!overrides || typeof overrides !== 'object') {
      return base;
    }

    const merged = {
      version: overrides.version || base.version,
      enabled: typeof overrides.enabled === 'boolean' ? overrides.enabled : base.enabled,
      enabledRoutes: Object.assign({}, base.enabledRoutes, overrides.enabledRoutes || {}),
      rulePrecedence: Array.isArray(overrides.rulePrecedence) ? overrides.rulePrecedence.slice() : base.rulePrecedence.slice(),
      thresholds: Object.assign({}, base.thresholds, overrides.thresholds || {}),
      explainability: Object.assign({}, base.explainability, overrides.explainability || {})
    };

    const validation = validateRoutingPolicyConfig(merged);
    if (!validation.valid) {
      throw new Error(`Invalid policy configuration: ${validation.error}`);
    }

    return merged;
  }

  return {
    DEFAULT_ROUTING_POLICY_CONFIG,
    validateRoutingPolicyConfig,
    createRoutingPolicyConfig
  };
});
