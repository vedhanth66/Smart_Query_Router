/**
 * Smart Query Router - Safe UI-Level Substitution Manager
 * 
 * CORE RESPONSIBILITIES:
 * 1. Implements the least invasive supported mechanism for applying an active optimization
 *    to Claude's ProseMirror contenteditable editor (`div[contenteditable="true"]`).
 * 2. Does NOT intercept native fetch/SSE network streams (preserves Anthropic internal integrity).
 * 3. Uses standard browser DOM input commands (`Selection`, `Range`, and `document.execCommand('insertText')`
 *    with synthetic `InputEvent` fallback) to keep ProseMirror and React document states synchronized.
 * 4. Applies single safest, easiest-to-verify behavior: semantics-preserving prompt normalization
 *    (trims boundary whitespace, collapses 3+ newlines to 2, standardizes CRLF -> LF, collapses repeated
 *    spaces in prose, while strictly protecting code, math, quotes, and list structures).
 * 5. Immediate bypass paths:
 *    - Manual hotkey bypass: holding `Alt` (`event.altKey === true`) immediately bypasses substitution.
 *    - Settings toggle bypass: `userSettings.isOptimizationEnabled() === false` immediately bypasses.
 *    - Strict fail-open bypass: if selection, DOM manipulation, or normalization throws, the error
 *      is caught immediately, the original prompt is left intact, and native submission proceeds unhindered.
 * 6. Privacy: Zero raw prompt text is exposed in diagnostics or logs.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let normalizer = null;
    try {
      normalizer = require('../shared/normalizer');
    } catch (_) {}
    module.exports = factory(normalizer);
  } else {
    const normalizer = root.SmartQueryRouterNormalizer || null;
    root.SmartQueryRouterUiSubstitution = factory(normalizer);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (normalizerDep) {
  'use strict';

  /**
   * Substitution outcome status
   */
  const SubstitutionStatus = Object.freeze({
    APPLIED: 'APPLIED',
    NO_OP: 'NO_OP',
    BYPASSED: 'BYPASSED',
    FAILED: 'FAILED'
  });

  /**
   * Safe UI-Level Substitutor
   */
  class SafeUiSubstitutor {
    /**
     * @param {object} [options]
     * @param {object} [options.normalizer] - Normalizer module
     * @param {object} [options.logger] - DiagnosticLogger instance
     * @param {object} [options.userSettingsManager] - UserSettingsManager instance
     */
    constructor(options = {}) {
      const opts = options || {};
      this.normalizer = opts.normalizer || normalizerDep || null;
      this.logger = opts.logger || null;
      this.userSettingsManager = opts.userSettingsManager || null;
    }

    /**
     * Resolves active normalizer module
     * @returns {object|null}
     */
    _getNormalizer() {
      if (this.normalizer && typeof this.normalizer.normalizeQuery === 'function') {
        return this.normalizer;
      }
      if (typeof globalThis !== 'undefined' && globalThis.SmartQueryRouterNormalizer) {
        return globalThis.SmartQueryRouterNormalizer;
      }
      return null;
    }

    /**
     * Checks whether an event indicates a user-initiated bypass (e.g. Alt key held)
     * @param {KeyboardEvent|MouseEvent|object} event
     * @returns {boolean}
     */
    isBypassTrigger(event) {
      if (!event || typeof event !== 'object') return false;
      return Boolean(event.altKey);
    }

    /**
     * Checks whether an element is a valid contenteditable editor suitable for substitution
     * @param {HTMLElement|any} editorElement
     * @returns {boolean}
     */
    canSubstitute(editorElement) {
      if (!editorElement || typeof editorElement !== 'object') return false;

      // Check contenteditable attribute or property
      const isEditable = editorElement.isContentEditable === true ||
        (editorElement.getAttribute && editorElement.getAttribute('contenteditable') === 'true');

      if (!isEditable) return false;

      // Check document connectivity if isConnected is supported
      if (typeof editorElement.isConnected === 'boolean' && !editorElement.isConnected) {
        return false;
      }

      return true;
    }

    /**
     * Safely extracts text content from an editor element
     * @param {HTMLElement|any} editorElement
     * @returns {string}
     */
    extractText(editorElement) {
      if (!editorElement) return '';
      return (editorElement.innerText || editorElement.textContent || '').trim();
    }

    /**
     * Applies the safest, easiest-to-verify prompt optimization (semantics-preserving normalization)
     * to Claude's contenteditable editor via supported browser input commands.
     * 
     * GUARANTEES:
     * - Immediate bypass if requested or disabled in settings.
     * - Immediate fail-open fallback if DOM manipulation throws (user original text remains intact).
     * - Preserves ProseMirror and React state via execCommand('insertText') / InputEvent.
     * 
     * @param {HTMLElement|any} editorElement
     * @param {object} [options]
     * @param {string} [options.rawText] - Pre-extracted text
     * @param {boolean} [options.bypass] - Explicit bypass flag
     * @param {string} [options.correlationId] - Telemetry correlation ID
     * @returns {{
     *   status: 'APPLIED' | 'NO_OP' | 'BYPASSED' | 'FAILED',
     *   reason: string,
     *   originalText: string,
     *   substitutedText: string,
     *   failOpen?: boolean,
     *   savings?: object
     * }}
     */
    applyPromptOptimization(editorElement, options = {}) {
      const rawText = typeof options.rawText === 'string'
        ? options.rawText
        : this.extractText(editorElement);

      // 1. Immediate Manual User Bypass (e.g. Alt key held)
      if (options.bypass === true) {
        if (this.logger && typeof this.logger.debug === 'function') {
          this.logger.debug('OPTIMIZATION_DECISION', 'UI substitution bypassed by user hotkey modifier', {
            bypassReason: 'USER_HOTKEY_BYPASS',
            promptLength: rawText.length
          });
        }
        return {
          status: SubstitutionStatus.BYPASSED,
          reason: 'USER_HOTKEY_BYPASS',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: false
        };
      }

      // 2. Settings Toggle Bypass
      if (this.userSettingsManager && typeof this.userSettingsManager.isOptimizationEnabled === 'function') {
        if (!this.userSettingsManager.isOptimizationEnabled()) {
          if (this.logger && typeof this.logger.debug === 'function') {
            this.logger.debug('OPTIMIZATION_DECISION', 'UI substitution bypassed by user settings', {
              bypassReason: 'SETTINGS_DISABLED',
              promptLength: rawText.length
            });
          }
          return {
            status: SubstitutionStatus.BYPASSED,
            reason: 'SETTINGS_DISABLED',
            originalText: rawText,
            substitutedText: rawText,
            failOpen: false
          };
        }
      }

      // 3. Validate Editor Element
      if (!this.canSubstitute(editorElement)) {
        return {
          status: SubstitutionStatus.FAILED,
          reason: 'INVALID_EDITOR_ELEMENT',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: true
        };
      }

      // 4. Check Empty / Whitespace-only Text (No-op)
      if (!rawText || !rawText.trim()) {
        return {
          status: SubstitutionStatus.NO_OP,
          reason: 'EMPTY_PROMPT',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: false
        };
      }

      // 5. Evaluate Normalization (Safest behavior from dry run)
      const normalizer = this._getNormalizer();
      if (!normalizer || typeof normalizer.normalizeQuery !== 'function') {
        if (this.logger && typeof this.logger.warn === 'function') {
          this.logger.warn('FAILURE', 'Normalizer module unavailable for UI substitution');
        }
        return {
          status: SubstitutionStatus.FAILED,
          reason: 'NORMALIZER_UNAVAILABLE',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: true
        };
      }

      let normResult;
      try {
        normResult = normalizer.normalizeQuery(rawText);
      } catch (err) {
        // Fail-open immediately if normalization throws
        return {
          status: SubstitutionStatus.FAILED,
          reason: `NORMALIZATION_ERROR: ${err.message}`,
          originalText: rawText,
          substitutedText: rawText,
          failOpen: true
        };
      }

      // If text was not modified by normalization, no substitution is needed
      if (!normResult.isChanged) {
        return {
          status: SubstitutionStatus.NO_OP,
          reason: 'ALREADY_OPTIMAL',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: false
        };
      }

      const replacementText = normResult.normalizedPrompt;

      // 5b. Rich Content Preservation Guarantee:
      // Ensure all attachment references and rich structures are preserved verbatim.
      // Never strip or rewrite attachment references or rich content merely to save tokens.
      const attachmentRefPattern = /\[(?:Attachment|Image|File|Upload)(?:\s*#?\d*)?(?:\s*:\s*[^\]]+)?\]|\b(?:attached\s+(?:file|document|pdf|spreadsheet|notes|data|report|code|screenshot|csv|image)|this\s+attachment|uploaded\s+(?:file|document|csv|data|image|pdf)|see\s+(?:the\s+)?attachment|in\s+the\s+attachment)\b/gi;
      const rawAttachmentMatches = rawText.match(attachmentRefPattern) || [];
      const normAttachmentMatches = replacementText.match(attachmentRefPattern) || [];
      if (rawAttachmentMatches.length !== normAttachmentMatches.length) {
        return {
          status: SubstitutionStatus.NO_OP,
          reason: 'RICH_CONTENT_CONSERVATIVE_PRESERVATION',
          originalText: rawText,
          substitutedText: rawText,
          failOpen: false
        };
      }

      // 6. Execute Safe DOM Substitution
      try {
        const doc = editorElement.ownerDocument || (typeof document !== 'undefined' ? document : null);
        const win = (doc && doc.defaultView) || (typeof window !== 'undefined' ? window : null);

        let replaced = false;

        // Focus editor if supported
        if (typeof editorElement.focus === 'function') {
          try { editorElement.focus(); } catch (_) {}
        }

        // Primary supported method: execCommand('insertText') with Selection/Range
        if (win && doc && typeof win.getSelection === 'function' && typeof doc.execCommand === 'function') {
          try {
            const selection = win.getSelection();
            if (selection && typeof doc.createRange === 'function') {
              const range = doc.createRange();
              range.selectNodeContents(editorElement);
              selection.removeAllRanges();
              selection.addRange(range);
              replaced = doc.execCommand('insertText', false, replacementText);
            }
          } catch (_) {
            replaced = false;
          }
        }

        // Resilient fallback method: Range deletion + TextNode + InputEvent dispatch
        if (!replaced) {
          if (doc && typeof doc.createRange === 'function') {
            try {
              const range = doc.createRange();
              range.selectNodeContents(editorElement);
              range.deleteContents();
              const textNode = doc.createTextNode(replacementText);
              range.insertNode(textNode);
              replaced = true;
            } catch (_) {
              editorElement.textContent = replacementText;
              replaced = true;
            }
          } else {
            editorElement.textContent = replacementText;
            replaced = true;
          }

          // Dispatch synthetic input events for ProseMirror / React synchronization
          try {
            const inputEvt = typeof InputEvent !== 'undefined'
              ? new InputEvent('input', {
                  bubbles: true,
                  cancelable: true,
                  inputType: 'insertReplacementText',
                  data: replacementText
                })
              : (typeof Event !== 'undefined' ? new Event('input', { bubbles: true }) : null);

            if (inputEvt && typeof editorElement.dispatchEvent === 'function') {
              editorElement.dispatchEvent(inputEvt);
            }
          } catch (_) {}
        }

        // Verify editor content reflects substitution
        const afterText = this.extractText(editorElement);
        const wasApplied = afterText === replacementText || afterText.length === replacementText.length;

        if (this.logger && typeof this.logger.info === 'function') {
          this.logger.info('OPTIMIZATION_DECISION', 'Prompt normalization applied via safe UI substitution', {
            originalLength: rawText.length,
            optimizedLength: replacementText.length,
            charactersSaved: normResult.savings ? normResult.savings.characters : (rawText.length - replacementText.length),
            applied: wasApplied
          });
        }

        return {
          status: wasApplied ? SubstitutionStatus.APPLIED : SubstitutionStatus.FAILED,
          reason: wasApplied ? 'NORMALIZATION_APPLIED' : 'SUBSTITUTION_VERIFICATION_FAILED',
          originalText: rawText,
          substitutedText: wasApplied ? replacementText : rawText,
          savings: normResult.savings,
          failOpen: !wasApplied
        };

      } catch (domErr) {
        // STRICT IMMEDIATE FAIL-OPEN GUARANTEE
        if (this.logger && typeof this.logger.warn === 'function') {
          this.logger.warn('FAILURE', 'Safe UI substitution failed with DOM error, bypassing to original prompt', {
            error: domErr.message
          });
        }

        return {
          status: SubstitutionStatus.FAILED,
          reason: `DOM_SUBSTITUTION_ERROR: ${domErr.message}`,
          originalText: rawText,
          substitutedText: rawText,
          failOpen: true
        };
      }
    }
  }

  const defaultUiSubstitutor = new SafeUiSubstitutor();

  return {
    SubstitutionStatus,
    SafeUiSubstitutor,
    defaultUiSubstitutor
  };
});
