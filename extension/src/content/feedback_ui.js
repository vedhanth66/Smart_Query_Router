/**
 * Smart Query Router - Optional Non-Intrusive Feedback UI Controller
 * 
 * CORE RESPONSIBILITIES:
 * 1. Discoverable but not constantly visible: Appears as an unobtrusive, ephemeral pill
 *    when a prompt optimization or model routing event occurs, auto-dismissing after 10s.
 * 2. Allows marking an optimization as helpful (POSITIVE) or unhelpful (NEGATIVE).
 * 3. If unhelpful, provides a short list of reasons to optionally select from.
 * 4. Never interrupts or blocks Claude use; entirely non-modal and dismissible.
 * 5. Can be disabled completely via user settings or directly from the UI.
 * 6. Privacy: Zero conversation, prompt, or response text is captured or displayed.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let outcomeFeedback = null;
    try {
      outcomeFeedback = require('../shared/outcome_feedback');
    } catch (_) {}
    module.exports = factory(outcomeFeedback);
  } else {
    const outcomeFeedback = root.SmartQueryRouterOutcomeFeedback || null;
    root.SmartQueryRouterFeedbackUi = factory(outcomeFeedback);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (outcomeFeedbackDep) {
  'use strict';

  const ROOT_ID = 'smart-query-router-feedback-root';
  const DEFAULT_AUTO_DISMISS_MS = 10000;

  const UiState = Object.freeze({
    HIDDEN: 'HIDDEN',
    PILL: 'PILL',
    EXPANDED: 'EXPANDED',
    REASONS: 'REASONS',
    THANK_YOU: 'THANK_YOU'
  });

  const REASONS_LIST = Object.freeze([
    { id: 'UNWANTED_REWRITE', label: 'Unwanted rewrite' },
    { id: 'INCORRECT_ANSWER', label: 'Incorrect answer' },
    { id: 'HIGH_LATENCY', label: 'High latency' },
    { id: 'PREFER_ORIGINAL', label: 'Prefer original' },
    { id: 'OTHER', label: 'Other' }
  ]);

  class FeedbackUiController {
    /**
     * @param {object} [options]
     * @param {object} [options.userSettingsManager] - UserSettingsManager instance
     * @param {Function} [options.onSubmitFeedback] - Callback ({ correlationId, rating, rejectionReason, notes })
     * @param {object} [options.logger] - DiagnosticLogger instance
     * @param {Document} [options.document] - Target DOM Document (defaults to global window.document)
     * @param {number} [options.autoDismissMs] - Duration in ms before auto-dismissing pill (default: 10000)
     */
    constructor(options = {}) {
      this.userSettingsManager = options.userSettingsManager || null;
      this.onSubmitFeedback = typeof options.onSubmitFeedback === 'function' ? options.onSubmitFeedback : null;
      this.logger = options.logger || null;
      this.doc = options.document || (typeof document !== 'undefined' ? document : null);
      this.autoDismissMs = typeof options.autoDismissMs === 'number' ? options.autoDismissMs : DEFAULT_AUTO_DISMISS_MS;

      this.state = UiState.HIDDEN;
      this.currentCorrelationId = null;
      this.currentRequestId = null;
      this.currentRoutingMetadata = {};
      this.dismissTimer = null;
      this.rootElement = null;
    }

    /**
     * Check whether feedback UI is enabled in settings
     * @returns {boolean}
     */
    isEnabled() {
      if (this.userSettingsManager && typeof this.userSettingsManager.isFeedbackUiEnabled === 'function') {
        return this.userSettingsManager.isFeedbackUiEnabled();
      }
      return true;
    }

    /**
     * Notify controller that an optimization or routing decision was made.
     * Triggers the ephemeral discoverable pill if enabled.
     * 
     * @param {object} params
     * @param {string} params.correlationId
     * @param {string} [params.requestId]
     * @param {object} [params.routingMetadata]
     */
    notifyOptimization({ correlationId, requestId = null, routingMetadata = {} } = {}) {
      if (!this.isEnabled()) {
        return;
      }
      if (!correlationId) {
        return;
      }

      this.currentCorrelationId = correlationId;
      this.currentRequestId = requestId;
      this.currentRoutingMetadata = routingMetadata || {};

      this._showPill();
    }

    /**
     * Completely disables the feedback UI across settings and removes it from the DOM
     */
    disableCompletely() {
      if (this.userSettingsManager && typeof this.userSettingsManager.setFeedbackUiEnabled === 'function') {
        this.userSettingsManager.setFeedbackUiEnabled(false).catch(() => {});
      }
      this.hide();
      this._destroyRoot();
    }

    /**
     * Hide and reset UI state
     */
    hide() {
      this._clearDismissTimer();
      this.state = UiState.HIDDEN;
      if (this.rootElement) {
        this.rootElement.style.display = 'none';
        this.rootElement.innerHTML = '';
      }
    }

    /**
     * Remove root container completely from DOM
     * @private
     */
    _destroyRoot() {
      if (this.rootElement && this.rootElement.parentNode) {
        this.rootElement.parentNode.removeChild(this.rootElement);
      }
      this.rootElement = null;
    }

    /**
     * Get or create container element
     * @private
     */
    _ensureRoot() {
      if (!this.doc) return null;
      let el = this.doc.getElementById(ROOT_ID);
      if (!el && this.doc.body) {
        el = this.doc.createElement('div');
        el.id = ROOT_ID;
        el.setAttribute('data-sqr-component', 'feedback-ui');
        this.doc.body.appendChild(el);
      }
      this.rootElement = el;
      return el;
    }

    /**
     * Clear active dismiss timer
     * @private
     */
    _clearDismissTimer() {
      if (this.dismissTimer) {
        clearTimeout(this.dismissTimer);
        this.dismissTimer = null;
      }
    }

    /**
     * Reset and start auto-dismiss countdown
     * @private
     */
    _startDismissTimer(durationMs) {
      this._clearDismissTimer();
      const delay = durationMs !== undefined ? durationMs : this.autoDismissMs;
      if (delay > 0) {
        this.dismissTimer = setTimeout(() => {
          this.hide();
        }, delay);
      }
    }

    /**
     * Render the subtle discoverable pill
     * @private
     */
    _showPill() {
      const root = this._ensureRoot();
      if (!root) return;

      this.state = UiState.PILL;
      this._clearDismissTimer();

      root.style.display = 'block';
      root.innerHTML = `
        <style>
          #${ROOT_ID} {
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            font-size: 12px;
            line-height: 1.4;
            color: #d4d4d8;
          }
          #${ROOT_ID} * {
            box-sizing: border-box;
          }
          .sqr-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 12px;
            background: rgba(18, 18, 22, 0.95);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 9999px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            user-select: none;
          }
          .sqr-pill:hover {
            background: rgba(28, 28, 34, 0.98);
            border-color: rgba(255, 255, 255, 0.22);
            transform: translateY(-1px);
          }
          .sqr-spark {
            color: #e4e4e7;
            font-size: 8px;
            line-height: 1;
          }
          .sqr-label {
            font-weight: 500;
            color: #f4f4f5;
            font-size: 11.5px;
          }
          .sqr-close {
            background: none;
            border: none;
            color: #71717a;
            cursor: pointer;
            padding: 0 2px;
            font-size: 12px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
            transition: color 0.15s;
          }
          .sqr-close:hover {
            color: #ffffff;
          }
        </style>
        <div class="sqr-pill" id="sqr-pill-trigger">
          <span class="sqr-spark">■</span>
          <span class="sqr-label">Optimization · Feedback?</span>
          <button class="sqr-close" id="sqr-pill-close" title="Dismiss" aria-label="Dismiss">✕</button>
        </div>
      `;

      const trigger = root.querySelector('#sqr-pill-trigger');
      const closeBtn = root.querySelector('#sqr-pill-close');

      if (trigger) {
        trigger.addEventListener('click', (e) => {
          if (e.target === closeBtn || (closeBtn && typeof closeBtn.contains === 'function' && closeBtn.contains(e.target))) {
            return;
          }
          this._showExpanded();
        });
      }

      if (closeBtn) {
        closeBtn.addEventListener('click', (e) => {
          if (e && typeof e.stopPropagation === 'function') {
            e.stopPropagation();
          }
          this.hide();
        });
      }

      // Auto-dismiss after 10 seconds if not interacted with
      this._startDismissTimer(this.autoDismissMs);
    }

    /**
     * Render the compact feedback card
     * @private
     */
    _showExpanded() {
      const root = this._ensureRoot();
      if (!root) return;

      this.state = UiState.EXPANDED;
      this._clearDismissTimer(); // Pause auto-dismiss while user actively interacts

      root.innerHTML = `
        <style>
          #${ROOT_ID} {
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            font-size: 12px;
            line-height: 1.4;
            color: #d4d4d8;
          }
          .sqr-card {
            width: 250px;
            padding: 14px;
            background: rgba(18, 18, 22, 0.96);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.55);
            animation: sqr-fadein 0.15s cubic-bezier(0.16, 1, 0.3, 1);
          }
          @keyframes sqr-fadein {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .sqr-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
          }
          .sqr-title {
            font-weight: 600;
            color: #f4f4f5;
            font-size: 12px;
            letter-spacing: -0.01em;
          }
          .sqr-close-card {
            background: none;
            border: none;
            color: #71717a;
            cursor: pointer;
            padding: 2px 4px;
            font-size: 12px;
            line-height: 1;
            border-radius: 3px;
            transition: color 0.15s ease;
          }
          .sqr-close-card:hover {
            color: #ffffff;
          }
          .sqr-actions {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
          }
          .sqr-btn {
            flex: 1;
            padding: 6px 10px;
            font-size: 11.5px;
            font-weight: 500;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            cursor: pointer;
            transition: all 0.15s ease;
            background: rgba(255, 255, 255, 0.05);
            color: #e4e4e7;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
          }
          .sqr-btn:hover {
            background: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.22);
            color: #ffffff;
          }
          .sqr-btn-helpful:hover {
            background: rgba(255, 255, 255, 0.14);
            border-color: rgba(255, 255, 255, 0.28);
            color: #ffffff;
          }
          .sqr-btn-unhelpful:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.2);
            color: #d4d4d8;
          }
          .sqr-footer {
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            padding-top: 8px;
            text-align: center;
          }
          .sqr-disable-link {
            background: none;
            border: none;
            color: #52525b;
            font-size: 10px;
            cursor: pointer;
            padding: 0;
            transition: color 0.15s ease;
          }
          .sqr-disable-link:hover {
            color: #a1a1aa;
            text-decoration: underline;
          }
        </style>
        <div class="sqr-card">
          <div class="sqr-header">
            <span class="sqr-title">Was this optimization helpful?</span>
            <button class="sqr-close-card" id="sqr-card-close" title="Close" aria-label="Close">✕</button>
          </div>
          <div class="sqr-actions">
            <button class="sqr-btn sqr-btn-helpful" id="sqr-btn-helpful">Helpful</button>
            <button class="sqr-btn sqr-btn-unhelpful" id="sqr-btn-unhelpful">Unhelpful</button>
          </div>
          <div class="sqr-footer">
            <button class="sqr-disable-link" id="sqr-disable-ui">Don't show feedback prompts</button>
          </div>
        </div>
      `;

      const closeBtn = root.querySelector('#sqr-card-close');
      const helpfulBtn = root.querySelector('#sqr-btn-helpful');
      const unhelpfulBtn = root.querySelector('#sqr-btn-unhelpful');
      const disableLink = root.querySelector('#sqr-disable-ui');

      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.hide());
      }

      if (helpfulBtn) {
        helpfulBtn.addEventListener('click', () => {
          this._submitAndShowThanks('POSITIVE');
        });
      }

      if (unhelpfulBtn) {
        unhelpfulBtn.addEventListener('click', () => {
          this._showReasons();
        });
      }

      if (disableLink) {
        disableLink.addEventListener('click', () => {
          this.disableCompletely();
        });
      }
    }

    /**
     * Render the short reason selection list for unhelpful feedback
     * @private
     */
    _showReasons() {
      const root = this._ensureRoot();
      if (!root) return;

      this.state = UiState.REASONS;

      const reasonButtonsHtml = REASONS_LIST.map((r) => `
        <button class="sqr-reason-btn" data-reason="${r.id}">${r.label}</button>
      `).join('');

      root.innerHTML = `
        <style>
          #${ROOT_ID} {
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            font-size: 12px;
            line-height: 1.4;
            color: #d4d4d8;
          }
          .sqr-card {
            width: 250px;
            padding: 14px;
            background: rgba(18, 18, 22, 0.96);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.55);
            animation: sqr-fadein 0.15s cubic-bezier(0.16, 1, 0.3, 1);
          }
          .sqr-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
          }
          .sqr-title {
            font-weight: 600;
            color: #f4f4f5;
            font-size: 12px;
            letter-spacing: -0.01em;
          }
          .sqr-reasons-list {
            display: flex;
            flex-direction: column;
            gap: 5px;
            margin-bottom: 10px;
          }
          .sqr-reason-btn {
            width: 100%;
            text-align: left;
            padding: 6px 10px;
            font-size: 11px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: #d4d4d8;
            cursor: pointer;
            transition: all 0.15s ease;
          }
          .sqr-reason-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.2);
            color: #ffffff;
          }
          .sqr-skip-btn {
            width: 100%;
            padding: 4px;
            font-size: 10.5px;
            background: none;
            border: none;
            color: #71717a;
            cursor: pointer;
            text-align: center;
            transition: color 0.15s ease;
          }
          .sqr-skip-btn:hover {
            color: #d4d4d8;
          }
        </style>
        <div class="sqr-card">
          <div class="sqr-header">
            <span class="sqr-title">What went wrong?</span>
            <button class="sqr-close-card" id="sqr-reason-close" title="Close" aria-label="Close">✕</button>
          </div>
          <div class="sqr-reasons-list" id="sqr-reasons-container">
            ${reasonButtonsHtml}
          </div>
          <button class="sqr-skip-btn" id="sqr-reason-skip">Submit without reason</button>
        </div>
      `;

      const closeBtn = root.querySelector('#sqr-reason-close');
      const skipBtn = root.querySelector('#sqr-reason-skip');
      const container = root.querySelector('#sqr-reasons-container');

      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.hide());
      }

      if (skipBtn) {
        skipBtn.addEventListener('click', () => {
          this._submitAndShowThanks('NEGATIVE', null);
        });
      }

      if (container) {
        container.addEventListener('click', (e) => {
          const target = e.target;
          if (!target) return;
          const btn = (typeof target.closest === 'function')
            ? target.closest('.sqr-reason-btn')
            : ((target.getAttribute && target.getAttribute('data-reason')) ? target : null);
          if (btn && typeof btn.getAttribute === 'function') {
            const reason = btn.getAttribute('data-reason');
            this._submitAndShowThanks('NEGATIVE', reason);
          }
        });
      }
    }

    /**
     * Submit feedback and display quick "Thank you" confirmation before auto-hiding
     * @private
     */
    _submitAndShowThanks(rating, rejectionReason = null) {
      this.state = UiState.THANK_YOU;
      this._clearDismissTimer();

      // Dispatch feedback event via registered callback or global helper
      if (this.onSubmitFeedback) {
        try {
          this.onSubmitFeedback({
            correlationId: this.currentCorrelationId,
            requestId: this.currentRequestId,
            rating,
            rejectionReason,
            routingMetadata: this.currentRoutingMetadata
          });
        } catch (err) {
          if (this.logger) {
            this.logger.debug('FAILURE', 'Feedback submission callback error', { error: err.message });
          }
        }
      }

      const root = this._ensureRoot();
      if (!root) return;

      root.innerHTML = `
        <style>
          .sqr-thanks {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 14px;
            background: rgba(20, 20, 24, 0.96);
            border: 1px solid rgba(255, 255, 255, 0.16);
            color: #f4f4f5;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            font-size: 11.5px;
            font-weight: 500;
            border-radius: 9999px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
            animation: sqr-fadein 0.15s ease-out;
          }
        </style>
        <div class="sqr-thanks">
          <span>✓</span>
          <span>Thank you for your feedback!</span>
        </div>
      `;

      // Auto-hide after 1.5 seconds
      this._startDismissTimer(1500);
    }
  }

  return {
    UiState,
    REASONS_LIST,
    FeedbackUiController
  };
});
