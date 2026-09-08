/**
 * Smart Query Router - Optimizer Metrics & Activity Tracker
 * 
 * CORE RESPONSIBILITIES:
 * 1. Aggregates high-level optimizer performance metrics:
 *    - Enabled state & user routing preference.
 *    - Cache hit rate (percentage and counts).
 *    - Small vs Strong route distribution.
 *    - Estimated cumulative token savings.
 *    - Bounded FIFO ring buffer of recent optimizer activity (max 15 items).
 * 2. STRICT PRIVACY INVARIANT:
 *    - Rejects, strips, and never stores raw prompts, queries, response texts,
 *      session IDs, cookies, or conversational content.
 *    - Only captures operational identifiers, numeric metrics, and timestamps.
 * 3. Persistence:
 *    - Synchronous in-memory access with asynchronous backing to chrome.storage.local.
 * 4. Cross-environment UMD wrapper (Node.js, Service Worker, Content Scripts, Popup).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let userSettingsModule = null;
    try {
      userSettingsModule = require('./user_settings');
    } catch (_) {}
    module.exports = factory(userSettingsModule);
  } else {
    root.SmartQueryRouterMetrics = factory(root.SmartQueryRouterUserSettings);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (userSettingsModule) {
  'use strict';

  const METRICS_STORAGE_KEY = 'smart_query_router_metrics';
  const MAX_RECENT_ACTIVITY = 15;

  // Forbidden keys to guarantee zero raw conversational or sensitive data is ever stored
  const FORBIDDEN_KEY_PATTERN = /^(prompt.*|raw.*|query.*|response.*|content|text|input|cookie.*|session.*|jwt|apikey.*|api_key.*|password|secret|auth.*|access_token|bearer_token|user_token|^token$)$/i;

  /**
   * Machine-readable explainability reason codes
   */
  const RoutingReasonCode = Object.freeze({
    SIMPLE_TASK_SIGNAL: 'SIMPLE_TASK_SIGNAL',
    CONTEXT_DEPENDENCY: 'CONTEXT_DEPENDENCY',
    CACHE_HIT: 'CACHE_HIT',
    ESCALATION: 'ESCALATION',
    LOCAL_RULE_MATCH: 'LOCAL_RULE_MATCH',
    USER_OVERRIDE: 'USER_OVERRIDE',
    RICH_CONTENT_PRESERVATION: 'RICH_CONTENT_PRESERVATION',
    COMPLEX_TASK_SIGNAL: 'COMPLEX_TASK_SIGNAL'
  });

  /**
   * Sanitizes an activity record, strictly allowing only high-level metadata.
   * Discards any conversational or sensitive fields.
   * @param {object} rawRecord
   * @returns {object|null} Sanitized record
   */
  function sanitizeActivityRecord(rawRecord) {
    if (!rawRecord || typeof rawRecord !== 'object') return null;

    // Check for explicit attempt to inject prompt content
    for (const key of Object.keys(rawRecord)) {
      if (FORBIDDEN_KEY_PATTERN.test(key)) {
        // Strip sensitive key
        delete rawRecord[key];
      }
    }

    const id = typeof rawRecord.id === 'string'
      ? rawRecord.id
      : `act_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

    const timestamp = typeof rawRecord.timestamp === 'number'
      ? rawRecord.timestamp
      : Date.now();

    const route = typeof rawRecord.route === 'string'
      ? rawRecord.route
      : 'Unknown';

    const modelTier = typeof rawRecord.modelTier === 'string'
      ? rawRecord.modelTier.toLowerCase()
      : 'simple';

    const cacheOutcome = typeof rawRecord.cacheOutcome === 'string'
      ? rawRecord.cacheOutcome.toUpperCase()
      : 'NOT_CHECKED';

    const tokensSaved = typeof rawRecord.tokensSaved === 'number' && Number.isFinite(rawRecord.tokensSaved)
      ? Math.max(0, Math.round(rawRecord.tokensSaved))
      : 0;

    const latencyMs = typeof rawRecord.latencyMs === 'number' && Number.isFinite(rawRecord.latencyMs)
      ? Math.max(0, Math.round(rawRecord.latencyMs))
      : 0;

    const status = typeof rawRecord.status === 'string'
      ? rawRecord.status.toUpperCase()
      : 'COMPLETED';

    // Optional developer diagnostics (strictly sanitized, zero prompt/conversation text)
    let diagnostics = null;
    if (rawRecord.diagnostics && typeof rawRecord.diagnostics === 'object') {
      const rawDiag = rawRecord.diagnostics;
      for (const k of Object.keys(rawDiag)) {
        if (FORBIDDEN_KEY_PATTERN.test(k)) {
          delete rawDiag[k];
        }
      }

      const reasonCode = typeof rawDiag.reasonCode === 'string'
        ? rawDiag.reasonCode
        : null;

      const reasonExplanation = typeof rawDiag.reasonExplanation === 'string'
        ? rawDiag.reasonExplanation
        : null;

      const taskCategory = typeof rawDiag.taskCategory === 'string'
        ? rawDiag.taskCategory
        : null;

      const signals = Array.isArray(rawDiag.signals)
        ? rawDiag.signals.filter(s => typeof s === 'string' && s.length <= 60).slice(0, 10)
        : [];

      let internalSignals = null;
      if (rawDiag.internalSignals && typeof rawDiag.internalSignals === 'object') {
        const is = rawDiag.internalSignals;
        internalSignals = {
          score: typeof is.score === 'number' && Number.isFinite(is.score) ? Math.round(is.score * 100) / 100 : null,
          level: typeof is.level === 'string' ? is.level : null,
          confidence: typeof is.confidence === 'number' && Number.isFinite(is.confidence) ? Math.round(is.confidence * 100) / 100 : null,
          isSignalOnly: true,
          label: 'Internal Heuristic Signal',
          disclaimer: 'Indicative heuristic signal only; not an objective complexity measure',
          factorBreakdown: (is.factorBreakdown && typeof is.factorBreakdown === 'object')
            ? Object.fromEntries(
                Object.entries(is.factorBreakdown)
                  .filter(([_, v]) => typeof v === 'number' && Number.isFinite(v))
                  .map(([k, v]) => [k, Math.round(v * 100) / 100])
              )
            : null
        };
      }

      let escalationDetails = null;
      if (rawDiag.escalationDetails && typeof rawDiag.escalationDetails === 'object') {
        const esc = rawDiag.escalationDetails;
        escalationDetails = {
          evaluatorId: typeof esc.evaluatorId === 'string' ? esc.evaluatorId : null,
          completeness: typeof esc.completeness === 'number' && Number.isFinite(esc.completeness) ? esc.completeness : null,
          detectedIssues: Array.isArray(esc.detectedIssues)
            ? esc.detectedIssues.filter(i => typeof i === 'string').slice(0, 5)
            : []
        };
      }

      if (reasonCode || reasonExplanation || internalSignals || escalationDetails) {
        diagnostics = {
          reasonCode: reasonCode || RoutingReasonCode.SIMPLE_TASK_SIGNAL,
          reasonExplanation: reasonExplanation || 'Route selected based on autonomous heuristics.',
          taskCategory,
          signals,
          internalSignals,
          escalationDetails
        };
      }
    }

    return {
      id,
      timestamp,
      route,
      modelTier,
      cacheOutcome,
      tokensSaved,
      latencyMs,
      status,
      diagnostics
    };
  }

  /**
   * Tracker managing in-memory metrics and persistence.
   */
  class OptimizerMetricsTracker {
    /**
     * @param {object} [options]
     * @param {object} [options.storage] chrome.storage.local or mock
     * @param {object} [options.userSettingsManager]
     * @param {number} [options.maxRecentActivity]
     */
    constructor(options = {}) {
      this.storage = options.storage || (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) || null;
      this.userSettingsManager = options.userSettingsManager || (userSettingsModule ? userSettingsModule.defaultUserSettingsManager : null);
      this.maxRecentActivity = options.maxRecentActivity || MAX_RECENT_ACTIVITY;

      this.totalQueries = 0;
      this.smallRouteCount = 0;
      this.strongRouteCount = 0;
      this.localRouteCount = 0;
      this.cacheHits = 0;
      this.cacheMisses = 0;
      this.cacheBypasses = 0;
      this.totalTokensSaved = 0;
      this.recentActivity = [];
      this.loaded = false;
    }

    /**
     * Records a high-level optimizer activity event.
     * Sanitizes input, updates aggregate counters, and appends to bounded activity buffer.
     * @param {object} record
     * @returns {object|null} Sanitized record if recorded
     */
    recordActivity(record) {
      const sanitized = sanitizeActivityRecord(record);
      if (!sanitized) return null;

      this.totalQueries += 1;

      // Update route counters
      const tier = sanitized.modelTier.toLowerCase();
      const routeStr = sanitized.route.toLowerCase();

      if (tier === 'strong' || routeStr.includes('strong')) {
        this.strongRouteCount += 1;
      } else if (tier === 'local' || routeStr.includes('rule') || routeStr.includes('local')) {
        this.localRouteCount += 1;
      } else {
        // Default to simple/fast model tier
        this.smallRouteCount += 1;
      }

      // Update cache counters
      if (sanitized.cacheOutcome === 'HIT' || sanitized.cacheOutcome === 'SEMANTIC_HIT') {
        this.cacheHits += 1;
      } else if (sanitized.cacheOutcome === 'MISS' || sanitized.cacheOutcome === 'SEMANTIC_MISS') {
        this.cacheMisses += 1;
      } else if (sanitized.cacheOutcome === 'BYPASS' || sanitized.cacheOutcome === 'SEMANTIC_BYPASS') {
        this.cacheBypasses += 1;
      }

      // Update token savings
      if (sanitized.tokensSaved > 0) {
        this.totalTokensSaved += sanitized.tokensSaved;
      }

      // Append to recent activity ring buffer (newest first)
      this.recentActivity.unshift(sanitized);
      if (this.recentActivity.length > this.maxRecentActivity) {
        this.recentActivity.length = this.maxRecentActivity;
      }

      // Persist asynchronously
      this.persist();

      return sanitized;
    }

    /**
     * Produces a clean, formatted summary object suitable for UI rendering.
     * @returns {object} High-level metrics summary
     */
    getMetricsSummary() {
      const settings = this.userSettingsManager
        ? this.userSettingsManager.getSettings()
        : {
            optimizationEnabled: true,
            backendEnabled: true,
            routingOverride: 'automatic',
            dryRunMode: false
          };

      // 1. Cache hit rate calculation
      const totalEvaluated = this.cacheHits + this.cacheMisses;
      const hitRateRaw = totalEvaluated > 0 ? (this.cacheHits / totalEvaluated) * 100 : 0;
      const cacheHitRateStr = totalEvaluated > 0 ? `${hitRateRaw.toFixed(1)}%` : '0.0%';

      // 2. Route distribution calculation (small vs strong)
      const totalRouted = this.smallRouteCount + this.strongRouteCount + this.localRouteCount;
      const modelRouted = this.smallRouteCount + this.strongRouteCount;
      const smallPct = modelRouted > 0 ? ((this.smallRouteCount / modelRouted) * 100).toFixed(1) + '%' : '0.0%';
      const strongPct = modelRouted > 0 ? ((this.strongRouteCount / modelRouted) * 100).toFixed(1) + '%' : '0.0%';

      return {
        enabledState: {
          optimizationEnabled: Boolean(settings.optimizationEnabled),
          backendEnabled: Boolean(settings.backendEnabled),
          routingOverride: settings.routingOverride || 'automatic',
          dryRunMode: Boolean(settings.dryRunMode)
        },
        cacheHitRate: {
          percentage: cacheHitRateStr,
          rawPercentage: hitRateRaw,
          hits: this.cacheHits,
          misses: this.cacheMisses,
          bypasses: this.cacheBypasses,
          totalEvaluated
        },
        routeDistribution: {
          smallCount: this.smallRouteCount,
          strongCount: this.strongRouteCount,
          localCount: this.localRouteCount,
          totalRouted,
          smallPercentage: smallPct,
          strongPercentage: strongPct
        },
        estimatedTokenSavings: {
          totalTokensSaved: this.totalTokensSaved,
          formatted: this.totalTokensSaved.toLocaleString()
        },
        totalQueries: this.totalQueries,
        recentActivity: [...this.recentActivity]
      };
    }

    /**
     * Persists in-memory metrics to storage.
     * @returns {Promise<void>}
     */
    async persist() {
      if (!this.storage || typeof this.storage.set !== 'function') return;

      const data = {
        totalQueries: this.totalQueries,
        smallRouteCount: this.smallRouteCount,
        strongRouteCount: this.strongRouteCount,
        localRouteCount: this.localRouteCount,
        cacheHits: this.cacheHits,
        cacheMisses: this.cacheMisses,
        cacheBypasses: this.cacheBypasses,
        totalTokensSaved: this.totalTokensSaved,
        recentActivity: this.recentActivity
      };

      return new Promise((resolve) => {
        try {
          this.storage.set({ [METRICS_STORAGE_KEY]: data }, () => resolve());
        } catch (_) {
          resolve();
        }
      });
    }

    /**
     * Loads persisted metrics from storage.
     * @returns {Promise<OptimizerMetricsTracker>}
     */
    async load() {
      if (!this.storage || typeof this.storage.get !== 'function') {
        this.loaded = true;
        return this;
      }

      return new Promise((resolve) => {
        try {
          this.storage.get([METRICS_STORAGE_KEY], (result) => {
            const data = result && result[METRICS_STORAGE_KEY];
            if (data && typeof data === 'object') {
              this.totalQueries = Number(data.totalQueries) || 0;
              this.smallRouteCount = Number(data.smallRouteCount) || 0;
              this.strongRouteCount = Number(data.strongRouteCount) || 0;
              this.localRouteCount = Number(data.localRouteCount) || 0;
              this.cacheHits = Number(data.cacheHits) || 0;
              this.cacheMisses = Number(data.cacheMisses) || 0;
              this.cacheBypasses = Number(data.cacheBypasses) || 0;
              this.totalTokensSaved = Number(data.totalTokensSaved) || 0;
              if (Array.isArray(data.recentActivity)) {
                this.recentActivity = data.recentActivity
                  .map(sanitizeActivityRecord)
                  .filter(Boolean)
                  .slice(0, this.maxRecentActivity);
              }
            }
            this.loaded = true;
            resolve(this);
          });
        } catch (_) {
          this.loaded = true;
          resolve(this);
        }
      });
    }

    /**
     * Resets metrics back to zero.
     * @returns {Promise<void>}
     */
    async reset() {
      this.totalQueries = 0;
      this.smallRouteCount = 0;
      this.strongRouteCount = 0;
      this.localRouteCount = 0;
      this.cacheHits = 0;
      this.cacheMisses = 0;
      this.cacheBypasses = 0;
      this.totalTokensSaved = 0;
      this.recentActivity = [];
      await this.persist();
    }
  }

  // Singleton instance
  const defaultMetricsTracker = new OptimizerMetricsTracker();
  // Attempt immediate async load
  defaultMetricsTracker.load();

  return {
    RoutingReasonCode,
    METRICS_STORAGE_KEY,
    MAX_RECENT_ACTIVITY,
    sanitizeActivityRecord,
    OptimizerMetricsTracker,
    defaultMetricsTracker
  };
});
