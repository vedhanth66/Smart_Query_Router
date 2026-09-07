/**
 * Smart Query Router - Page-Facing Content Script
 * Scoped to: https://claude.ai/*
 * Non-intrusive observation layer:
 * - Reliably detects when the user is in a Claude conversation.
 * - Detects prompt submission triggers (Enter keypress and Send button clicks).
 * - Builds an in-memory DetectedQueryEvent data structure separating content from metadata.
 * - Observational ONLY: strictly does NOT block, delay, rewrite, duplicate, or replace prompts.
 * - Fully passive ({ passive: true, capture: true }) ensuring zero user-facing latency.
 * - Does NOT persist content or send to backend yet.
 */

(function initSmartQueryRouterContent() {
  const SCRIPT_TAG = '[Smart Query Router Content]';

  const logger = globalThis.SmartQueryRouterLogger ? globalThis.SmartQueryRouterLogger.defaultLogger : null;
  const EventCategory = globalThis.SmartQueryRouterLogger
    ? globalThis.SmartQueryRouterLogger.EventCategory
    : { QUERY_DETECTION: 'QUERY_DETECTION', PAGE_ATTACH: 'PAGE_ATTACH', PAGE_DETACH: 'PAGE_DETACH', FAILURE: 'FAILURE' };

  const msgProtocol = globalThis.SmartQueryRouterMessages;
  if (!msgProtocol) {
    if (logger) logger.warn(EventCategory.FAILURE, 'Message protocol not loaded in content script');
    return;
  }

  const normalizerModule = globalThis.SmartQueryRouterNormalizer || null;
  const featureExtractorModule = globalThis.SmartQueryRouterFeatureExtractor || null;
  const contextDetectorModule = globalThis.SmartQueryRouterContextDetector || null;
  const turnTrackerModule = globalThis.SmartQueryRouterTurnTracker || null;
  const turnTracker = turnTrackerModule ? new turnTrackerModule.RecentTurnsTracker({
    maxTurns: 4,
    maxSnippetChars: 300
  }) : null;
  const queryEventModule = globalThis.SmartQueryRouterQueryEvent || null;
  const deduplicatorModule = globalThis.SmartQueryRouterDeduplicator || null;
  const deduplicator = deduplicatorModule ? new deduplicatorModule.QueryDeduplicator() : null;
  const decisionEngineModule = globalThis.SmartQueryRouterDecision || null;
  const decisionEngine = decisionEngineModule ? decisionEngineModule.defaultDecisionEngine : null;
  const arithmeticModule = globalThis.SmartQueryRouterArithmetic || null;
  const dateTimeModule = globalThis.SmartQueryRouterDateTime || null;
  const greetingModule = globalThis.SmartQueryRouterGreeting || null;

  // Register deterministic rule plugins if available
  if (decisionEngine) {
    if (arithmeticModule && arithmeticModule.arithmeticRule) {
      decisionEngine.registerRule(arithmeticModule.arithmeticRule);
    }
    if (dateTimeModule && dateTimeModule.dateTimeRule) {
      decisionEngine.registerRule(dateTimeModule.dateTimeRule);
    }
    if (greetingModule && greetingModule.greetingRule) {
      decisionEngine.registerRule(greetingModule.greetingRule);
    }
  }

  const {
    MessageTypes,
    createInitMessage,
    createQueryObservedMessage,
    createSuccessResponse
  } = msgProtocol;

  // Safe typed messaging helper
  function sendTypedMessage(message, callback) {
    try {
      if (!chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          if (logger) {
            logger.debug(EventCategory.FAILURE, 'Messaging note', {
              detail: chrome.runtime.lastError.message
            });
          }
          return;
        }
        if (callback && typeof callback === 'function') {
          callback(response);
        }
      });
    } catch (err) {
      if (logger) {
        logger.debug(EventCategory.FAILURE, 'Message dispatch error', { error: err.message });
      }
    }
  }

  // 1. Initial registration with background runtime
  const initMsg = createInitMessage(window.location.origin);
  sendTypedMessage(initMsg, (response) => {
    if (response && response.success && logger) {
      logger.debug(EventCategory.PAGE_ATTACH, 'Content script registered with background', {
        hostname: window.location.hostname
      });
    }
  });

  // 2. Health probe responder
  if (chrome && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (message && message.type === MessageTypes.HEALTH_CHECK) {
        sendResponse(
          createSuccessResponse({
            active: true,
            component: 'CLAUDE_CONTENT_SCRIPT',
            hostname: window.location.hostname,
            inConversation: isUserInConversation()
          })
        );
        return false;
      }
      return false;
    });
  }

  // 3. Conversation & DOM Detection Utilities
  function findPromptEditor() {
    return document.querySelector('div[contenteditable="true"].ProseMirror') ||
      document.querySelector('div[contenteditable="true"]');
  }

  function getActiveModelHint() {
    // Attempt to read accessible text from model picker button if present
    const modelBtn = document.querySelector('button[aria-haspopup="menu"]') ||
      document.querySelector('button[role="combobox"]');
    if (!modelBtn) return null;
    const text = (modelBtn.innerText || modelBtn.getAttribute('aria-label') || '').trim();
    return text || null;
  }

  function isUserInConversation() {
    const path = window.location.pathname;
    const isConvPath = path.startsWith('/chat') || path.startsWith('/new') || path.startsWith('/project');
    const hasEditor = Boolean(findPromptEditor());
    return isConvPath || hasEditor;
  }

  function extractEditorText(editor) {
    if (!editor) return '';
    return editor.innerText || editor.textContent || '';
  }

  // 4. Observational Submission Detection
  // State guard to prevent double-counting within a single submission cycle
  let lastObservedTimestamp = 0;
  const SUBMIT_DEBOUNCE_WINDOW_MS = 600;

  // Transient in-memory reference to most recent query event (never persisted)
  let latestTransientQueryEvent = null;

  function notifyQueryObserved(promptText, triggerSource) {
    const now = Date.now();

    const safeContext = queryEventModule
      ? queryEventModule.extractSafeContext(window.location, getActiveModelHint())
      : { origin: window.location.origin, pathname: window.location.pathname };

    // Enforce idempotency: check bounded deduplicator
    if (deduplicator) {
      const dedupResult = deduplicator.recordSubmission(promptText, safeContext, now);
      if (!dedupResult.accepted) {
        if (logger) {
          logger.debug(
            EventCategory.QUERY_DETECTION,
            'Duplicate submission ignored',
            { reason: dedupResult.reason, trigger: triggerSource }
          );
        }
        return; // Idempotent: drop duplicate event
      }
    } else {
      // Fallback simple debounce
      if (now - lastObservedTimestamp < SUBMIT_DEBOUNCE_WINDOW_MS) {
        return;
      }
      lastObservedTimestamp = now;
    }

    // Record user turn in bounded in-memory tracker (zero persistence, local only)
    if (turnTracker) {
      turnTracker.recordTurn({
        role: 'user',
        text: promptText,
        conversationId: safeContext.conversationId,
        timestamp: now
      });
    }

    // Construct the typed in-memory query event structure
    if (queryEventModule) {
      latestTransientQueryEvent = queryEventModule.createDetectedQueryEvent({
        rawPrompt: promptText,
        triggerType: triggerSource,
        context: safeContext
      });

      // Evaluate optimization decision interface
      if (decisionEngine) {
        const decision = decisionEngine.evaluate(latestTransientQueryEvent);
        latestTransientQueryEvent.optimization.status = 'EVALUATED';
        latestTransientQueryEvent.optimization.decision = decision;

        if (logger) {
          logger.info(
            EventCategory.OPTIMIZATION_DECISION,
            'Optimization decision evaluated',
            {
              outcome: decision.outcome,
              ruleId: decision.ruleId,
              reason: decision.reason
            }
          );
        }
      }
    }

    // Produce sanitized summary safe for diagnostics/logging (zero raw prompt text)
    const safeSummary = (queryEventModule && latestTransientQueryEvent)
      ? queryEventModule.toSafeSummary(latestTransientQueryEvent)
      : { prompt_length: promptText.length, trigger: triggerSource };

    if (logger) {
      logger.info(
        EventCategory.QUERY_DETECTION,
        'Prompt submission observed',
        safeSummary
      );
    }

    const observedMsg = createQueryObservedMessage(
      safeSummary.characterCount || promptText.length,
      triggerSource
    );
    sendTypedMessage(observedMsg);
  }

  /**
   * Keyboard Trigger Observation:
   * Attaches to document using passive capture.
   * STRICTLY DOES NOT call preventDefault() or stopPropagation().
   */
  function handleKeyDown(event) {
    if (event.key !== 'Enter') return;
    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.isComposing) return; // IME composition in progress

    // Verify event originated from or within a contenteditable prompt editor
    const target = event.target;
    if (!target) return;

    const editor = target.closest
      ? target.closest('div[contenteditable="true"]')
      : (target.getAttribute && target.getAttribute('contenteditable') === 'true' ? target : null);

    if (!editor) return;

    const text = extractEditorText(editor);
    if (text.trim().length > 0) {
      notifyQueryObserved(text, 'keyboard_enter');
    }
  }

  /**
   * Button Trigger Observation:
   * Attaches to document using passive capture.
   * STRICTLY DOES NOT call preventDefault() or stopPropagation().
   */
  function handleClick(event) {
    const target = event.target;
    if (!target || !target.closest) return;

    // Detect click on send button (aria-label containing send/prompt or submit button)
    const button = target.closest('button');
    if (!button || button.disabled) return;

    const ariaLabel = (button.getAttribute('aria-label') || '').toLowerCase();
    const isSendButton = ariaLabel.includes('send') || ariaLabel.includes('prompt') ||
      button.getAttribute('type') === 'submit' ||
      button.querySelector('svg[data-icon="arrow-up"], svg[data-icon="arrow-right"]');

    if (!isSendButton) return;

    const editor = findPromptEditor();
    if (!editor) return;

    const text = extractEditorText(editor);
    if (text.trim().length > 0) {
      notifyQueryObserved(text, 'button_click');
    }
  }

  // Attach global passive listeners (resilient to dynamic DOM mounting and remounting)
  document.addEventListener('keydown', handleKeyDown, { capture: true, passive: true });
  document.addEventListener('click', handleClick, { capture: true, passive: true });

  // 5. Dynamic DOM & Route Change Monitoring
  let lastRecordedPath = window.location.pathname;

  // Passive observation of assistant turn from DOM when available and safe
  function observeAssistantTurnFromDom() {
    if (!turnTracker) return;
    try {
      const assistantNodes = document.querySelectorAll(
        '[data-message-author-role="assistant"], .font-claude-message'
      );
      if (!assistantNodes || assistantNodes.length === 0) return;

      const latestNode = assistantNodes[assistantNodes.length - 1];
      if (latestNode.getAttribute('data-is-streaming') === 'true') {
        return; // Don't capture while actively streaming
      }

      const text = (latestNode.innerText || latestNode.textContent || '').trim();
      if (text.length > 0) {
        const safeContext = queryEventModule
          ? queryEventModule.extractSafeContext(window.location, getActiveModelHint())
          : { conversationId: null };

        turnTracker.recordTurn({
          role: 'assistant',
          text,
          conversationId: safeContext.conversationId
        });
      }
    } catch (_) {
      // Fail-open: ignore DOM reading errors
    }
  }

  function checkRouteOrDomChange() {
    const currentPath = window.location.pathname;
    if (currentPath !== lastRecordedPath) {
      lastRecordedPath = currentPath;
      if (logger) {
        logger.debug(EventCategory.PAGE_ATTACH, 'Claude route changed', {
          path: currentPath,
          inConversation: isUserInConversation()
        });
      }

      // Cleanup on conversation switch or navigation away from chat
      if (turnTracker) {
        const chatMatch = currentPath.match(/^\/chat\/([a-zA-Z0-9_\-]+)/);
        const newConvId = chatMatch ? chatMatch[1] : null;
        if (newConvId !== turnTracker.currentConversationId) {
          turnTracker.switchConversation(newConvId);
          if (logger) {
            logger.debug(EventCategory.PAGE_ATTACH, 'Turn tracker reset for conversation switch', {
              newConversationId: newConvId
            });
          }
        }
      }
    }

    // Safely observe recent assistant turn if available
    observeAssistantTurnFromDom();
  }

  window.addEventListener('popstate', checkRouteOrDomChange, { passive: true });

  // Lightweight MutationObserver watching for editor mounting/unmounting
  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    if (debounceTimer) return;
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      checkRouteOrDomChange();
    }, 300);
  });

  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener('DOMContentLoaded', () => {
      if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
      }
    });
  }

  // 6. Graceful cleanup on unload
  window.addEventListener('pagehide', () => {
    observer.disconnect();
    document.removeEventListener('keydown', handleKeyDown, { capture: true });
    document.removeEventListener('click', handleClick, { capture: true });
    if (turnTracker) {
      turnTracker.clear();
    }
    if (logger) {
      logger.debug(EventCategory.PAGE_DETACH, 'Page detached from Claude view');
    }
  }, { capture: true, once: true });
})();
