/**
 * Smart Query Router - Popup Surface Controller
 * 
 * Controls the secondary extension popup:
 * - Displays enabled state and allows toggling optimizer on/off.
 * - Displays and updates routing mode override.
 * - Renders high-level metrics: cache hit rate, small vs strong route distribution,
 *   and estimated token savings.
 * - Renders bounded recent activity list without raw user prompts or conversation content.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const userSettingsModule = require('../shared/user_settings');
    const optimizerMetricsModule = require('../shared/optimizer_metrics');
    module.exports = factory(userSettingsModule, optimizerMetricsModule);
  } else {
    root.SmartQueryRouterPopup = factory(
      root.SmartQueryRouterUserSettings,
      root.SmartQueryRouterMetrics
    );
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (userSettingsModule, optimizerMetricsModule) {
  'use strict';

  const userSettingsManager = userSettingsModule
    ? userSettingsModule.defaultUserSettingsManager
    : null;

  const metricsTracker = optimizerMetricsModule
    ? optimizerMetricsModule.defaultMetricsTracker
    : null;

  /**
   * Format relative timestamp (e.g. "Just now", "2m ago", "1h ago")
   * @param {number} timestamp
   * @returns {string}
   */
  function formatRelativeTime(timestamp) {
    if (!timestamp || typeof timestamp !== 'number') return 'Unknown';
    const diffSec = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (diffSec < 10) return 'Just now';
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${Math.floor(diffHours / 24)}d ago`;
  }

  /**
   * Popup Controller Class
   */
  class PopupController {
    /**
     * @param {object} [options]
     * @param {Document} [options.document] DOM document
     * @param {object} [options.userSettingsManager]
     * @param {object} [options.metricsTracker]
     */
    constructor(options = {}) {
      this.document = options.document || (typeof document !== 'undefined' ? document : null);
      this.userSettingsManager = options.userSettingsManager || userSettingsManager;
      this.metricsTracker = options.metricsTracker || metricsTracker;
      this.initialized = false;
    }

    /**
     * Initialize DOM event listeners and render initial state
     */
    async init() {
      if (!this.document) return;

      this._bindEvents();
      this.render();
      this.initialized = true;

      // Ensure fresh state from storage
      if (this.userSettingsManager && typeof this.userSettingsManager.load === 'function') {
        await this.userSettingsManager.load();
      }
      if (this.metricsTracker && typeof this.metricsTracker.load === 'function') {
        await this.metricsTracker.load();
      }

      this.render();

      // Listen for runtime settings updates
      if (this.userSettingsManager && typeof this.userSettingsManager.addListener === 'function') {
        this.userSettingsManager.addListener(() => {
          this.render();
        });
      }
    }

    /**
     * Bind DOM controls
     * @private
     */
    _bindEvents() {
      const doc = this.document;

      // Toggle Optimizer Switch
      const toggleEl = doc.getElementById('toggle-optimizer');
      if (toggleEl) {
        toggleEl.addEventListener('change', (e) => {
          const isEnabled = (e.target && typeof e.target.checked === 'boolean') ? e.target.checked : false;
          if (this.userSettingsManager) {
            this.userSettingsManager.setOptimizationEnabled(isEnabled);
            this.render();
          }
        });
      }

      // Routing Mode Selector
      const selectEl = doc.getElementById('select-routing-override');
      if (selectEl) {
        selectEl.addEventListener('change', (e) => {
          const override = e.target ? e.target.value : 'automatic';
          if (this.userSettingsManager) {
            this.userSettingsManager.setRoutingOverride(override);
            this.render();
          }
        });
      }

      // Reset Stats Button
      const resetBtn = doc.getElementById('btn-reset-metrics');
      if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
          if (this.metricsTracker) {
            await this.metricsTracker.reset();
            this.render();
          }
        });
      }
    }

    /**
     * Render complete popup view from current settings and metrics
     */
    render() {
      const doc = this.document;
      if (!doc) return;

      const summary = this.metricsTracker
        ? this.metricsTracker.getMetricsSummary()
        : {
            enabledState: { optimizationEnabled: true, routingOverride: 'automatic', dryRunMode: false },
            cacheHitRate: { percentage: '0.0%', hits: 0, misses: 0, totalEvaluated: 0 },
            routeDistribution: { smallCount: 0, strongCount: 0, totalRouted: 0, smallPercentage: '50.0%', strongPercentage: '50.0%' },
            estimatedTokenSavings: { totalTokensSaved: 0, formatted: '0' },
            recentActivity: []
          };

      // 1. Controls & Status Indicator
      const toggleEl = doc.getElementById('toggle-optimizer');
      const selectEl = doc.getElementById('select-routing-override');
      const statusBadge = doc.getElementById('status-indicator');

      const isOptEnabled = summary.enabledState.optimizationEnabled;
      const isDryRun = summary.enabledState.dryRunMode;

      if (toggleEl) {
        toggleEl.checked = isOptEnabled;
      }
      if (selectEl) {
        selectEl.value = summary.enabledState.routingOverride || 'automatic';
      }

      if (statusBadge) {
        statusBadge.className = 'sqr-status-badge';
        if (!isOptEnabled) {
          statusBadge.textContent = 'Paused';
          statusBadge.classList.add('paused');
        } else if (isDryRun) {
          statusBadge.textContent = 'Dry Run';
          statusBadge.classList.add('dry-run');
        } else {
          statusBadge.textContent = 'Active';
        }
      }

      // 2. Token Savings
      const tokensSavedEl = doc.getElementById('stat-tokens-saved');
      if (tokensSavedEl) {
        tokensSavedEl.textContent = summary.estimatedTokenSavings.formatted || '0';
      }

      // 3. Cache Hit Rate
      const cacheHitEl = doc.getElementById('stat-cache-hit-rate');
      const cacheDetailEl = doc.getElementById('stat-cache-detail');
      if (cacheHitEl) {
        cacheHitEl.textContent = summary.cacheHitRate.percentage;
      }
      if (cacheDetailEl) {
        cacheDetailEl.textContent = `${summary.cacheHitRate.hits} hits / ${summary.cacheHitRate.totalEvaluated} checks`;
      }

      // 4. Route Distribution (Small vs Strong)
      const totalRoutedEl = doc.getElementById('stat-total-routed');
      const barSmall = doc.getElementById('bar-small');
      const barStrong = doc.getElementById('bar-strong');
      const labelSmall = doc.getElementById('label-small');
      const labelStrong = doc.getElementById('label-strong');

      const dist = summary.routeDistribution;
      if (totalRoutedEl) {
        totalRoutedEl.textContent = `${dist.totalRouted} queries`;
      }

      const smallWidth = dist.totalRouted > 0
        ? Math.round((dist.smallCount / dist.totalRouted) * 100)
        : 50;
      const strongWidth = dist.totalRouted > 0
        ? (100 - smallWidth)
        : 50;

      if (barSmall) barSmall.style.width = `${smallWidth}%`;
      if (barStrong) barStrong.style.width = `${strongWidth}%`;

      if (labelSmall) {
        labelSmall.innerHTML = `<span class="sqr-dot sqr-dot-small"></span> Simple: <strong>${dist.smallPercentage}</strong> (${dist.smallCount})`;
      }
      if (labelStrong) {
        labelStrong.innerHTML = `<span class="sqr-dot sqr-dot-strong"></span> Strong: <strong>${dist.strongPercentage}</strong> (${dist.strongCount})`;
      }

      // 5. Recent Activity List
      const activityCountEl = doc.getElementById('activity-count');
      const emptyEl = doc.getElementById('activity-empty');
      const listEl = doc.getElementById('activity-list');

      const activities = summary.recentActivity || [];
      if (activityCountEl) {
        activityCountEl.textContent = `${activities.length} items`;
      }

      if (activities.length === 0) {
        if (emptyEl) emptyEl.style.display = 'flex';
        if (listEl) listEl.style.display = 'none';
      } else {
        if (emptyEl) emptyEl.style.display = 'none';
        if (listEl) {
          listEl.style.display = 'block';
          listEl.innerHTML = '';

          for (const item of activities) {
            const li = doc.createElement('li');
            li.className = 'sqr-activity-item';

            const timeStr = formatRelativeTime(item.timestamp);
            const routeName = item.route || 'Model Routing';
            const latencyStr = item.latencyMs ? `${item.latencyMs}ms` : '';
            const savingsStr = item.tokensSaved > 0 ? `+${item.tokensSaved} tok` : '';
            const cacheStr = item.cacheOutcome === 'HIT' ? 'HIT' : (item.cacheOutcome === 'MISS' ? 'MISS' : null);

            let badgesHtml = '';
            if (cacheStr) {
              badgesHtml += `<span class="sqr-badge sqr-badge-${cacheStr.toLowerCase()}">${cacheStr}</span>`;
            }
            if (savingsStr) {
              badgesHtml += `<span class="sqr-badge sqr-badge-savings">${savingsStr}</span>`;
            }
            if (latencyStr) {
              badgesHtml += `<span class="sqr-badge-latency">${latencyStr}</span>`;
            }

            li.innerHTML = `
              <div class="sqr-activity-meta">
                <span class="sqr-activity-route">${routeName}</span>
                <span class="sqr-activity-time">${timeStr}</span>
              </div>
              <div class="sqr-activity-badges">
                ${badgesHtml}
              </div>
            `;
            listEl.appendChild(li);
          }
        }
      }
    }
  }

  // Auto-init in browser window context
  if (typeof document !== 'undefined' && document.getElementById('toggle-optimizer')) {
    const popup = new PopupController({ document });
    document.addEventListener('DOMContentLoaded', () => {
      popup.init();
    });
  }

  return {
    formatRelativeTime,
    PopupController
  };
});
