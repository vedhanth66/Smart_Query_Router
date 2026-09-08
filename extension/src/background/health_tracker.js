/**
 * Smart Query Router - Background Health Tracker
 * Non-intrusively tracks connection health of Claude tabs.
 * Manages timeouts, graceful recovery on tab navigation/closure, and storage persistence.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const messages = require('../shared/messages');
    module.exports = factory(messages);
  } else {
    const messages = root.SmartQueryRouterMessages;
    root.SmartQueryRouterHealth = factory(messages);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (messages) {
  'use strict';

  const {
    MessageTypes,
    ErrorCodes,
    createHealthCheckMessage
  } = messages;

  const HEALTH_STORAGE_KEY = 'router_health';
  const DEFAULT_HEALTH_TIMEOUT_MS = 600;

  class HealthTracker {
    constructor() {
      // tabId -> { tabId, origin, lastSeen, connected }
      this.tabs = new Map();
      this.startTime = Date.now();
      this.lastAuditTime = Date.now();
      this.dryRunActions = [];
      this.maxDryRunActions = 20;
      this.outcomeFeedback = [];
      this.maxOutcomeFeedback = 50;
    }

    /**
     * Register or update an active Claude tab
     * @param {number} tabId
     * @param {string} origin
     */
    registerTab(tabId, origin) {
      if (typeof tabId !== 'number') return this.getHealthSummary();

      const existing = this.tabs.get(tabId) || {};
      this.tabs.set(tabId, {
        tabId,
        origin: origin || existing.origin || 'unknown',
        lastSeen: Date.now(),
        connected: true
      });

      return this.getHealthSummary();
    }

    /**
     * Unregister a tab when closed
     * @param {number} tabId
     */
    unregisterTab(tabId) {
      this.tabs.delete(tabId);
      return this.getHealthSummary();
    }

    /**
     * Mark a tab as navigating/loading (prior to re-initialization)
     * @param {number} tabId
     */
    markTabNavigating(tabId) {
      const tab = this.tabs.get(tabId);
      if (tab) {
        tab.connected = false;
        tab.lastSeen = Date.now();
      }
      return this.getHealthSummary();
    }

    /**
     * Get snapshot summary of runtime health without exposing private data
     */
    getHealthSummary() {
      const activeTabs = [];
      for (const [tabId, info] of this.tabs.entries()) {
        if (info.connected) {
          activeTabs.push({
            tabId,
            origin: info.origin,
            lastSeen: info.lastSeen,
            connected: info.connected
          });
        }
      }

      return {
        status: activeTabs.length > 0 ? 'HEALTHY' : 'STANDBY',
        serviceWorkerActive: true,
        connectedTabsCount: activeTabs.length,
        activeTabs,
        dryRunActionsCount: this.dryRunActions.length,
        outcomeFeedbackCount: this.outcomeFeedback.length,
        uptimeMs: Date.now() - this.startTime,
        lastCheckedAt: Date.now()
      };
    }

    /**
     * Record an outcome feedback event
     * @param {object} feedback OutcomeFeedback
     */
    recordOutcomeFeedback(feedback) {
      if (!feedback) return;
      this.outcomeFeedback.unshift(feedback);
      if (this.outcomeFeedback.length > this.maxOutcomeFeedback) {
        this.outcomeFeedback.length = this.maxOutcomeFeedback;
      }
    }

    /**
     * Get recent outcome feedback events (most recent first)
     * @returns {Array<object>}
     */
    getRecentOutcomeFeedback() {
      return [...this.outcomeFeedback];
    }

    /**
     * Record a proposed action from dry-run mode
     * @param {object} action ProposedActionRecord
     */
    recordDryRunAction(action) {
      if (!action) return;
      this.dryRunActions.unshift(action);
      if (this.dryRunActions.length > this.maxDryRunActions) {
        this.dryRunActions.length = this.maxDryRunActions;
      }
    }

    /**
     * Get recent dry run actions (most recent first)
     * @returns {Array<object>}
     */
    getRecentDryRunActions() {
      return [...this.dryRunActions];
    }

    /**
     * Persist current health summary to chrome.storage.local for internal diagnostics
     * @param {object} storageApi chrome.storage.local
     */
    async persistHealth(storageApi) {
      if (!storageApi || typeof storageApi.set !== 'function') return;
      const summary = this.getHealthSummary();
      return new Promise((resolve) => {
        storageApi.set({ [HEALTH_STORAGE_KEY]: summary }, () => {
          resolve(summary);
        });
      });
    }

    /**
     * Ping a specific tab with a strict timeout and graceful recovery
     * @param {number} tabId
     * @param {Function} sendTabMessageFn (tabId, message, callback)
     * @param {number} timeoutMs
     * @returns {Promise<{ healthy: boolean, tabId: number, error?: string }>}
     */
    checkTabHealth(tabId, sendTabMessageFn, timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS) {
      return new Promise((resolve) => {
        let timedOut = false;
        const timer = setTimeout(() => {
          timedOut = true;
          // Graceful recovery: mark tab disconnected
          const tab = this.tabs.get(tabId);
          if (tab) {
            tab.connected = false;
          }
          resolve({
            healthy: false,
            tabId,
            error: ErrorCodes.HEALTH_CHECK_TIMEOUT
          });
        }, timeoutMs);

        try {
          const healthMsg = createHealthCheckMessage();
          sendTabMessageFn(tabId, healthMsg, (response) => {
            if (timedOut) return; // Ignore late response
            clearTimeout(timer);

            if (!response || !response.success) {
              // Graceful recovery on missing receiver or tab error
              const tab = this.tabs.get(tabId);
              if (tab) {
                tab.connected = false;
              }
              resolve({
                healthy: false,
                tabId,
                error: ErrorCodes.TAB_DISCONNECTED
              });
              return;
            }

            // Tab responded healthy
            this.registerTab(tabId);
            resolve({
              healthy: true,
              tabId,
              data: response.data
            });
          });
        } catch (err) {
          if (timedOut) return;
          clearTimeout(timer);
          this.unregisterTab(tabId);
          resolve({
            healthy: false,
            tabId,
            error: err.message
          });
        }
      });
    }
  }

  return {
    HealthTracker,
    HEALTH_STORAGE_KEY,
    DEFAULT_HEALTH_TIMEOUT_MS
  };
});
