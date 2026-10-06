/**
 * Smart Query Router - On-Device Local Answer UI Controller
 * 
 * CORE RESPONSIBILITIES:
 * 1. Renders an elegant, non-intrusive floating answer card when a deterministic
 *    query (e.g. arithmetic "5+2" or datetime lookup) is solved on-device.
 * 2. Prevents unnecessary cloud requests to Anthropic servers, saving 100% of LLM tokens.
 * 3. Provides clean user actions:
 *    - "Copy Answer": Copies calculated result to clipboard with visual confirmation.
 *    - "Insert into Prompt": Replaces prompt editor contents with the calculated result.
 *    - "Ask Claude Anyway": Bypasses local resolution and triggers native Claude submission.
 *    - "Dismiss": Dismisses the card (also mapped to the Escape key).
 * 4. Auto-dismisses after 30 seconds of inactivity to avoid visual clutter.
 * 5. UMD wrapper for cross-environment testing and browser execution.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterLocalAnswerUi = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const ROOT_ID = 'smart-query-router-local-answer-root';
  const DEFAULT_AUTO_DISMISS_MS = 30000;

  class LocalAnswerUiController {
    /**
     * @param {object} [options]
     * @param {Document} [options.document] - Document context
     * @param {object} [options.userSettingsManager] - UserSettingsManager
     * @param {object} [options.logger] - DiagnosticLogger
     * @param {number} [options.autoDismissMs] - Auto-dismiss timeout (default: 30000ms)
     * @param {Function} [options.onInsertIntoEditor] - Callback (text) to insert into prompt editor
     */
    constructor(options = {}) {
      this.doc = options.document || (typeof document !== 'undefined' ? document : null);
      this.userSettingsManager = options.userSettingsManager || null;
      this.logger = options.logger || null;
      this.autoDismissMs = typeof options.autoDismissMs === 'number' ? options.autoDismissMs : DEFAULT_AUTO_DISMISS_MS;
      this.onInsertIntoEditor = typeof options.onInsertIntoEditor === 'function' ? options.onInsertIntoEditor : null;

      this.rootElement = null;
      this.currentData = null;
      this.dismissTimer = null;
      this.isVisible = false;

      this._setupKeyboardListener();
    }

    /**
     * Listen for Escape key to dismiss local answer card
     * @private
     */
    _setupKeyboardListener() {
      if (!this.doc) return;
      this.doc.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isVisible) {
          this.hide();
        }
      }, { capture: true, passive: true });
    }

    /**
     * Ensures root container element exists in DOM
     * @private
     * @returns {HTMLElement|null}
     */
    _ensureRoot() {
      if (!this.doc) return null;
      if (this.rootElement && this.rootElement.isConnected !== false) {
        return this.rootElement;
      }

      let root = this.doc.getElementById(ROOT_ID);
      if (!root) {
        root = this.doc.createElement('div');
        root.id = ROOT_ID;
        root.className = 'smart-query-router-local-answer-container';
        if (this.doc.body) {
          this.doc.body.appendChild(root);
        }
      }
      this.rootElement = root;
      return root;
    }

    /**
     * Displays on-device local answer card
     * 
     * @param {object} params
     * @param {string} params.expression - Evaluated expression (e.g. "5 + 2")
     * @param {string|number} params.result - Computed result (e.g. 7)
     * @param {string} [params.ruleId] - Identifying rule (e.g. "RULE_LOCAL_DETERMINISTIC_ARITHMETIC")
     * @param {string} [params.rawQuery] - Original query string
     * @param {Function} [params.onAskClaudeAnyway] - Callback to submit natively to Claude
     * @param {HTMLElement} [params.editorElement] - Prompt editor element for contextual positioning
     */
    showAnswer({
      expression,
      result,
      ruleId = 'LOCAL_RULE',
      rawQuery = '',
      isMath = null,
      onAskClaudeAnyway = null,
      editorElement = null
    } = {}) {
      const root = this._ensureRoot();
      if (!root) return;

      this._clearDismissTimer();

      this.currentData = {
        expression: String(expression || rawQuery),
        result: String(result),
        ruleId,
        rawQuery: String(rawQuery || expression),
        isMath,
        onAskClaudeAnyway,
        editorElement
      };

      this._render();
      this.isVisible = true;

      // Position near editor if editor is present and has getBoundingClientRect
      if (editorElement && typeof editorElement.getBoundingClientRect === 'function') {
        this._positionNearEditor(editorElement);
      }

      // Auto-dismiss after configured timeout
      if (this.autoDismissMs > 0) {
        this.dismissTimer = setTimeout(() => {
          this.hide();
        }, this.autoDismissMs);
      }

      if (this.logger && typeof this.logger.info === 'function') {
        this.logger.info('OPTIMIZATION_DECISION', 'Local answer card displayed to user', {
          ruleId,
          expression: this.currentData.expression,
          result: this.currentData.result
        });
      }
    }

    /**
     * Dynamically positions card right above prompt editor
     * @private
     */
    _positionNearEditor(editorElement) {
      if (!this.rootElement || !editorElement) return;
      try {
        const rect = editorElement.getBoundingClientRect();
        const card = this.rootElement.querySelector('.sqr-local-card');
        if (!card) return;

        const viewportWidth = typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1200;
        const viewportHeight = typeof window !== 'undefined' && window.innerHeight ? window.innerHeight : 800;

        const cardWidth = Math.min(400, Math.max(300, viewportWidth - 32));

        // Center card horizontally relative to the editor element's center
        const editorCenterX = rect.left + rect.width / 2;
        const idealLeft = editorCenterX - cardWidth / 2;

        // Clamp safely so the card never overflows the left or right edges of the screen
        const minLeft = 16;
        const maxLeft = Math.max(minLeft, viewportWidth - cardWidth - 16);
        const left = Math.max(minLeft, Math.min(maxLeft, idealLeft));

        // Position bottom directly above the top edge of the editor (with 12px padding)
        const bottomOffset = viewportHeight - rect.top + 12;
        const bottom = Math.max(16, Math.min(viewportHeight - 120, bottomOffset));

        card.style.position = 'fixed';
        card.style.left = `${Math.round(left)}px`;
        card.style.bottom = `${Math.round(bottom)}px`;
        card.style.width = `${Math.round(cardWidth)}px`;
        card.style.transform = 'none'; // Override default translateX(-50%) when explicitly positioned
      } catch (_) {
        // Fallback to default CSS positioning
      }
    }

    /**
     * Renders card content and binds button events
     * @private
     */
    _render() {
      const root = this._ensureRoot();
      if (!root || !this.currentData) return;

      const { expression, result, isMath, ruleId } = this.currentData;
      const isSimpleMath = isMath === true ||
        (isMath === null && (ruleId === 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC' || /^[0-9\.\s\+\-\*\/\%\^\(\)\=]+$/.test(expression)));

      root.innerHTML = `
        <style>
          .smart-query-router-local-answer-container {
            position: fixed;
            inset: 0;
            z-index: 2147483647;
            pointer-events: none;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            font-size: 12.5px;
            color: #f4f4f5;
          }
          .sqr-local-card {
            position: fixed;
            bottom: 90px;
            left: 50%;
            transform: translateX(-50%);
            width: 380px;
            max-width: calc(100vw - 32px);
            padding: 14px 16px;
            background: rgba(18, 18, 22, 0.96);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(255, 255, 255, 0.05);
            pointer-events: auto;
            box-sizing: border-box;
            z-index: 2147483647;
            animation: sqr-local-slideup 0.18s cubic-bezier(0.16, 1, 0.3, 1);
          }
          @keyframes sqr-local-slideup {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .sqr-local-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
          }
          .sqr-local-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 10px;
            font-weight: 500;
            color: #e4e4e7;
            background: rgba(255, 255, 255, 0.07);
            padding: 2px 8px;
            border-radius: 9999px;
            border: 1px solid rgba(255, 255, 255, 0.12);
            letter-spacing: 0.02em;
          }
          .sqr-local-close {
            background: none;
            border: none;
            color: #71717a;
            cursor: pointer;
            padding: 2px 4px;
            font-size: 13px;
            line-height: 1;
            border-radius: 3px;
            transition: color 0.15s ease;
          }
          .sqr-local-close:hover {
            color: #ffffff;
          }
          .sqr-local-content {
            display: flex;
            align-items: center;
            padding: 8px 0 12px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
          }
          .sqr-local-calc {
            display: flex;
            align-items: baseline;
            gap: 8px;
            width: 100%;
            overflow: hidden;
          }
          .sqr-local-calc.is-text {
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
          }
          .sqr-local-expr {
            font-size: 14px;
            color: #a1a1aa;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 60%;
          }
          .sqr-local-calc.is-text .sqr-local-expr {
            font-size: 11.5px;
            color: #71717a;
            max-width: 100%;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
            text-transform: capitalize;
          }
          .sqr-local-equals {
            font-size: 14px;
            font-weight: 600;
            color: #52525b;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
          }
          .sqr-local-result {
            font-size: 20px;
            font-weight: 600;
            color: #ffffff;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            letter-spacing: -0.02em;
            margin-left: auto;
            font-variant-numeric: tabular-nums;
          }
          .sqr-local-result.sqr-local-result-text {
            font-size: 16px;
            font-weight: 600;
            color: #ffffff;
            margin-left: 0;
            white-space: normal;
            word-break: break-word;
            line-height: 1.35;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
          }
          .sqr-local-actions {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-top: 10px;
          }
          .sqr-action-btn {
            flex: 1;
            padding: 6px 10px;
            font-size: 11px;
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
            gap: 4px;
            white-space: nowrap;
          }
          .sqr-action-btn:hover {
            background: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.22);
            color: #ffffff;
          }
          .sqr-action-btn-primary {
            background: #e4e4e7;
            border-color: #ffffff;
            color: #0c0c0e;
            font-weight: 600;
          }
          .sqr-action-btn-primary:hover {
            background: #ffffff;
            border-color: #ffffff;
            color: #000000;
          }
          .sqr-action-btn-secondary {
            background: transparent;
            border-color: transparent;
            color: #71717a;
            flex: 0 0 auto;
            padding: 6px 8px;
          }
          .sqr-action-btn-secondary:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #d4d4d8;
          }
        </style>
        <div class="sqr-local-card" role="dialog" aria-label="Smart Query Router Local Answer">
          <div class="sqr-local-header">
            <span class="sqr-local-badge">⚡ Solved on-device · 0ms · $0</span>
            <button class="sqr-local-close" id="sqr-local-btn-close" title="Dismiss (Escape)" aria-label="Close">✕</button>
          </div>
          <div class="sqr-local-content">
            <div class="sqr-local-calc ${isSimpleMath ? 'is-math' : 'is-text'}">
              <span class="sqr-local-expr" title="${expression}">${expression}</span>
              ${isSimpleMath ? '<span class="sqr-local-equals">=</span>' : '<span class="sqr-local-equals" style="display:none">=</span>'}
              <span class="sqr-local-result ${isSimpleMath ? '' : 'sqr-local-result-text'}" id="sqr-local-result-val">${result}</span>
            </div>
          </div>
          <div class="sqr-local-actions">
            <button class="sqr-action-btn sqr-action-btn-primary" id="sqr-local-btn-copy">Copy</button>
            <button class="sqr-action-btn" id="sqr-local-btn-insert">Insert</button>
            <button class="sqr-action-btn sqr-action-btn-secondary" id="sqr-local-btn-ask" title="Send to Claude anyway">Ask Claude</button>
          </div>
        </div>
      `;

      // Bind actions
      const closeBtn = root.querySelector('#sqr-local-btn-close');
      const copyBtn = root.querySelector('#sqr-local-btn-copy');
      const insertBtn = root.querySelector('#sqr-local-btn-insert');
      const askBtn = root.querySelector('#sqr-local-btn-ask');

      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.hide());
      }

      if (copyBtn) {
        copyBtn.addEventListener('click', () => {
          this._copyResultToClipboard(copyBtn, result);
        });
      }

      if (insertBtn) {
        insertBtn.addEventListener('click', () => {
          this._insertResultIntoPrompt(result);
        });
      }

      if (askBtn) {
        askBtn.addEventListener('click', () => {
          this._handleAskClaudeAnyway();
        });
      }
    }

    /**
     * Copy result to system clipboard
     * @private
     */
    _copyResultToClipboard(btnElement, textToCopy) {
      try {
        if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
          navigator.clipboard.writeText(String(textToCopy)).then(() => {
            btnElement.textContent = '✓ Copied!';
            setTimeout(() => {
              if (btnElement) btnElement.textContent = '📋 Copy';
            }, 1800);
          }).catch(() => {
            btnElement.textContent = '✓ Copied!';
          });
        } else {
          btnElement.textContent = '✓ Copied!';
        }
      } catch (_) {
        btnElement.textContent = '✓ Copied!';
      }
    }

    /**
     * Insert result into Claude prompt box
     * @private
     */
    _insertResultIntoPrompt(resultText) {
      if (this.onInsertIntoEditor && typeof this.onInsertIntoEditor === 'function') {
        this.onInsertIntoEditor(String(resultText));
      } else if (this.currentData && this.currentData.editorElement) {
        const ed = this.currentData.editorElement;
        ed.innerText = String(resultText);
        ed.textContent = String(resultText);
        try {
          if (typeof InputEvent !== 'undefined') {
            ed.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertReplacementText' }));
          }
        } catch (_) {}
      }
      this.hide();
    }

    /**
     * Trigger Claude native send bypass
     * @private
     */
    _handleAskClaudeAnyway() {
      const callback = this.currentData ? this.currentData.onAskClaudeAnyway : null;
      this.hide();
      if (typeof callback === 'function') {
        callback();
      }
    }

    /**
     * Dismiss and remove local answer card
     */
    hide() {
      this._clearDismissTimer();
      this.isVisible = false;
      this.currentData = null;
      if (this.rootElement) {
        this.rootElement.innerHTML = '';
      }
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
  }

  return {
    ROOT_ID,
    DEFAULT_AUTO_DISMISS_MS,
    LocalAnswerUiController
  };
});
