/**
 * Smart Query Router - In-Page Secondary Status Surface
 * 
 * DESIGN PRINCIPLES:
 * 1. Strictly secondary: Normal Claude remains the primary interface.
 * 2. Unobtrusive: Completely hidden by default, zero interference with typing or streaming.
 * 3. Discoverable: Opened via hotkey (Alt+S) or optional link in the ephemeral feedback pill.
 * 4. Displays high-level optimizer metrics only (zero raw user prompts or conversation content).
 * 5. Easily dismissed via Escape key or close button (✕).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const userSettingsModule = require('../shared/user_settings');
    const optimizerMetricsModule = require('../shared/optimizer_metrics');
    module.exports = factory(userSettingsModule, optimizerMetricsModule);
  } else {
    root.SmartQueryRouterStatusSurface = factory(
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

  const ROOT_ELEMENT_ID = 'smart-query-router-status-surface-root';

  class StatusSurfaceController {
    /**
     * @param {object} [options]
     * @param {Document} [options.document]
     * @param {object} [options.userSettingsManager]
     * @param {object} [options.metricsTracker]
     */
    constructor(options = {}) {
      this.document = options.document || (typeof document !== 'undefined' ? document : null);
      this.userSettingsManager = options.userSettingsManager || userSettingsManager;
      this.metricsTracker = options.metricsTracker || metricsTracker;
      this.isOpen = false;
      this.rootElement = null;

      this._setupKeyboardShortcut();
    }

    /**
     * Set up Alt+S keyboard shortcut to toggle status surface
     * @private
     */
    _setupKeyboardShortcut() {
      if (!this.document) return;

      this.document.addEventListener('keydown', (e) => {
        // Alt + S toggles secondary status drawer
        if (e.altKey && (e.key === 's' || e.key === 'S')) {
          e.preventDefault();
          this.toggle();
        } else if (e.key === 'Escape' && this.isOpen) {
          this.close();
        }
      });
    }

    /**
     * Toggle status surface visibility
     */
    toggle() {
      if (this.isOpen) {
        this.close();
      } else {
        this.open();
      }
    }

    /**
     * Open secondary status surface
     */
    async open() {
      if (!this.document) return;

      if (this.userSettingsManager && typeof this.userSettingsManager.load === 'function') {
        await this.userSettingsManager.load();
      }
      if (this.metricsTracker && typeof this.metricsTracker.load === 'function') {
        await this.metricsTracker.load();
      }

      this._ensureRootElement();
      this.render();
      this.isOpen = true;
      if (this.rootElement) {
        this.rootElement.style.display = 'block';
      }
    }

    /**
     * Close secondary status surface
     */
    close() {
      this.isOpen = false;
      if (this.rootElement) {
        this.rootElement.style.display = 'none';
      }
    }

    /**
     * Ensure container element exists in DOM
     * @private
     */
    _ensureRootElement() {
      if (!this.document) return;

      let root = this.document.getElementById(ROOT_ELEMENT_ID);
      if (!root) {
        root = this.document.createElement('div');
        root.id = ROOT_ELEMENT_ID;
        root.style.position = 'fixed';
        root.style.top = '16px';
        root.style.right = '16px';
        root.style.width = '340px';
        root.style.maxHeight = 'calc(100vh - 32px)';
        root.style.zIndex = '999998';
        root.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        root.style.fontSize = '12.5px';
        root.style.boxShadow = '0 12px 32px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255, 255, 255, 0.1)';
        root.style.borderRadius = '12px';
        root.style.overflow = 'hidden';
        root.style.display = 'none';

        if (this.document.body) {
          this.document.body.appendChild(root);
        }
      }
      this.rootElement = root;
    }

    /**
     * Render the in-page secondary status drawer
     */
    render() {
      if (!this.rootElement) return;

      const summary = this.metricsTracker
        ? this.metricsTracker.getMetricsSummary()
        : {
            enabledState: { optimizationEnabled: true, routingOverride: 'automatic', dryRunMode: false },
            cacheHitRate: { percentage: '0.0%', hits: 0, misses: 0, totalEvaluated: 0 },
            routeDistribution: { smallCount: 0, strongCount: 0, totalRouted: 0, smallPercentage: '50.0%', strongPercentage: '50.0%' },
            estimatedTokenSavings: { totalTokensSaved: 0, formatted: '0' },
            recentActivity: []
          };

      const isOptEnabled = summary.enabledState.optimizationEnabled;
      const isDiagEnabled = this.userSettingsManager && typeof this.userSettingsManager.isDeveloperDiagnosticsEnabled === 'function'
        ? this.userSettingsManager.isDeveloperDiagnosticsEnabled()
        : false;

      const dist = summary.routeDistribution;
      const smallWidth = dist.totalRouted > 0 ? Math.round((dist.smallCount / dist.totalRouted) * 100) : 50;
      const strongWidth = dist.totalRouted > 0 ? (100 - smallWidth) : 50;

      const activities = summary.recentActivity || [];
      let activityRowsHtml = '';

      if (activities.length === 0) {
        activityRowsHtml = '<div style="padding: 14px; text-align: center; color: #71717a; font-size: 11px;">No recent optimizer activity recorded yet.</div>';
      } else {
        for (const item of activities.slice(0, 5)) {
          const reasonBadge = (isDiagEnabled && item.diagnostics && item.diagnostics.reasonCode)
            ? `<span style="background: rgba(129, 140, 248, 0.2); color: #818cf8; padding: 1px 5px; border-radius: 4px; font-size: 9px; font-weight: 600;">${item.diagnostics.reasonCode}</span>`
            : '';
          const savingsBadge = item.tokensSaved > 0 ? `<span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 1px 5px; border-radius: 4px; font-size: 10px;">+${item.tokensSaved} tok</span>` : '';
          const cacheBadge = item.cacheOutcome === 'HIT' ? '<span style="background: rgba(52, 211, 153, 0.15); color: #34d399; padding: 1px 5px; border-radius: 4px; font-size: 10px;">HIT</span>' : '';
          const latencyStr = item.latencyMs ? `<span style="color: #71717a; font-size: 10px;">${item.latencyMs}ms</span>` : '';

          activityRowsHtml += `
            <div style="padding: 6px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); display: flex; justify-content: space-between; align-items: center;">
              <div>
                <div style="font-size: 11px; font-weight: 500; color: #f4f4f5;">${item.route}</div>
                <div style="font-size: 9.5px; color: #71717a;">${new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
              </div>
              <div style="display: flex; gap: 4px; align-items: center;">
                ${reasonBadge}
                ${cacheBadge}
                ${savingsBadge}
                ${latencyStr}
              </div>
            </div>
          `;
        }
      }

      let diagnosticsPanelHtml = '';
      if (isDiagEnabled) {
        const latestAct = activities.length > 0 ? activities[0] : null;
        if (latestAct && latestAct.diagnostics) {
          const d = latestAct.diagnostics;
          const isig = d.internalSignals;
          const esc = d.escalationDetails;

          let factorTagsHtml = '';
          if (isig && isig.factorBreakdown) {
            for (const [k, v] of Object.entries(isig.factorBreakdown)) {
              factorTagsHtml += `<span style="font-size: 9px; padding: 1px 4px; border-radius: 3px; background: rgba(255,255,255,0.05); color: #a1a1aa;">${k}: <strong style="color: #f4f4f5;">${v}</strong></span>`;
            }
          }

          let escalationHtml = '';
          if (esc && (d.reasonCode === 'ESCALATION' || esc.completeness !== null)) {
            const compStr = (esc.completeness !== null && esc.completeness !== undefined) ? `Completeness: ${(esc.completeness * 100).toFixed(0)}%` : '';
            const issuesStr = esc.detectedIssues && esc.detectedIssues.length > 0 ? `Issues: ${esc.detectedIssues.join(', ')}` : '';
            escalationHtml = `
              <div style="margin-top: 6px; padding: 6px 8px; border-radius: 6px; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); font-size: 10px; color: #fca5a5;">
                <div style="font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;">⚠️ Escalation Evaluator Trace</div>
                <div style="margin-top: 2px;">${[compStr, issuesStr].filter(Boolean).join(' · ')}</div>
              </div>
            `;
          }

          diagnosticsPanelHtml = `
            <div style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); background: #1a1a1e; display: flex; flex-direction: column; gap: 6px;">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; gap: 6px; align-items: center;">
                  <span style="font-size: 9.5px; font-weight: 700; padding: 1px 6px; border-radius: 4px; background: rgba(129, 140, 248, 0.25); color: #818cf8; letter-spacing: 0.04em;">${d.reasonCode}</span>
                  <span style="font-size: 9.5px; color: #71717a; text-transform: uppercase;">${d.taskCategory || 'GENERAL'}</span>
                </div>
                <span style="font-size: 10px; color: #a1a1aa; font-style: italic;">${latestAct.route}</span>
              </div>
              <div style="font-size: 11px; color: #e4e4e7; line-height: 1.35;">${d.reasonExplanation}</div>

              <!-- Internal Heuristic Signal Box -->
              <div style="margin-top: 4px; padding: 6px 8px; border-radius: 6px; background: #141416; border: 1px solid rgba(255,255,255,0.06);">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <span style="font-size: 9.5px; font-weight: 600; color: #d4d4d8; text-transform: uppercase; letter-spacing: 0.03em;">🧭 Internal Heuristic Signal</span>
                  <span style="font-size: 9.5px; font-weight: 700; padding: 1px 5px; border-radius: 9999px; background: rgba(56, 189, 248, 0.15); color: #38bdf8;">${isig ? `${isig.level || 'SIGNAL'} (${isig.score !== null && isig.score !== undefined ? isig.score.toFixed(2) : 'N/A'})` : 'N/A'}</span>
                </div>
                <div style="font-size: 9px; color: #71717a; font-style: italic; margin-top: 2px;">Indicative heuristic signal only; not an objective complexity measure.</div>
                ${factorTagsHtml ? `<div style="display: flex; flex-wrap: wrap; gap: 3px; margin-top: 4px;">${factorTagsHtml}</div>` : ''}
              </div>
              ${escalationHtml}
            </div>
          `;
        } else {
          diagnosticsPanelHtml = `
            <div style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); background: #1a1a1e; font-size: 11px; color: #71717a; text-align: center;">
              No developer diagnostics recorded yet for this session.
            </div>
          `;
        }
      }

      this.rootElement.innerHTML = `
        <div style="background: #18181b; color: #f4f4f5; border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column;">
          <!-- Header -->
          <div style="display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.08); background: #141416;">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span style="color: #fbbf24; font-size: 14px;">✦</span>
              <span style="font-weight: 600; font-size: 12.5px;">Optimizer Status</span>
              <span style="font-size: 10px; padding: 1px 6px; border-radius: 999px; background: ${isOptEnabled ? 'rgba(52,211,153,0.15)' : 'rgba(245,158,11,0.15)'}; color: ${isOptEnabled ? '#34d399' : '#fbbf24'}; font-weight: 600;">
                ${isOptEnabled ? 'ACTIVE' : 'PAUSED'}
              </span>
            </div>
            <button id="sqr-surface-close" style="background: none; border: none; color: #9ca3af; font-size: 14px; cursor: pointer; padding: 2px 4px;" title="Close (Esc)">✕</button>
          </div>

          <!-- Controls -->
          <div style="padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.06);">
            <span style="color: #d4d4d8; font-size: 11.5px;">Optimizer State</span>
            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="checkbox" id="sqr-surface-toggle" ${isOptEnabled ? 'checked' : ''} style="cursor: pointer;">
              <span style="font-size: 11px; color: ${isOptEnabled ? '#34d399' : '#9ca3af'};">${isOptEnabled ? 'Enabled' : 'Disabled'}</span>
            </label>
          </div>

          <!-- Developer Diagnostics Toggle -->
          <div style="padding: 8px 14px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.06); background: rgba(129,140,248,0.04);">
            <div>
              <div style="color: #d4d4d8; font-size: 11px; font-weight: 500;">Developer Diagnostics</div>
              <div style="color: #71717a; font-size: 9.5px;">Routing reason codes & signals</div>
            </div>
            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="checkbox" id="sqr-surface-diag-toggle" ${isDiagEnabled ? 'checked' : ''} style="cursor: pointer;">
              <span style="font-size: 10.5px; color: ${isDiagEnabled ? '#818cf8' : '#9ca3af'};">${isDiagEnabled ? 'Shown' : 'Hidden'}</span>
            </label>
          </div>

          <!-- Diagnostics Panel (Conditional) -->
          ${diagnosticsPanelHtml}

          <!-- Metrics Row -->
          <div style="padding: 10px 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.06);">
            <div style="background: #27272a; padding: 8px; border-radius: 8px;">
              <div style="font-size: 10px; color: #a1a1aa; text-transform: uppercase;">Token Savings</div>
              <div style="font-size: 16px; font-weight: 700; color: #38bdf8; margin-top: 2px;">${summary.estimatedTokenSavings.formatted}</div>
            </div>
            <div style="background: #27272a; padding: 8px; border-radius: 8px;">
              <div style="font-size: 10px; color: #a1a1aa; text-transform: uppercase;">Cache Hit Rate</div>
              <div style="font-size: 16px; font-weight: 700; color: #34d399; margin-top: 2px;">${summary.cacheHitRate.percentage}</div>
            </div>
          </div>

          <!-- Route Distribution -->
          <div style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06);">
            <div style="display: flex; justify-content: space-between; font-size: 10.5px; color: #a1a1aa; margin-bottom: 4px;">
              <span>Small vs Strong Distribution</span>
              <span>${dist.totalRouted} queries</span>
            </div>
            <div style="height: 6px; border-radius: 3px; background: #27272a; overflow: hidden; display: flex;">
              <div style="width: ${smallWidth}%; background: #34d399;"></div>
              <div style="width: ${strongWidth}%; background: #818cf8;"></div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 10px; color: #a1a1aa; margin-top: 4px;">
              <span>● Simple: ${dist.smallPercentage} (${dist.smallCount})</span>
              <span>● Strong: ${dist.strongPercentage} (${dist.strongCount})</span>
            </div>
          </div>

          <!-- Recent Activity -->
          <div style="padding: 6px 14px 2px; font-size: 10.5px; font-weight: 600; color: #a1a1aa; text-transform: uppercase;">Recent Activity</div>
          <div style="max-height: 120px; overflow-y: auto;">
            ${activityRowsHtml}
          </div>

          <!-- Footer -->
          <div style="padding: 6px 14px; background: #141416; border-top: 1px solid rgba(255,255,255,0.06); display: flex; justify-content: space-between; align-items: center; font-size: 9.5px; color: #71717a;">
            <span>🔒 Zero prompt/conversation text saved</span>
            <span>Shortcut: Alt+S</span>
          </div>
        </div>
      `;

      // Bind close button
      const closeBtn = this.rootElement.querySelector('#sqr-surface-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.close());
      }

      // Bind toggle checkbox
      const toggleCheck = this.rootElement.querySelector('#sqr-surface-toggle');
      if (toggleCheck) {
        toggleCheck.addEventListener('change', async (e) => {
          const checked = e.target.checked;
          if (this.userSettingsManager) {
            await this.userSettingsManager.setOptimizationEnabled(checked);
            this.render();
          }
        });
      }

      // Bind developer diagnostics toggle checkbox
      const diagToggle = this.rootElement.querySelector('#sqr-surface-diag-toggle');
      if (diagToggle) {
        diagToggle.addEventListener('change', async (e) => {
          const checked = e.target.checked;
          if (this.userSettingsManager && typeof this.userSettingsManager.setDeveloperDiagnosticsEnabled === 'function') {
            await this.userSettingsManager.setDeveloperDiagnosticsEnabled(checked);
            this.render();
          }
        });
      }
    }
  }

  // Singleton instance
  const defaultStatusSurface = new StatusSurfaceController();

  return {
    StatusSurfaceController,
    defaultStatusSurface
  };
});
