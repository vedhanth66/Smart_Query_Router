/**
 * Smart Query Router - Initial Complexity Scorer Configuration
 * 
 * DESIGN PRINCIPLES:
 * - Centralizes all component weights, sub-dimension factors, and score level thresholds
 *   rather than scattering hardcoded constants throughout the codebase.
 * - Non-aggressive, balanced initial defaults: plenty of headroom without brittle cliffs
 *   until real evaluation data exists.
 * - Supports validation, cloning, overrides, and decoupled instantiation across runtimes.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterComplexityScorerConfig = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * Default configuration for ComplexityScorer
   */
  const DEFAULT_COMPLEXITY_SCORER_CONFIG = Object.freeze({
    version: '1.0.0',
    enabled: true,

    // Primary component weights (Must sum to 1.0)
    weights: Object.freeze({
      length: 0.15,
      code: 0.25,
      listStructure: 0.10,
      cues: 0.15,
      contextDependency: 0.15,
      taskType: 0.20
    }),

    // Categorical complexity level thresholds
    thresholds: Object.freeze({
      veryLow: 0.06,  // [0.00, 0.06) -> VERY_LOW
      low: 0.20,      // [0.06, 0.20) -> LOW
      medium: 0.45,   // [0.20, 0.45) -> MEDIUM
      high: 0.70      // [0.45, 0.70) -> HIGH, [0.70, 1.00] -> VERY_HIGH
    }),

    // Length scaling thresholds (measured in word count)
    lengthThresholds: Object.freeze({
      shortWords: 20,       // <= 20 words -> factor 0.1 - 0.3
      mediumWords: 80,      // 21 - 80 words -> factor 0.4 - 0.7
      longWords: 200,       // 81 - 200 words -> factor 0.75 - 0.9
      veryLongWords: 500    // > 200 words -> factor up to 1.0
    }),

    // Code presence relative contribution factors (0.0 to 1.0)
    codeFactors: Object.freeze({
      codeFence: 1.0,
      codeSyntax: 0.85,
      indentedCode: 0.75,
      inlineCodeWithKeywords: 0.65,
      inlineCodeOnly: 0.35,
      keywordsOnly: 0.25
    }),

    // List structure relative contribution factors (0.0 to 1.0)
    listFactors: Object.freeze({
      singleType: 0.6,
      mixedType: 1.0,
      fullScoreItemCount: 5
    }),

    // Reasoning / Comparison cues relative contribution factors (0.0 to 1.0)
    cueFactors: Object.freeze({
      singleCue: 0.6,
      multipleCues: 1.0
    }),

    // Context dependency relative contribution factors (0.0 to 1.0)
    contextFactors: Object.freeze({
      ANAPHORIC: 0.9,
      ANAPHORIC_REFERENCE: 0.9,
      CONTINUATION: 0.8,
      CONTINUATION_COMMAND: 0.8,
      ELLIPTICAL: 0.7,
      FEEDBACK: 0.6,
      UNKNOWN: 0.3,
      STANDALONE: 0.0
    }),

    // Task type relative contribution factors (0.0 to 1.0)
    taskFactors: Object.freeze({
      debugging: 1.0,
      coding: 0.90,
      analysis: 0.85,
      reasoning: 0.80,
      comparison: 0.75,
      'creative writing': 0.55,
      summarization: 0.50,
      rewriting: 0.45,
      translation: 0.40,
      'factual question': 0.35,
      unknown: 0.30,
      arithmetic: 0.15,
      greeting: 0.05
    })
  });

  /**
   * Validates a complexity scorer configuration object
   * @param {any} config
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateComplexityScorerConfig(config) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      return { valid: false, error: 'Config must be a non-null object' };
    }

    if (typeof config.version !== 'string' || !config.version.trim()) {
      return { valid: false, error: 'Config requires a non-empty string "version"' };
    }

    if (typeof config.enabled !== 'boolean') {
      return { valid: false, error: 'Config "enabled" must be a boolean' };
    }

    // Validate weights
    if (!config.weights || typeof config.weights !== 'object') {
      return { valid: false, error: 'Config requires a "weights" object' };
    }

    const requiredWeightKeys = ['length', 'code', 'listStructure', 'cues', 'contextDependency', 'taskType'];
    let weightSum = 0;
    for (const key of requiredWeightKeys) {
      if (typeof config.weights[key] !== 'number' || config.weights[key] < 0) {
        return { valid: false, error: `weights["${key}"] must be a non-negative number` };
      }
      weightSum += config.weights[key];
    }

    // Bounded floating-point tolerance check (must sum close to 1.0)
    if (Math.abs(weightSum - 1.0) > 0.01) {
      return { valid: false, error: `weights must sum to 1.0 (found ${weightSum.toFixed(3)})` };
    }

    // Validate thresholds
    if (!config.thresholds || typeof config.thresholds !== 'object') {
      return { valid: false, error: 'Config requires a "thresholds" object' };
    }

    const { veryLow, low, medium, high } = config.thresholds;
    if (typeof veryLow !== 'number' || typeof low !== 'number' || typeof medium !== 'number' || typeof high !== 'number') {
      return { valid: false, error: 'Thresholds must all be numbers' };
    }

    if (!(veryLow < low && low < medium && medium < high)) {
      return { valid: false, error: 'Thresholds must be strictly ascending: veryLow < low < medium < high' };
    }

    return { valid: true };
  }

  /**
   * Creates a customized configuration by merging overrides onto defaults
   * @param {object} [overrides]
   * @returns {object}
   */
  function createComplexityScorerConfig(overrides = {}) {
    if (!overrides || typeof overrides !== 'object') {
      return JSON.parse(JSON.stringify(DEFAULT_COMPLEXITY_SCORER_CONFIG));
    }

    const merged = {
      version: overrides.version || DEFAULT_COMPLEXITY_SCORER_CONFIG.version,
      enabled: typeof overrides.enabled === 'boolean' ? overrides.enabled : DEFAULT_COMPLEXITY_SCORER_CONFIG.enabled,
      weights: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.weights, ...(overrides.weights || {}) },
      thresholds: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds, ...(overrides.thresholds || {}) },
      lengthThresholds: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.lengthThresholds, ...(overrides.lengthThresholds || {}) },
      codeFactors: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.codeFactors, ...(overrides.codeFactors || {}) },
      listFactors: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.listFactors, ...(overrides.listFactors || {}) },
      cueFactors: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.cueFactors, ...(overrides.cueFactors || {}) },
      contextFactors: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.contextFactors, ...(overrides.contextFactors || {}) },
      taskFactors: { ...DEFAULT_COMPLEXITY_SCORER_CONFIG.taskFactors, ...(overrides.taskFactors || {}) }
    };

    const validation = validateComplexityScorerConfig(merged);
    if (!validation.valid) {
      throw new Error(`Invalid complexity scorer configuration: ${validation.error}`);
    }

    return merged;
  }

  return {
    DEFAULT_COMPLEXITY_SCORER_CONFIG,
    validateComplexityScorerConfig,
    createComplexityScorerConfig
  };
});
