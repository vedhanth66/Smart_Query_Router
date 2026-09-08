/**
 * Smart Query Router - Internal Privacy Configuration Model
 * 
 * Provides a structured, privacy-preserving configuration model for:
 * 1. Telemetry Categories (performance, errors, feature bucketing, raw text opt-in)
 * 2. Diagnostic Logging Categories (query detection, routing decisions, failures, retention TTL)
 * 3. Optimization Categories (local rules, prompt normalization, context pruning, backend routing)
 * 4. Content Retention Bounds (turn count, snippet length, in-memory TTL)
 * 
 * DESIGN PRINCIPLES:
 * - Privacy-preserving by default: zero raw prompt text in telemetry, quiet console, bounded TTLs.
 * - Non-disruptive defaults: core optimization features are fully enabled out of the box.
 * - Decoupled: disabling optional telemetry NEVER disables the core optimizer.
 * - Zero long-term persistence of conversational content.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterPrivacyConfig = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * Default privacy configuration
   */
  const DEFAULT_PRIVACY_CONFIG = Object.freeze({
    version: '1.0.0',

    // Telemetry categories
    telemetry: Object.freeze({
      enabled: true,                   // Master switch for telemetry performance records
      performanceMetrics: true,       // Latency, timestamps, and model decision metadata
      errorMetrics: true,             // Error category and failure reason tracking (health monitoring)
      featureMetrics: true,           // Non-generative structural flags and character bucketing
      allowRawConversationText: false // Strictly false: never transmit raw prompts in telemetry
    }),

    // Diagnostics categories
    diagnostics: Object.freeze({
      enabled: true,                  // In-memory diagnostic ring buffer logging
      logQueryDetection: true,        // Log prompt observation summaries (sanitized)
      logRoutingDecisions: true,      // Log route classification and rule outcomes
      logFailures: true,              // Log network and system failures
      consoleOutput: false,           // Quiet console output in production
      retentionTtlMs: 300_000,        // 5 minutes max in-memory log entry retention
      maxEntries: 50                  // Maximum in-memory log buffer size
    }),

    // Optimization categories
    optimization: Object.freeze({
      localRules: true,               // Fast deterministic rules (greeting, arithmetic, datetime)
      promptNormalization: true,      // Semantics-preserving whitespace/formatting cleanups
      contextPruning: true,           // Bounded recent turns selection and relevance ranking
      backendRouting: true,           // Remote backend optimization and model recommendation
      allowUiSubstitution: true       // Safe UI prompt text substitution when approved
    }),

    // Content retention limits (ensures conversation content is not persisted longer than needed)
    retention: Object.freeze({
      maxTurnHistory: 4,              // Maximum conversational turns stored in memory
      turnSnippetMaxChars: 300,       // Maximum characters per stored turn snippet
      turnRetentionTtlMs: 900_000,    // 15 minutes max retention for in-memory turns
      transientEventTtlMs: 60_000     // 60 seconds max retention for in-memory prompt event references
    })
  });

  /**
   * Validates a privacy configuration object
   * @param {any} config
   * @returns {{ valid: boolean, error?: string }}
   */
  function validatePrivacyConfig(config) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      return { valid: false, error: 'Privacy config must be a non-null object' };
    }

    if (config.telemetry !== undefined) {
      if (typeof config.telemetry !== 'object' || config.telemetry === null || Array.isArray(config.telemetry)) {
        return { valid: false, error: 'telemetry must be an object' };
      }
      const t = config.telemetry;
      if (t.enabled !== undefined && typeof t.enabled !== 'boolean') {
        return { valid: false, error: 'telemetry.enabled must be a boolean' };
      }
      if (t.performanceMetrics !== undefined && typeof t.performanceMetrics !== 'boolean') {
        return { valid: false, error: 'telemetry.performanceMetrics must be a boolean' };
      }
      if (t.errorMetrics !== undefined && typeof t.errorMetrics !== 'boolean') {
        return { valid: false, error: 'telemetry.errorMetrics must be a boolean' };
      }
      if (t.featureMetrics !== undefined && typeof t.featureMetrics !== 'boolean') {
        return { valid: false, error: 'telemetry.featureMetrics must be a boolean' };
      }
      if (t.allowRawConversationText !== undefined && typeof t.allowRawConversationText !== 'boolean') {
        return { valid: false, error: 'telemetry.allowRawConversationText must be a boolean' };
      }
    }

    if (config.diagnostics !== undefined) {
      if (typeof config.diagnostics !== 'object' || config.diagnostics === null || Array.isArray(config.diagnostics)) {
        return { valid: false, error: 'diagnostics must be an object' };
      }
      const d = config.diagnostics;
      if (d.enabled !== undefined && typeof d.enabled !== 'boolean') {
        return { valid: false, error: 'diagnostics.enabled must be a boolean' };
      }
      if (d.logQueryDetection !== undefined && typeof d.logQueryDetection !== 'boolean') {
        return { valid: false, error: 'diagnostics.logQueryDetection must be a boolean' };
      }
      if (d.logRoutingDecisions !== undefined && typeof d.logRoutingDecisions !== 'boolean') {
        return { valid: false, error: 'diagnostics.logRoutingDecisions must be a boolean' };
      }
      if (d.logFailures !== undefined && typeof d.logFailures !== 'boolean') {
        return { valid: false, error: 'diagnostics.logFailures must be a boolean' };
      }
      if (d.consoleOutput !== undefined && typeof d.consoleOutput !== 'boolean') {
        return { valid: false, error: 'diagnostics.consoleOutput must be a boolean' };
      }
      if (d.retentionTtlMs !== undefined && (typeof d.retentionTtlMs !== 'number' || d.retentionTtlMs <= 0)) {
        return { valid: false, error: 'diagnostics.retentionTtlMs must be a positive number' };
      }
      if (d.maxEntries !== undefined && (typeof d.maxEntries !== 'number' || d.maxEntries <= 0)) {
        return { valid: false, error: 'diagnostics.maxEntries must be a positive number' };
      }
    }

    if (config.optimization !== undefined) {
      if (typeof config.optimization !== 'object' || config.optimization === null || Array.isArray(config.optimization)) {
        return { valid: false, error: 'optimization must be an object' };
      }
      const o = config.optimization;
      if (o.localRules !== undefined && typeof o.localRules !== 'boolean') {
        return { valid: false, error: 'optimization.localRules must be a boolean' };
      }
      if (o.promptNormalization !== undefined && typeof o.promptNormalization !== 'boolean') {
        return { valid: false, error: 'optimization.promptNormalization must be a boolean' };
      }
      if (o.contextPruning !== undefined && typeof o.contextPruning !== 'boolean') {
        return { valid: false, error: 'optimization.contextPruning must be a boolean' };
      }
      if (o.backendRouting !== undefined && typeof o.backendRouting !== 'boolean') {
        return { valid: false, error: 'optimization.backendRouting must be a boolean' };
      }
      if (o.allowUiSubstitution !== undefined && typeof o.allowUiSubstitution !== 'boolean') {
        return { valid: false, error: 'optimization.allowUiSubstitution must be a boolean' };
      }
    }

    if (config.retention !== undefined) {
      if (typeof config.retention !== 'object' || config.retention === null || Array.isArray(config.retention)) {
        return { valid: false, error: 'retention must be an object' };
      }
      const r = config.retention;
      if (r.maxTurnHistory !== undefined && (typeof r.maxTurnHistory !== 'number' || r.maxTurnHistory <= 0)) {
        return { valid: false, error: 'retention.maxTurnHistory must be a positive number' };
      }
      if (r.turnSnippetMaxChars !== undefined && (typeof r.turnSnippetMaxChars !== 'number' || r.turnSnippetMaxChars <= 0)) {
        return { valid: false, error: 'retention.turnSnippetMaxChars must be a positive number' };
      }
      if (r.turnRetentionTtlMs !== undefined && (typeof r.turnRetentionTtlMs !== 'number' || r.turnRetentionTtlMs <= 0)) {
        return { valid: false, error: 'retention.turnRetentionTtlMs must be a positive number' };
      }
      if (r.transientEventTtlMs !== undefined && (typeof r.transientEventTtlMs !== 'number' || r.transientEventTtlMs <= 0)) {
        return { valid: false, error: 'retention.transientEventTtlMs must be a positive number' };
      }
    }

    return { valid: true };
  }

  /**
   * Creates a frozen, validated privacy configuration merging defaults with overrides
   * @param {object} [overrides]
   * @returns {Readonly<object>}
   */
  function createPrivacyConfig(overrides = {}) {
    if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) {
      return Object.freeze({ ...DEFAULT_PRIVACY_CONFIG });
    }

    const tOverrides = overrides.telemetry || {};
    const dOverrides = overrides.diagnostics || {};
    const oOverrides = overrides.optimization || {};
    const rOverrides = overrides.retention || {};

    const merged = {
      version: typeof overrides.version === 'string' && overrides.version.trim()
        ? overrides.version.trim()
        : DEFAULT_PRIVACY_CONFIG.version,
      telemetry: Object.freeze({
        enabled: typeof tOverrides.enabled === 'boolean'
          ? tOverrides.enabled
          : DEFAULT_PRIVACY_CONFIG.telemetry.enabled,
        performanceMetrics: typeof tOverrides.performanceMetrics === 'boolean'
          ? tOverrides.performanceMetrics
          : DEFAULT_PRIVACY_CONFIG.telemetry.performanceMetrics,
        errorMetrics: typeof tOverrides.errorMetrics === 'boolean'
          ? tOverrides.errorMetrics
          : DEFAULT_PRIVACY_CONFIG.telemetry.errorMetrics,
        featureMetrics: typeof tOverrides.featureMetrics === 'boolean'
          ? tOverrides.featureMetrics
          : DEFAULT_PRIVACY_CONFIG.telemetry.featureMetrics,
        allowRawConversationText: typeof tOverrides.allowRawConversationText === 'boolean'
          ? tOverrides.allowRawConversationText
          : DEFAULT_PRIVACY_CONFIG.telemetry.allowRawConversationText
      }),
      diagnostics: Object.freeze({
        enabled: typeof dOverrides.enabled === 'boolean'
          ? dOverrides.enabled
          : DEFAULT_PRIVACY_CONFIG.diagnostics.enabled,
        logQueryDetection: typeof dOverrides.logQueryDetection === 'boolean'
          ? dOverrides.logQueryDetection
          : DEFAULT_PRIVACY_CONFIG.diagnostics.logQueryDetection,
        logRoutingDecisions: typeof dOverrides.logRoutingDecisions === 'boolean'
          ? dOverrides.logRoutingDecisions
          : DEFAULT_PRIVACY_CONFIG.diagnostics.logRoutingDecisions,
        logFailures: typeof dOverrides.logFailures === 'boolean'
          ? dOverrides.logFailures
          : DEFAULT_PRIVACY_CONFIG.diagnostics.logFailures,
        consoleOutput: typeof dOverrides.consoleOutput === 'boolean'
          ? dOverrides.consoleOutput
          : DEFAULT_PRIVACY_CONFIG.diagnostics.consoleOutput,
        retentionTtlMs: typeof dOverrides.retentionTtlMs === 'number' && dOverrides.retentionTtlMs > 0
          ? dOverrides.retentionTtlMs
          : DEFAULT_PRIVACY_CONFIG.diagnostics.retentionTtlMs,
        maxEntries: typeof dOverrides.maxEntries === 'number' && dOverrides.maxEntries > 0
          ? dOverrides.maxEntries
          : DEFAULT_PRIVACY_CONFIG.diagnostics.maxEntries
      }),
      optimization: Object.freeze({
        localRules: typeof oOverrides.localRules === 'boolean'
          ? oOverrides.localRules
          : DEFAULT_PRIVACY_CONFIG.optimization.localRules,
        promptNormalization: typeof oOverrides.promptNormalization === 'boolean'
          ? oOverrides.promptNormalization
          : DEFAULT_PRIVACY_CONFIG.optimization.promptNormalization,
        contextPruning: typeof oOverrides.contextPruning === 'boolean'
          ? oOverrides.contextPruning
          : DEFAULT_PRIVACY_CONFIG.optimization.contextPruning,
        backendRouting: typeof oOverrides.backendRouting === 'boolean'
          ? oOverrides.backendRouting
          : DEFAULT_PRIVACY_CONFIG.optimization.backendRouting,
        allowUiSubstitution: typeof oOverrides.allowUiSubstitution === 'boolean'
          ? oOverrides.allowUiSubstitution
          : DEFAULT_PRIVACY_CONFIG.optimization.allowUiSubstitution
      }),
      retention: Object.freeze({
        maxTurnHistory: typeof rOverrides.maxTurnHistory === 'number' && rOverrides.maxTurnHistory > 0
          ? rOverrides.maxTurnHistory
          : DEFAULT_PRIVACY_CONFIG.retention.maxTurnHistory,
        turnSnippetMaxChars: typeof rOverrides.turnSnippetMaxChars === 'number' && rOverrides.turnSnippetMaxChars > 0
          ? rOverrides.turnSnippetMaxChars
          : DEFAULT_PRIVACY_CONFIG.retention.turnSnippetMaxChars,
        turnRetentionTtlMs: typeof rOverrides.turnRetentionTtlMs === 'number' && rOverrides.turnRetentionTtlMs > 0
          ? rOverrides.turnRetentionTtlMs
          : DEFAULT_PRIVACY_CONFIG.retention.turnRetentionTtlMs,
        transientEventTtlMs: typeof rOverrides.transientEventTtlMs === 'number' && rOverrides.transientEventTtlMs > 0
          ? rOverrides.transientEventTtlMs
          : DEFAULT_PRIVACY_CONFIG.retention.transientEventTtlMs
      })
    };

    const validation = validatePrivacyConfig(merged);
    if (!validation.valid) {
      throw new Error(`Invalid privacy configuration: ${validation.error}`);
    }

    return Object.freeze(merged);
  }

  return {
    DEFAULT_PRIVACY_CONFIG,
    validatePrivacyConfig,
    createPrivacyConfig
  };
});
