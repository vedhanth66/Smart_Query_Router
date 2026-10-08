/**
 * Smart Query Router - Page-Facing Content Script
 * Scoped to: https://claude.ai/*
 *
 * Responsibilities:
 * 1. Passive DOM observation: detects prompt editor, model selector, attachments, and route changes.
 * 2. Submission detection: intercepts Enter keypresses and Send button clicks.
 * 3. On-device resolution: intercepts deterministic queries (arithmetic, datetime, greetings, etc.)
 *    and displays a floating Local Answer card without consuming cloud tokens.
 * 4. Safe UI substitution: applies non-destructive prompt normalization with full bypass support.
 * 5. Routing pipeline: classifies task and complexity, sends routing package to service worker,
 *    and records activity in the metrics tracker.
 * 6. Streaming lifecycle: tracks assistant response state and token consumption.
 */

(function initSmartQueryRouterContent() {
  'use strict';

  // ---------------------------------------------------------------------------
  // 1. Dependency Resolution & Module Initialization
  // ---------------------------------------------------------------------------

  const logger = globalThis.SmartQueryRouterLogger ? globalThis.SmartQueryRouterLogger.defaultLogger : null;
  const EventCategory = globalThis.SmartQueryRouterLogger
    ? globalThis.SmartQueryRouterLogger.EventCategory
    : { QUERY_DETECTION: 'QUERY_DETECTION', PAGE_ATTACH: 'PAGE_ATTACH', PAGE_DETACH: 'PAGE_DETACH', FAILURE: 'FAILURE' };

  const msgProtocol = globalThis.SmartQueryRouterMessages;
  if (!msgProtocol) {
    if (logger) logger.warn(EventCategory.FAILURE, 'Message protocol not loaded in content script');
    return;
  }

  const {
    MessageTypes,
    createInitMessage,
    createQueryObservedMessage,
    createOptimizeRequestMessage,
    createDryRunRecordMessage,
    createSuccessResponse
  } = msgProtocol;

  const normalizerModule = globalThis.SmartQueryRouterNormalizer || null;
  const turnTrackerModule = globalThis.SmartQueryRouterTurnTracker || null;
  const turnTracker = turnTrackerModule ? new turnTrackerModule.RecentTurnsTracker({ maxTurns: 4, maxSnippetChars: 300 }) : null;

  const queryEventModule = globalThis.SmartQueryRouterQueryEvent || null;
  const deduplicatorModule = globalThis.SmartQueryRouterDeduplicator || null;
  const deduplicator = deduplicatorModule ? new deduplicatorModule.QueryDeduplicator() : null;

  const decisionEngineModule = globalThis.SmartQueryRouterDecision || null;
  const decisionEngine = decisionEngineModule ? decisionEngineModule.defaultDecisionEngine : null;

  const arithmeticModule = globalThis.SmartQueryRouterArithmetic || null;
  const dateTimeModule = globalThis.SmartQueryRouterDateTime || null;
  const greetingModule = globalThis.SmartQueryRouterGreeting || null;
  const unitConversionModule = globalThis.SmartQueryRouterUnitConversion || null;
  const advancedCalculatorModule = globalThis.SmartQueryRouterAdvancedCalculator || null;

  if (decisionEngine) {
    if (arithmeticModule?.arithmeticRule) decisionEngine.registerRule(arithmeticModule.arithmeticRule);
    if (dateTimeModule?.dateTimeRule) decisionEngine.registerRule(dateTimeModule.dateTimeRule);
    if (greetingModule?.greetingRule) decisionEngine.registerRule(greetingModule.greetingRule);
    if (unitConversionModule?.unitConversionRule) decisionEngine.registerRule(unitConversionModule.unitConversionRule);
    if (advancedCalculatorModule?.advancedCalculatorRule) decisionEngine.registerRule(advancedCalculatorModule.advancedCalculatorRule);
  }

  const complexityScorerModule = globalThis.SmartQueryRouterComplexityScorer || null;
  const complexityScorer = complexityScorerModule ? new complexityScorerModule.ComplexityScorer() : null;

  const routingPolicyModule = globalThis.SmartQueryRouterRoutingPolicy || null;
  const routingPolicy = routingPolicyModule ? routingPolicyModule.defaultRoutingPolicy : null;

  const userSettingsModule = globalThis.SmartQueryRouterUserSettings || null;
  const userSettingsManager = userSettingsModule ? userSettingsModule.defaultUserSettingsManager : null;
  if (userSettingsManager && typeof userSettingsManager.load === 'function') {
    userSettingsManager.load().catch(() => {});
  }

  const optimizerMetricsModule = globalThis.SmartQueryRouterMetrics || null;
  const metricsTracker = optimizerMetricsModule ? optimizerMetricsModule.defaultMetricsTracker : null;
  const buildRouteDiagnostics = optimizerMetricsModule?.buildRouteDiagnostics || (() => ({}));

  const uiSubstitutionModule = globalThis.SmartQueryRouterUiSubstitution || null;
  const uiSubstitutor = uiSubstitutionModule
    ? new uiSubstitutionModule.SafeUiSubstitutor({ normalizer: normalizerModule, logger, userSettingsManager })
    : null;

  const statusSurfaceModule = globalThis.SmartQueryRouterStatusSurface || null;
  const statusSurfaceController = statusSurfaceModule ? statusSurfaceModule.defaultStatusSurface : null;

  const tokenCounterModule = globalThis.SmartQueryRouterTokenCounter || null;
  const telemetryModule = globalThis.SmartQueryRouterTelemetry || null;
  const relevanceRankerModule = globalThis.SmartQueryRouterRelevanceRanker || null;
  const contextPackagerModule = globalThis.SmartQueryRouterContextPackager || null;
  const optimizerPipelineModule = globalThis.SmartQueryRouterOptimizerPipeline || null;

  const outcomeFeedbackModule = globalThis.SmartQueryRouterOutcomeFeedback || null;
  const responseTrackerModule = globalThis.SmartQueryRouterResponseTracker || null;
  const { ResponseLifecycleState, FailureReason } = responseTrackerModule || {
    ResponseLifecycleState: { IDLE: 'IDLE', REQUEST_STARTED: 'REQUEST_STARTED', RESPONSE_STREAMING: 'RESPONSE_STREAMING', RESPONSE_COMPLETED: 'RESPONSE_COMPLETED', RESPONSE_FAILED: 'RESPONSE_FAILED' },
    FailureReason: { NONE: 'NONE' }
  };

  // State guards
  let lastObservedTimestamp = 0;
  const SUBMIT_DEBOUNCE_WINDOW_MS = 600;
  let latestTransientQueryEvent = null;

  // ---------------------------------------------------------------------------
  // 2. Messaging Helper
  // ---------------------------------------------------------------------------

  function sendTypedMessage(message, callback) {
    try {
      if (!chrome?.runtime?.sendMessage) return;
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          if (logger) logger.debug(EventCategory.FAILURE, 'Messaging note', { detail: chrome.runtime.lastError.message });
          return;
        }
        if (typeof callback === 'function') callback(response);
      });
    } catch (err) {
      if (logger) logger.debug(EventCategory.FAILURE, 'Message dispatch error', { error: err.message });
    }
  }

  // ---------------------------------------------------------------------------
  // 3. UI Controllers (Feedback & Local Answer)
  // ---------------------------------------------------------------------------

  const feedbackUiModule = globalThis.SmartQueryRouterFeedbackUi || null;
  const feedbackUiController = feedbackUiModule
    ? new feedbackUiModule.FeedbackUiController({
        userSettingsManager,
        logger,
        onSubmitFeedback: (feedbackParams) => submitUserFeedback(feedbackParams)
      })
    : null;

  const localAnswerUiModule = globalThis.SmartQueryRouterLocalAnswerUi || null;
  const localAnswerController = localAnswerUiModule
    ? new localAnswerUiModule.LocalAnswerUiController({
        userSettingsManager,
        logger,
        onInsertIntoEditor: (text) => {
          const editor = findPromptEditor();
          if (editor && uiSubstitutor) {
            uiSubstitutor.applyPromptOptimization(editor, { rawText: text, bypass: false });
          }
        }
      })
    : null;

  // ---------------------------------------------------------------------------
  // 4. Response Lifecycle Tracker
  // ---------------------------------------------------------------------------

  function getRoutingMeta() {
    if (!latestTransientQueryEvent) return {};
    const backendOpt = latestTransientQueryEvent.backendOptimization;
    return {
      coarseRoute: latestTransientQueryEvent.routing ? latestTransientQueryEvent.routing.route : null,
      modelRoute: backendOpt ? backendOpt.model_route : null,
      modelVersion: backendOpt?.execution_metadata ? backendOpt.execution_metadata.model_version : null,
      decisionType: backendOpt ? backendOpt.decision_type : null,
      taskCategory: latestTransientQueryEvent.taskClassification ? latestTransientQueryEvent.taskClassification.category : null,
      complexityLevel: latestTransientQueryEvent.complexity?.level || latestTransientQueryEvent.complexityScore?.level || null,
      cacheOutcome: backendOpt ? backendOpt.cache_outcome : null,
      ruleId: latestTransientQueryEvent.routing ? latestTransientQueryEvent.routing.ruleId : null,
      dryRun: Boolean(latestTransientQueryEvent.dryRunMode)
    };
  }

  function handleResponseLifecycleChange(newState, previousState, meta) {
    if (!outcomeFeedbackModule?.createOutcomeFeedback) return;

    const routingMeta = getRoutingMeta();
    const correlationId = meta?.correlationId || latestTransientQueryEvent?.metadata?.correlationId || `corr_${Date.now()}`;
    const requestId = meta?.requestId || latestTransientQueryEvent?.metadata?.eventId || null;

    if (newState === ResponseLifecycleState.RESPONSE_COMPLETED) {
      const compFeedback = outcomeFeedbackModule.createOutcomeFeedback({
        correlationId,
        requestId,
        outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
        source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
        routingMetadata: routingMeta,
        executionMetadata: { durationMs: meta?.durationMs || null, failureReason: null }
      });

      if (msgProtocol?.createOutcomeFeedbackMessage) {
        sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(compFeedback));
      }

      if (feedbackUiController) {
        feedbackUiController.notifyOptimization({ correlationId: compFeedback.correlationId, requestId: compFeedback.requestId, routingMetadata: routingMeta });
      }

      // Accounting for consumed tokens
      if (metricsTracker) {
        const promptText = latestTransientQueryEvent?.content?.rawText || '';
        const responseText = meta?.rawResponseText || '';
        const consumed = tokenCounterModule
          ? tokenCounterModule.estimateConsumedTokens(promptText, responseText, turnTracker)
          : { inputTokens: 0, outputTokens: 0, totalTokens: 0 };

        if (latestTransientQueryEvent?._recordedActivity) {
          latestTransientQueryEvent._recordedActivity.tokensConsumed = consumed.totalTokens;
          if (consumed.totalTokens > 0) {
            metricsTracker.totalTokensConsumed += consumed.totalTokens;
            metricsTracker.persist();
          }
        } else if (!latestTransientQueryEvent?._localAnswerResolved) {
          const activeOverride = userSettingsManager ? userSettingsManager.getRoutingOverride() : 'automatic';
          const resolved = resolveEffectiveRoute(null, latestTransientQueryEvent?.routing, activeOverride, routingMeta.complexityLevel);
          const finalReasonCode = activeOverride && activeOverride !== 'automatic'
            ? 'USER_OVERRIDE'
            : (resolved.isStrong ? 'COMPLEX_TASK_SIGNAL' : 'SIMPLE_TASK_SIGNAL');

          metricsTracker.recordActivity({
            route: resolved.route,
            modelTier: resolved.isStrong ? 'strong' : 'simple',
            cacheOutcome: routingMeta.cacheOutcome || 'NOT_CHECKED',
            tokensSaved: 0,
            tokensConsumed: consumed.totalTokens,
            latencyMs: meta?.durationMs || 0,
            status: 'COMPLETED',
            diagnostics: buildRouteDiagnostics({
              routeType: 'MODEL',
              routing: {
                coarseRoute: resolved.route,
                reasonCode: finalReasonCode,
                explanation: resolved.isStrong ? 'Classified as complex query based on task complexity signals.' : 'Classified as simple query based on standalone signals.'
              }
            })
          });
        }
      }
    } else if (newState === ResponseLifecycleState.RESPONSE_FAILED) {
      const errFeedback = outcomeFeedbackModule.createOutcomeFeedback({
        correlationId,
        requestId,
        outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.ERROR,
        source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
        routingMetadata: routingMeta,
        executionMetadata: { durationMs: meta?.durationMs || null, failureReason: meta?.failureReason || 'RESPONSE_FAILED' }
      });

      if (msgProtocol?.createOutcomeFeedbackMessage) {
        sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(errFeedback));
      }
    }

    if (newState === ResponseLifecycleState.RESPONSE_COMPLETED || newState === ResponseLifecycleState.RESPONSE_FAILED) {
      latestTransientQueryEvent = null;
    }

    if (msgProtocol?.createResponseStateUpdateMessage) {
      sendTypedMessage(msgProtocol.createResponseStateUpdateMessage(newState, meta));
    }
  }

  const responseTracker = responseTrackerModule
    ? new responseTrackerModule.ResponseStateTracker({
        logger,
        turnTracker,
        onStateChange: handleResponseLifecycleChange
      })
    : null;

  if (responseTracker) {
    responseTracker.recoverFromSessionStorage();
  }

  // ---------------------------------------------------------------------------
  // 5. Registration & Health Handlers
  // ---------------------------------------------------------------------------

  sendTypedMessage(createInitMessage(window.location.origin), (response) => {
    if (response?.success && logger) {
      logger.debug(EventCategory.PAGE_ATTACH, 'Content script registered with background', { hostname: window.location.hostname });
    }
  });

  if (chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (message?.type === MessageTypes.HEALTH_CHECK) {
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

  // ---------------------------------------------------------------------------
  // 6. DOM & Conversation Utilities
  // ---------------------------------------------------------------------------

  function sanitizeQueryString(text) {
    if (typeof text !== 'string') return '';
    return text
      .replace(/[\u200B-\u200D\uFEFF\u00AD\u2060\u180E]/g, '')
      .replace(/\u00A0/g, ' ')
      .replace(/[\r\n\t]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function findPromptEditor() {
    return document.querySelector('div[contenteditable="true"].ProseMirror') ||
      document.querySelector('div.ProseMirror[contenteditable="true"]') ||
      document.querySelector('div[contenteditable="true"]') ||
      document.querySelector('[contenteditable="true"]') ||
      document.querySelector('div[role="textbox"]');
  }

  function extractEditorText(editor) {
    if (!editor) return '';
    const pElements = editor.querySelectorAll ? editor.querySelectorAll('p') : null;
    if (pElements && pElements.length > 0) {
      const lines = [];
      pElements.forEach((p) => {
        lines.push(p.innerText !== undefined ? p.innerText : (p.textContent || ''));
      });
      return lines.join('\n');
    }
    return editor.innerText !== undefined ? editor.innerText : (editor.textContent || '');
  }

  function getActiveModelHint() {
    const modelBtn = document.querySelector('button[aria-haspopup="menu"]') || document.querySelector('button[role="combobox"]');
    if (!modelBtn) return null;
    const text = (modelBtn.innerText || modelBtn.getAttribute('aria-label') || '').trim();
    return text || null;
  }

  function isUserInConversation() {
    const path = window.location.pathname;
    const isConvPath = path.startsWith('/chat') || path.startsWith('/new') || path.startsWith('/project');
    return isConvPath || Boolean(findPromptEditor());
  }

  function detectDomAttachments() {
    try {
      const candidates = [];

      const removeBtnSelectors = [
        'button[aria-label*="Remove file" i]',
        'button[aria-label*="Remove attachment" i]',
        'button[aria-label*="Remove image" i]',
        'button[aria-label*="Remove" i][aria-label*="document" i]',
        'button[aria-label*="Remove" i][aria-label*="upload" i]',
        'button[aria-label*="Delete attachment" i]',
        'button[aria-label*="Delete file" i]'
      ];
      document.querySelectorAll(removeBtnSelectors.join(', ')).forEach((btn) => candidates.push(btn));

      const previewSelectors = [
        '[data-testid*="file-preview"]',
        '[data-testid*="image-preview"]',
        '[data-testid*="attachment-preview"]',
        '[data-testid*="attachment-thumbnail"]',
        '[data-testid*="attachment-card"]',
        '[data-testid*="attachment-pill"]',
        '.file-attachment',
        '.attachment-pill',
        '.attachment-preview',
        'img[alt*="upload" i]'
      ];
      document.querySelectorAll(previewSelectors.join(', ')).forEach((el) => {
        if (!candidates.includes(el)) candidates.push(el);
      });

      document.querySelectorAll('input[type="file"]').forEach((inp) => {
        if (inp.files && inp.files.length > 0 && !candidates.includes(inp)) {
          candidates.push(inp);
        }
      });

      document.querySelectorAll('[data-testid="file-upload"], [data-testid="attachment"]').forEach((el) => {
        const testId = (el.getAttribute('data-testid') || '').toLowerCase();
        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
        const isButton = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button';
        if ((isButton && (aria.includes('upload') || aria.includes('attach') || aria.includes('add'))) ||
            testId.includes('button') || testId.includes('trigger') || testId.includes('menu')) {
          return;
        }

        const hasAttached = el.querySelector && Boolean(
          el.querySelector('button[aria-label*="remove" i], button[aria-label*="delete" i], [data-testid*="preview"], img, .file-attachment')
        );
        if (hasAttached && !candidates.some((c) => el === c || (el.contains && el.contains(c)))) {
          candidates.push(el);
        }
      });

      const hasAttachments = candidates.length > 0;
      const types = [];

      if (hasAttachments) {
        candidates.forEach((node) => {
          const aria = (node.getAttribute('aria-label') || '').toLowerCase();
          const testId = (node.getAttribute('data-testid') || '').toLowerCase();
          const alt = (node.getAttribute('alt') || '').toLowerCase();
          const tag = (node.tagName || '').toLowerCase();

          if (aria.includes('image') || testId.includes('image') || alt.includes('image') || tag === 'img') {
            if (!types.includes('image')) types.push('image');
          } else if (aria.includes('file') || testId.includes('file') || aria.includes('document')) {
            if (!types.includes('file')) types.push('file');
          } else if (!types.includes('attachment')) {
            types.push('attachment');
          }
        });
        if (types.length === 0) types.push('attachment');
      }

      return { hasAttachments, count: candidates.length, types };
    } catch (_) {
      return { hasAttachments: false, count: 0, types: [] };
    }
  }

  // ---------------------------------------------------------------------------
  // 7. Local Answer Candidate Evaluation
  // ---------------------------------------------------------------------------

  function formatDateTimeResult(dtClass, now) {
    switch (dtClass.category) {
      case 'CURRENT_TIME':
        return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      case 'CURRENT_DATE':
        return now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
      case 'CURRENT_DATETIME':
        return `${now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}, ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      case 'CURRENT_DAY_OF_WEEK':
        return now.toLocaleDateString([], { weekday: 'long' });
      case 'CURRENT_YEAR':
        return String(now.getFullYear());
      case 'BROWSER_TIMEZONE':
        return dateTimeModule?.getBrowserExposedTimezone ? dateTimeModule.getBrowserExposedTimezone() : 'Browser-Local';
      default:
        return now.toLocaleString();
    }
  }

  function getGreetingResponse(greet, matched) {
    const type = greet.type || 'GREETING';
    if (type === 'SIGN_OFF') {
      return matched.startsWith('thank') ? "You're welcome! 😊" : 'Goodbye! Have a great day! 👋';
    }
    if (type === 'PLEASANTRY') {
      if (matched.includes('what') && (matched.includes('up') || matched.includes('new'))) return "Not much, just ready to help! What's on your mind today?";
      if (matched.includes('long time') || matched.includes('been a while')) return "Good to see you! How can I help you today?";
      if (matched.includes('nice to meet') || matched.includes('good to see')) return "Great to connect with you! How can I assist you today?";
      return "I'm doing great, thanks for asking! How can I help you today?";
    }
    // GREETING
    if (matched.includes('howdy')) return 'Howdy! How can I assist you today? 🤠';
    if (matched.includes('morning')) return 'Good morning! ☀️ How can I help you today?';
    if (matched.includes('afternoon')) return 'Good afternoon! 🌤️ How can I help you today?';
    if (matched.includes('evening')) return 'Good evening! 🌙 How can I help you today?';
    if (matched.includes('what') && (matched.includes('up') || matched.includes('new'))) return "Not much, just ready to help! What's on your mind today?";
    if (matched.includes('good day')) return 'Good day! How can I assist you today?';
    return 'Hello! 👋 How can I help you today?';
  }

  function evaluateLocalAnswerCandidate(text) {
    if (!text || typeof text !== 'string') return null;
    const sanitized = sanitizeQueryString(text);
    if (!sanitized) return null;

    // 1. Arithmetic
    if (typeof arithmeticModule?.evaluateDeterministicArithmetic === 'function') {
      const arith = arithmeticModule.evaluateDeterministicArithmetic(sanitized);
      if (arith && Number.isFinite(arith.result)) {
        return {
          canAnswerLocally: true,
          expression: arith.expression,
          result: arith.result,
          ruleId: arithmeticModule.RULE_ID || 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC',
          isMath: true
        };
      }
    }

    // 2. Date/Time
    if (typeof dateTimeModule?.classifyDateTimeQuery === 'function') {
      const dtClass = dateTimeModule.classifyDateTimeQuery(sanitized);
      if (dtClass?.eligible) {
        const now = new Date();
        let formattedResult = '';
        let expressionStr = sanitized;

        if (typeof dateTimeModule.evaluateDateTimeCalculation === 'function') {
          const calc = dateTimeModule.evaluateDateTimeCalculation(sanitized, now);
          if (calc?.success) {
            formattedResult = calc.result;
            expressionStr = calc.expression || sanitized;
          }
        }
        if (!formattedResult) {
          formattedResult = formatDateTimeResult(dtClass, now);
        }

        return {
          canAnswerLocally: true,
          expression: expressionStr,
          result: formattedResult,
          ruleId: dateTimeModule.RULE_ID || 'RULE_LOCAL_DETERMINISTIC_DATETIME',
          isMath: false
        };
      }
    }

    // 3. Advanced Calculator
    if (typeof advancedCalculatorModule?.evaluateAdvancedCalculation === 'function') {
      const adv = advancedCalculatorModule.evaluateAdvancedCalculation(sanitized);
      if (adv?.success) {
        return {
          canAnswerLocally: true,
          expression: adv.expression,
          result: adv.result,
          ruleId: advancedCalculatorModule.RULE_ID || 'RULE_LOCAL_DETERMINISTIC_ADVANCED_CALCULATOR',
          isMath: true
        };
      }
    }

    // 4. Greetings
    if (typeof greetingModule?.classifyGreeting === 'function') {
      const greet = greetingModule.classifyGreeting(sanitized);
      if (greet?.isGreeting) {
        const matched = (greet.matchedPhrase || sanitized).toLowerCase();
        return {
          canAnswerLocally: true,
          expression: sanitized,
          result: getGreetingResponse(greet, matched),
          ruleId: greetingModule.RULE_ID || 'RULE_LOCAL_CONVERSATIONAL_GREETING',
          isMath: false
        };
      }
    }

    // 5. Unit Conversion
    if (typeof unitConversionModule?.evaluateUnitConversion === 'function') {
      const conv = unitConversionModule.evaluateUnitConversion(sanitized);
      if (conv?.success) {
        return {
          canAnswerLocally: true,
          expression: conv.expression,
          result: conv.result,
          ruleId: unitConversionModule.RULE_ID || 'RULE_LOCAL_DETERMINISTIC_UNIT_CONVERSION',
          isMath: true
        };
      }
    }

    return null;
  }

  function triggerNativeSubmission(editor, text) {
    const sendBtn = document.querySelector('button[aria-label*="send" i], button[type="submit"]') ||
      (document.querySelector('button svg[data-icon="arrow-up"]') && document.querySelector('button svg[data-icon="arrow-up"]').closest('button'));

    if (sendBtn && !sendBtn.disabled) {
      sendBtn._sqrBypass = true;
      sendBtn.click();
      setTimeout(() => { delete sendBtn._sqrBypass; }, 500);
    } else if (editor) {
      const enterEvt = new KeyboardEvent('keydown', {
        key: 'Enter',
        code: 'Enter',
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
        altKey: true
      });
      editor.dispatchEvent(enterEvt);
    }
  }

  // ---------------------------------------------------------------------------
  // 8. Effective Route Resolution Helper
  // ---------------------------------------------------------------------------

  function resolveEffectiveRoute(decisionData, routingClassification, activeOverride, compLevel) {
    const isHighComplexity = compLevel === 'HIGH' || compLevel === 'VERY_HIGH' || compLevel === 'COMPLEX';
    const execMeta = decisionData?.execution_metadata || null;
    const rawRoute = (execMeta && execMeta.route) || decisionData?.coarse_route || '';
    const modelRoute = decisionData?.model_route ||
      execMeta?.model_id ||
      decisionData?.model_tier ||
      decisionData?.optimization_instructions?.suggested_model ||
      '';
    const onDeviceRoute = routingClassification?.route || null;

    if (activeOverride === 'prefer-strong') return { route: 'Complex Query', isStrong: true };
    if (activeOverride === 'prefer-simple') return { route: 'Simple Query', isStrong: false };
    if (rawRoute.includes('complex') || modelRoute.includes('strong') || modelRoute.includes('complex')) {
      return { route: 'Complex Query', isStrong: true };
    }
    if (rawRoute.includes('local') || rawRoute.includes('LOCAL')) return { route: 'Local (On-Device)', isStrong: false };
    if (rawRoute.includes('simple') || rawRoute.includes('SIMPLE')) return { route: 'Simple Query', isStrong: false };
    if (rawRoute.includes('evaluation')) return { route: 'Complex Query', isStrong: true };

    // Fallback on-device classification
    if (onDeviceRoute === 'complex-model candidate' || isHighComplexity || onDeviceRoute === 'needs-evaluation') {
      return { route: 'Complex Query', isStrong: true };
    }
    if (onDeviceRoute === 'local-eligible') return { route: 'Local (On-Device)', isStrong: false };
    return { route: 'Simple Query', isStrong: false };
  }

  // ---------------------------------------------------------------------------
  // 9. Query Observation & Backend Pipeline
  // ---------------------------------------------------------------------------

  function notifyQueryObserved(promptText, triggerSource, extraOptions = {}) {
    const now = Date.now();

    const safeContext = queryEventModule
      ? queryEventModule.extractSafeContext(window.location, getActiveModelHint())
      : { origin: window.location.origin, pathname: window.location.pathname };

    // Idempotency / deduplication
    if (deduplicator) {
      const dedupResult = deduplicator.recordSubmission(promptText, safeContext, now);
      if (!dedupResult.accepted) {
        if (logger) logger.debug(EventCategory.QUERY_DETECTION, 'Duplicate submission ignored', { reason: dedupResult.reason, trigger: triggerSource });
        return;
      }
    } else {
      if (now - lastObservedTimestamp < SUBMIT_DEBOUNCE_WINDOW_MS) return;
      lastObservedTimestamp = now;
    }

    const correlationId = telemetryModule
      ? telemetryModule.generateCorrelationId()
      : `corr_${now}_${Math.random().toString(36).slice(2, 9)}`;

    const domAttachments = detectDomAttachments();

    if (queryEventModule) {
      latestTransientQueryEvent = queryEventModule.createDetectedQueryEvent({
        rawPrompt: promptText,
        triggerType: triggerSource,
        context: safeContext,
        correlationId,
        domAttachments
      });

      if (extraOptions?.substitution) {
        latestTransientQueryEvent.uiSubstitution = extraOptions.substitution;
        if (extraOptions.substitution.status === 'BYPASSED' && outcomeFeedbackModule?.createOutcomeFeedback) {
          const bypassFeedback = outcomeFeedbackModule.createOutcomeFeedback({
            correlationId,
            requestId: latestTransientQueryEvent.metadata?.eventId || null,
            outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.OPTIMIZATION_BYPASS,
            source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
            routingMetadata: { dryRun: Boolean(latestTransientQueryEvent.dryRunMode) },
            executionMetadata: { substitutionStatus: 'BYPASSED', failureReason: extraOptions.substitution.reason || 'BYPASS' }
          });
          if (msgProtocol?.createOutcomeFeedbackMessage) {
            sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(bypassFeedback));
          }
        } else if (extraOptions.substitution.status === 'APPLIED') {
          if (metricsTracker) {
            const origLen = extraOptions.substitution.originalLength || 0;
            const subLen = extraOptions.substitution.substitutedLength || 0;
            const savedTok = tokenCounterModule
              ? Math.max(0, tokenCounterModule.countTokens(promptText) - tokenCounterModule.countTokens(extraOptions.substitution.substitutedText || ''))
              : Math.max(0, Math.ceil((origLen - subLen) / 4));
            metricsTracker.recordActivity({
              route: 'Prompt Normalization',
              modelTier: 'local',
              cacheOutcome: 'NOT_CHECKED',
              tokensSaved: savedTok,
              tokensConsumed: 0,
              latencyMs: extraOptions.substitution.durationMs || 1,
              status: 'APPLIED',
              diagnostics: buildRouteDiagnostics({
                routeType: 'LOCAL',
                routing: { reasonCode: 'LOCAL_DETERMINISTIC_RULE_MATCH', explanation: 'Prompt normalized locally to optimize whitespace and formatting.', signals: ['PROMPT_NORMALIZATION_APPLIED'] }
              })
            });
          }
          if (feedbackUiController) {
            feedbackUiController.notifyOptimization({
              correlationId,
              requestId: latestTransientQueryEvent.metadata?.eventId || null,
              routingMetadata: { substitutionStatus: 'APPLIED' }
            });
          }
        }
      }

      // Transient in-memory TTL cleanup
      const retentionCfg = userSettingsManager?.getRetentionConfig ? userSettingsManager.getRetentionConfig() : { transientEventTtlMs: 60000 };
      const currentEventId = latestTransientQueryEvent.metadata?.eventId || null;
      setTimeout(() => {
        if (latestTransientQueryEvent?.metadata?.eventId === currentEventId) {
          latestTransientQueryEvent = null;
        }
      }, retentionCfg?.transientEventTtlMs || 60000);
    }

    // Relevance ranking & candidate context packaging
    let contextRelevance = null;
    if (relevanceRankerModule && turnTracker && turnTracker.getTurnCount() > 0) {
      contextRelevance = relevanceRankerModule.rankTurnsByRelevance(promptText, turnTracker.getRecentTurns(), {
        contextDependency: latestTransientQueryEvent?.contextDependency || null
      });
      if (latestTransientQueryEvent) latestTransientQueryEvent.contextRelevance = contextRelevance;
    }

    let candidateContextPackage = null;
    const isContextPruningActive = userSettingsManager?.isOptimizationCategoryEnabled
      ? userSettingsManager.isOptimizationCategoryEnabled('contextPruning')
      : true;

    if (isContextPruningActive && contextPackagerModule && turnTracker && turnTracker.getTurnCount() > 0) {
      candidateContextPackage = contextPackagerModule.buildCandidateContextPackage({
        queryText: promptText,
        recentTurns: turnTracker.getRecentTurns(),
        contextRelevance,
        contextDependency: latestTransientQueryEvent?.contextDependency || null
      });
      if (latestTransientQueryEvent) latestTransientQueryEvent.candidateContextPackage = candidateContextPackage;
    }

    // In-memory turn tracking
    if (turnTracker) {
      turnTracker.recordTurn({ role: 'user', text: promptText, conversationId: safeContext.conversationId, timestamp: now });
    }

    if (responseTracker) {
      responseTracker.startRequest({ requestId: latestTransientQueryEvent?.metadata?.eventId || null, correlationId, timestamp: now });
    }

    // Rule evaluation
    const isLocalRulesActive = userSettingsManager?.isOptimizationCategoryEnabled
      ? userSettingsManager.isOptimizationCategoryEnabled('localRules')
      : true;

    if (isLocalRulesActive && decisionEngine && latestTransientQueryEvent) {
      const decision = decisionEngine.evaluate(latestTransientQueryEvent);
      latestTransientQueryEvent.optimization.status = 'EVALUATED';
      latestTransientQueryEvent.optimization.decision = decision;
      if (logger) {
        logger.info(EventCategory.OPTIMIZATION_DECISION, 'Optimization decision evaluated', { outcome: decision.outcome, ruleId: decision.ruleId, reason: decision.reason });
      }
    }

    // Complexity scoring
    let complexityScore = null;
    if (complexityScorer && latestTransientQueryEvent) {
      complexityScore = complexityScorer.computeScore({
        promptText,
        localFeatures: latestTransientQueryEvent.features,
        contextDependency: latestTransientQueryEvent.contextDependency,
        taskClassification: latestTransientQueryEvent.taskClassification
      });
      latestTransientQueryEvent.complexityScore = complexityScore;
      latestTransientQueryEvent.complexity = complexityScore;
    }

    // Deterministic Routing Policy
    let routingClassification = null;
    const activeOverride = userSettingsManager ? userSettingsManager.getRoutingOverride() : 'automatic';
    if (routingPolicy && latestTransientQueryEvent) {
      routingClassification = routingPolicy.classify(latestTransientQueryEvent, { userOverride: activeOverride });
      latestTransientQueryEvent.routing = routingClassification;
      latestTransientQueryEvent.userOverride = activeOverride;

      if (logger) {
        logger.info(EventCategory.OPTIMIZATION_DECISION, 'Deterministic coarse route classified', {
          route: routingClassification.route,
          ruleId: routingClassification.ruleId,
          reasonCode: routingClassification.reasonCode,
          confidence: routingClassification.confidence,
          userOverride: activeOverride
        });
      }
    }

    const safeSummary = (queryEventModule && latestTransientQueryEvent)
      ? queryEventModule.toSafeSummary(latestTransientQueryEvent)
      : { prompt_length: promptText.length, trigger: triggerSource, userOverride: activeOverride };

    if (logger) logger.info(EventCategory.QUERY_DETECTION, 'Prompt submission observed', safeSummary);

    sendTypedMessage(createQueryObservedMessage(safeSummary.characterCount || promptText.length, triggerSource));

    // Dry-run pipeline mode
    const isDryRun = userSettingsManager?.isDryRunMode ? userSettingsManager.isDryRunMode() : false;
    const isBackendRoutingEnabled = userSettingsManager?.isBackendEnabled
      ? userSettingsManager.isBackendEnabled() && (!userSettingsManager.isOptimizationCategoryEnabled || userSettingsManager.isOptimizationCategoryEnabled('backendRouting'))
      : true;

    if (isDryRun && optimizerPipelineModule?.executeDryRunPipeline) {
      optimizerPipelineModule.executeDryRunPipeline({
        promptText,
        triggerSource,
        safeContext,
        turnTracker,
        userSettingsManager,
        deduplicator: null,
        decisionEngine,
        routingPolicy,
        backendDispatcher: (pkg) => new Promise((resolve) => {
          if (!createOptimizeRequestMessage) return resolve(null);
          sendTypedMessage(createOptimizeRequestMessage(pkg), (resp) => resolve(resp?.data || null));
        }),
        logger,
        telemetry: telemetryModule,
        forceDryRun: true
      }).then((pipelineResult) => {
        if (pipelineResult?.proposedAction) {
          if (latestTransientQueryEvent) {
            latestTransientQueryEvent.proposedAction = pipelineResult.proposedAction;
            latestTransientQueryEvent.dryRunMode = true;
          }
          if (metricsTracker) {
            const act = pipelineResult.proposedAction;
            metricsTracker.recordActivity({
              route: act.routing?.coarseRoute || 'Dry Run Route',
              modelTier: act.routing?.modelTier || 'simple',
              cacheOutcome: act.caching?.cacheOutcome || 'NOT_CHECKED',
              tokensSaved: 0,
              latencyMs: act.backend?.latencyMs || 0,
              status: 'DRY_RUN',
              diagnostics: buildRouteDiagnostics({
                routing: act.routing,
                complexityScore: act.complexityScore || latestTransientQueryEvent?.complexityScore,
                cacheOutcome: act.caching?.cacheOutcome || 'NOT_CHECKED'
              })
            });
          }
          if (createDryRunRecordMessage) {
            sendTypedMessage(createDryRunRecordMessage(pipelineResult.proposedAction));
          }
        }
      }).catch((err) => {
        if (logger) logger.warn(EventCategory.FAILURE, 'Dry-run pipeline execution error', { error: err.message });
      });
    } else if (isBackendRoutingEnabled && createOptimizeRequestMessage) {
      // Backend dispatch
      const queryPackage = {
        request_id: latestTransientQueryEvent?.metadata?.eventId || `req_${now}_${Math.random().toString(36).slice(2, 6)}`,
        correlation_id: correlationId,
        user_override: activeOverride,
        coarse_route: routingClassification ? routingClassification.route : null,
        task_category: latestTransientQueryEvent?.taskClassification?.category || routingClassification?.taskCategory || null,
        complexity_score: latestTransientQueryEvent?.complexity?.score || null,
        complexity_level: latestTransientQueryEvent?.complexity?.level || null,
        query_text: promptText,
        context_candidates: candidateContextPackage?.includedTurns ? candidateContextPackage.includedTurns.slice(0, 10).map((t) => ({
          turn_id: t.turnId,
          role: t.role,
          content: t.content,
          original_index: t.originalIndex,
          relevance_score: t.relevanceScore || 0.0,
          timestamp: t.timestamp
        })) : [],
        local_features: latestTransientQueryEvent?.features ? {
          character_count: latestTransientQueryEvent.features.length?.characterCount || promptText.length,
          word_count: latestTransientQueryEvent.features.length?.wordCount || promptText.split(/\s+/).filter(Boolean).length,
          has_code: Boolean(latestTransientQueryEvent.features.code?.hasCodeSyntax),
          has_math: Boolean(latestTransientQueryEvent.features.math?.hasMathSymbols),
          has_questions: Boolean(latestTransientQueryEvent.features.questions?.questionCount > 0),
          has_urls: Boolean(latestTransientQueryEvent.features.urls?.hasUrl),
          is_normalized: Boolean(latestTransientQueryEvent.content?.isNormalized),
          detected_cues: latestTransientQueryEvent.features.cues?.detectedCues || [],
          has_rich_input: Boolean(latestTransientQueryEvent.features.richContent?.hasRichInput),
          has_attachments: Boolean(latestTransientQueryEvent.features.richContent?.hasAttachments),
          has_images: Boolean(latestTransientQueryEvent.features.richContent?.hasImages),
          has_files: Boolean(latestTransientQueryEvent.features.richContent?.hasFiles),
          has_code_blocks: Boolean(latestTransientQueryEvent.features.richContent?.hasCodeBlocks),
          has_tables: Boolean(latestTransientQueryEvent.features.richContent?.hasTables),
          attachment_types: latestTransientQueryEvent.features.richContent?.types || []
        } : null,
        client_metadata: {
          extension_version: '0.1.0',
          client_type: 'chrome_extension',
          schema_version: '1.0',
          hostname: (window?.location?.hostname ? String(window.location.hostname).replace(/[:/\\?#].*$/, '') : 'claude.ai')
        },
        execute_route: false
      };

      const clientStartMs = now;
      sendTypedMessage(createOptimizeRequestMessage(queryPackage), (response) => {
        const clientLatencyMs = Date.now() - clientStartMs;
        const decisionData = response?.data || null;
        if (latestTransientQueryEvent && decisionData) {
          latestTransientQueryEvent.backendOptimization = decisionData;
        }

        if (metricsTracker && decisionData) {
          const compLevel = latestTransientQueryEvent?.complexity?.level || latestTransientQueryEvent?.complexityScore?.level || '';
          const resolved = resolveEffectiveRoute(decisionData, routingClassification, activeOverride, compLevel);
          const cacheOutcome = decisionData.cache_outcome || 'MISS';
          const prunedCount = candidateContextPackage?.metadata?.prunedTurnIds?.length || 0;
          const promptToks = tokenCounterModule ? tokenCounterModule.countTokens(promptText) : Math.ceil(promptText.length / 4);
          const tokensSaved = (prunedCount * 35) + (cacheOutcome === 'HIT' ? (promptToks + 20) : 0);

          const diag = buildRouteDiagnostics({
            routing: routingClassification || latestTransientQueryEvent?.routing,
            complexityScore: complexityScore || latestTransientQueryEvent?.complexity,
            userOverride: activeOverride,
            decisionData,
            cacheOutcome
          });

          const recordedActivity = metricsTracker.recordActivity({
            route: resolved.route,
            modelTier: resolved.isStrong ? 'strong' : 'simple',
            cacheOutcome,
            tokensSaved,
            tokensConsumed: 0,
            latencyMs: clientLatencyMs,
            status: 'COMPLETED',
            diagnostics: diag
          });
          if (latestTransientQueryEvent) {
            latestTransientQueryEvent._recordedActivity = recordedActivity;
          }
        }

        // Telemetry Performance Record
        const isTelemetryActive = userSettingsManager?.isTelemetryEnabled
          ? userSettingsManager.isTelemetryEnabled('performanceMetrics')
          : true;

        if (isTelemetryActive && telemetryModule && latestTransientQueryEvent) {
          const execMeta = decisionData?.execution_metadata || null;
          const executedRoute = execMeta?.route || decisionData?.coarse_route || null;
          const modelVersion = execMeta?.model_version || null;
          const escalationOccurred = Boolean(execMeta?.escalation_occurred);
          const escalationReason = execMeta?.escalation_reason || null;

          if (escalationOccurred && outcomeFeedbackModule?.createOutcomeFeedback) {
            const escFeedback = outcomeFeedbackModule.createOutcomeFeedback({
              correlationId: decisionData?.correlation_id || correlationId,
              requestId: latestTransientQueryEvent.metadata?.eventId || null,
              outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.ESCALATION,
              source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
              routingMetadata: { coarseRoute: executedRoute, modelRoute: decisionData?.model_route || null, modelVersion, escalationOccurred: true, escalationReason },
              executionMetadata: { durationMs: clientLatencyMs }
            });
            if (msgProtocol?.createOutcomeFeedbackMessage) {
              sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(escFeedback));
            }
          }

          const perfRecord = telemetryModule.createPerformanceRecord({
            correlationId: decisionData?.correlation_id || correlationId,
            clientTimestamp: clientStartMs,
            backendTimestamp: decisionData?.timestamp || null,
            decisionType: decisionData?.decision_type || 'NO_OPTIMIZATION',
            coarseRoute: executedRoute,
            modelRoute: decisionData?.model_route || execMeta?.model_id || decisionData?.model_tier || decisionData?.optimization_instructions?.suggested_model || null,
            modelVersion,
            cacheOutcome: decisionData?.cache_outcome || telemetryModule.CacheOutcome.NOT_CHECKED,
            latencyMs: clientLatencyMs,
            executionLatencyMs: execMeta?.latency_ms || null,
            errorCategory: response && !response.success ? (response.errorCategory || telemetryModule.ErrorCategory.NETWORK_ERROR) : telemetryModule.ErrorCategory.NONE,
            failureCategory: execMeta?.failure_category || 'NONE',
            escalationOccurred,
            escalationReason,
            localFeatures: latestTransientQueryEvent.features,
            candidateCount: candidateContextPackage?.includedTurns?.length || 0,
            versionIdentifiers: { extension: '0.1.0', server: '0.1.0', schema: '1.0' },
            options: { enabled: false, environment: 'production' }
          });
          latestTransientQueryEvent.performanceRecord = perfRecord;

          if (logger) {
            logger.info(EventCategory.BACKEND_CALL || 'BACKEND_CALL', 'Optimization performance record recorded', {
              correlation_id: perfRecord.correlation_id,
              latency_ms: perfRecord.latency_ms,
              decision_type: perfRecord.decision_type,
              coarse_route: perfRecord.coarse_route,
              model_version: perfRecord.model_version,
              failure_category: perfRecord.failure_category,
              cache_outcome: perfRecord.cache_outcome,
              error_category: perfRecord.error_category,
              escalation_occurred: perfRecord.escalation_occurred,
              escalation_reason: perfRecord.escalation_reason
            });
          }
        }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // 10. Unified Submission Handler
  // ---------------------------------------------------------------------------

  function handlePromptSubmission(event, triggerSource, editor) {
    let text = extractEditorText(editor);
    if (!text || !text.trim()) return;

    const isBypass = uiSubstitutor ? uiSubstitutor.isBypassTrigger(event) : false;
    const isLocalAnsweringActive = userSettingsManager?.isLocalAnsweringEnabled
      ? (!userSettingsManager.isOptimizationEnabled || userSettingsManager.isOptimizationEnabled()) && userSettingsManager.isLocalAnsweringEnabled()
      : true;

    if (isLocalAnsweringActive && !isBypass && localAnswerController) {
      const localCandidate = evaluateLocalAnswerCandidate(text);
      if (localCandidate?.canAnswerLocally) {
        event._sqrHandled = true;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();

        localAnswerController.showAnswer({
          expression: localCandidate.expression,
          result: localCandidate.result,
          ruleId: localCandidate.ruleId,
          rawQuery: text,
          isMath: localCandidate.isMath,
          editorElement: editor,
          onAskClaudeAnyway: () => triggerNativeSubmission(editor, text)
        });

        if (metricsTracker) {
          const diag = buildRouteDiagnostics({ routeType: 'LOCAL', cacheOutcome: 'HIT' });
          const tokensSaved = tokenCounterModule
            ? tokenCounterModule.estimateLocalRuleSavings(text, localCandidate.result, turnTracker)
            : Math.max(1, Math.ceil(text.length / 4) + 15);

          metricsTracker.recordActivity({
            route: 'Local Deterministic Rule',
            modelTier: 'local',
            cacheOutcome: 'HIT',
            tokensSaved,
            tokensConsumed: 0,
            latencyMs: 0.2,
            status: 'RESOLVED_LOCALLY',
            diagnostics: diag
          });
        }

        latestTransientQueryEvent = { _localAnswerResolved: true };
        if (logger) {
          logger.info(EventCategory.ROUTING_DECISION, 'Query resolved on-device by local answer card', {
            expression: localCandidate.expression,
            result: localCandidate.result,
            ruleId: localCandidate.ruleId
          });
        }
        return;
      }
    }

    // Safe prompt normalization
    let substitutionResult = null;
    if (uiSubstitutor?.applyPromptOptimization) {
      substitutionResult = uiSubstitutor.applyPromptOptimization(editor, { rawText: text, bypass: isBypass });
      if (substitutionResult?.status === 'APPLIED') {
        text = substitutionResult.substitutedText;
      }
    }

    notifyQueryObserved(text, triggerSource, { substitution: substitutionResult });
  }

  function handleKeyDown(event) {
    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.metaKey || event.isComposing || event._sqrHandled) {
      return;
    }

    const target = event.target;
    if (!target) return;
    const elementTarget = target.nodeType === 3 ? target.parentElement : target;
    let editor = elementTarget?.closest ? (
      elementTarget.closest('div[contenteditable="true"]') ||
      elementTarget.closest('[contenteditable="true"]') ||
      elementTarget.closest('.ProseMirror')
    ) : null;

    if (!editor) {
      const activePrompt = findPromptEditor();
      if (activePrompt && (activePrompt === document.activeElement || activePrompt.contains?.(elementTarget))) {
        editor = activePrompt;
      }
    }

    if (!editor) return;
    handlePromptSubmission(event, 'keyboard_enter', editor);
  }

  function handleClick(event) {
    if (event._sqrHandled || !event.target) return;
    const elementTarget = event.target.nodeType === 3 ? event.target.parentElement : event.target;
    if (!elementTarget?.closest) return;

    const button = elementTarget.closest('button');
    if (!button || button.disabled || button._sqrBypass) return;

    const ariaLabel = (button.getAttribute('aria-label') || '').toLowerCase();
    const testId = (button.getAttribute('data-testid') || '').toLowerCase();
    const isSendButton = ariaLabel.includes('send') ||
      ariaLabel.includes('prompt') ||
      ariaLabel.includes('submit') ||
      testId.includes('send') ||
      testId.includes('submit') ||
      button.getAttribute('type') === 'submit' ||
      Boolean(button.querySelector('svg[data-icon="arrow-up"], svg[data-icon="arrow-right"], svg.lucide-arrow-up'));

    if (!isSendButton) return;

    const editor = findPromptEditor();
    if (!editor) return;
    handlePromptSubmission(event, 'button_click', editor);
  }

  window.addEventListener('keydown', handleKeyDown, { capture: true, passive: false });
  document.addEventListener('keydown', handleKeyDown, { capture: true, passive: false });
  window.addEventListener('click', handleClick, { capture: true, passive: false });
  document.addEventListener('click', handleClick, { capture: true, passive: false });

  // ---------------------------------------------------------------------------
  // 11. Navigation & Route Monitoring
  // ---------------------------------------------------------------------------

  let lastRecordedPath = window.location.pathname;

  function observeAssistantTurnFromDom() {
    const safeContext = queryEventModule
      ? queryEventModule.extractSafeContext(window.location, getActiveModelHint())
      : { conversationId: null };

    if (responseTracker) {
      responseTracker.processDomUpdate(document, safeContext.conversationId);
      return;
    }

    if (!turnTracker) return;
    try {
      const assistantNodes = document.querySelectorAll('[data-message-author-role="assistant"], .font-claude-message');
      if (!assistantNodes?.length) return;

      const latestNode = assistantNodes[assistantNodes.length - 1];
      if (latestNode.getAttribute('data-is-streaming') === 'true') return;

      const text = (latestNode.innerText || latestNode.textContent || '').trim();
      if (text.length > 0) {
        turnTracker.recordTurn({ role: 'assistant', text, conversationId: safeContext.conversationId });
      }
    } catch (_) {}
  }

  function checkRouteOrDomChange() {
    const currentPath = window.location.pathname;
    if (currentPath !== lastRecordedPath) {
      lastRecordedPath = currentPath;
      if (logger) {
        logger.debug(EventCategory.PAGE_ATTACH, 'Claude route changed', { path: currentPath, inConversation: isUserInConversation() });
      }

      if (responseTracker) responseTracker.handleNavigation(currentPath);
      if (turnTracker) {
        const chatMatch = currentPath.match(/^\/chat\/([a-zA-Z0-9_\-]+)/);
        const newConvId = chatMatch ? chatMatch[1] : null;
        if (newConvId !== turnTracker.currentConversationId) {
          turnTracker.switchConversation(newConvId);
          if (logger) {
            logger.debug(EventCategory.PAGE_ATTACH, 'Turn tracker reset for conversation switch', { newConversationId: newConvId });
          }
        }
      }
    }

    observeAssistantTurnFromDom();
  }

  window.addEventListener('popstate', checkRouteOrDomChange, { passive: true });

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
      if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    });
  }

  // ---------------------------------------------------------------------------
  // 12. Cleanup & User Feedback Interface
  // ---------------------------------------------------------------------------

  window.addEventListener('pagehide', () => {
    observer.disconnect();
    document.removeEventListener('keydown', handleKeyDown, { capture: true });
    document.removeEventListener('click', handleClick, { capture: true });
    if (responseTracker) responseTracker.handlePageUnload();
    if (turnTracker) turnTracker.clear();
    if (feedbackUiController) feedbackUiController.hide();
    if (logger) logger.debug(EventCategory.PAGE_DETACH, 'Page detached from Claude view');
  }, { capture: true, once: true });

  function submitUserFeedback(options = {}) {
    if (!outcomeFeedbackModule?.createOutcomeFeedback) return null;
    const correlationId = options.correlationId || latestTransientQueryEvent?.metadata?.correlationId || `corr_fb_${Date.now()}`;
    const isNegative = options.rating === outcomeFeedbackModule.UserRating.NEGATIVE || Boolean(options.rejectionReason);
    const outcomeType = isNegative
      ? outcomeFeedbackModule.FeedbackOutcomeType.USER_REJECTION
      : outcomeFeedbackModule.FeedbackOutcomeType.SUCCESSFUL_COMPLETION;

    const userFeedbackEvent = outcomeFeedbackModule.createOutcomeFeedback({
      correlationId,
      requestId: options.requestId || latestTransientQueryEvent?.metadata?.eventId || null,
      outcomeType,
      source: outcomeFeedbackModule.FeedbackSource.USER,
      routingMetadata: options.routingMetadata || {},
      userFeedback: {
        rating: options.rating || null,
        rejectionReason: options.rejectionReason || null,
        notes: options.notes || null,
        submittedAt: Date.now()
      }
    });

    if (msgProtocol?.createOutcomeFeedbackMessage) {
      sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(userFeedbackEvent));
    }
    return userFeedbackEvent;
  }

  // Global interfaces for testing and developer tooling
  if (typeof globalThis !== 'undefined') {
    globalThis.__smartQueryRouterResponseTracker = responseTracker;
    globalThis.__smartQueryRouter_submitUserFeedback = submitUserFeedback;
    globalThis.__smartQueryRouterFeedbackUi = feedbackUiController;
  }
})();
