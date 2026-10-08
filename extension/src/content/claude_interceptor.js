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
  const relevanceRankerModule = globalThis.SmartQueryRouterRelevanceRanker || null;
  const contextPackagerModule = globalThis.SmartQueryRouterContextPackager || null;
  const experimentalSummarizerModule = globalThis.SmartQueryRouterExperimentalSummarizer || null;
  const telemetryModule = globalThis.SmartQueryRouterTelemetry || null;
  const tokenCounterModule = globalThis.SmartQueryRouterTokenCounter || null;
  // Experimental flags: disabled by default and strictly locked to production mode
  const experimentalConfig = {
    enabled: false,
    environment: 'production'
  };
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
  const unitConversionModule = globalThis.SmartQueryRouterUnitConversion || null;
  const advancedCalculatorModule = globalThis.SmartQueryRouterAdvancedCalculator || null;
  const routingPolicyConfigModule = globalThis.SmartQueryRouterRoutingPolicyConfig || null;
  const taskClassifierModule = globalThis.SmartQueryRouterTaskClassifier || null;
  const complexityScorerConfigModule = globalThis.SmartQueryRouterComplexityScorerConfig || null;
  const complexityScorerModule = globalThis.SmartQueryRouterComplexityScorer || null;
  const complexityScorer = complexityScorerModule ? new complexityScorerModule.ComplexityScorer() : null;
  const routingPolicyModule = globalThis.SmartQueryRouterRoutingPolicy || null;
  const routingPolicy = routingPolicyModule ? routingPolicyModule.defaultRoutingPolicy : null;
  const userSettingsModule = globalThis.SmartQueryRouterUserSettings || null;
  const userSettingsManager = userSettingsModule ? userSettingsModule.defaultUserSettingsManager : null;
  if (userSettingsManager && typeof userSettingsManager.load === 'function') {
    userSettingsManager.load().catch(() => {});
  }
  const optimizerPipelineModule = globalThis.SmartQueryRouterOptimizerPipeline || null;
  const uiSubstitutionModule = globalThis.SmartQueryRouterUiSubstitution || null;
  const uiSubstitutor = uiSubstitutionModule
    ? new uiSubstitutionModule.SafeUiSubstitutor({
        normalizer: normalizerModule,
        logger,
        userSettingsManager
      })
    : null;

  const optimizerMetricsModule = globalThis.SmartQueryRouterMetrics || null;
  const metricsTracker = optimizerMetricsModule ? optimizerMetricsModule.defaultMetricsTracker : null;
  const RoutingReasonCode = optimizerMetricsModule ? optimizerMetricsModule.RoutingReasonCode : null;
  const statusSurfaceModule = globalThis.SmartQueryRouterStatusSurface || null;
  const statusSurfaceController = statusSurfaceModule ? statusSurfaceModule.defaultStatusSurface : null;

  const responseTrackerModule = globalThis.SmartQueryRouterResponseTracker || null;
  const {
    ResponseLifecycleState,
    FailureReason
  } = responseTrackerModule || {
    ResponseLifecycleState: {
      IDLE: 'IDLE',
      REQUEST_STARTED: 'REQUEST_STARTED',
      RESPONSE_STREAMING: 'RESPONSE_STREAMING',
      RESPONSE_COMPLETED: 'RESPONSE_COMPLETED',
      RESPONSE_FAILED: 'RESPONSE_FAILED'
    },
    FailureReason: { NONE: 'NONE' }
  };

  const outcomeFeedbackModule = globalThis.SmartQueryRouterOutcomeFeedback || null;
  const {
    FeedbackOutcomeType,
    FeedbackSource,
    UserRating,
    UserRejectionReason
  } = outcomeFeedbackModule || {
    FeedbackOutcomeType: {
      SUCCESSFUL_COMPLETION: 'SUCCESSFUL_COMPLETION',
      USER_REJECTION: 'USER_REJECTION',
      OPTIMIZATION_BYPASS: 'OPTIMIZATION_BYPASS',
      ESCALATION: 'ESCALATION',
      ERROR: 'ERROR'
    },
    FeedbackSource: {
      SYSTEM: 'SYSTEM',
      USER: 'USER'
    },
    UserRating: {
      POSITIVE: 'POSITIVE',
      NEGATIVE: 'NEGATIVE',
      NEUTRAL: 'NEUTRAL'
    },
    UserRejectionReason: {
      UNWANTED_REWRITE: 'UNWANTED_REWRITE',
      INCORRECT_ANSWER: 'INCORRECT_ANSWER',
      HIGH_LATENCY: 'HIGH_LATENCY',
      PREFER_ORIGINAL: 'PREFER_ORIGINAL',
      OTHER: 'OTHER'
    }
  };

  const feedbackUiModule = globalThis.SmartQueryRouterFeedbackUi || null;
  const feedbackUiController = feedbackUiModule
    ? new feedbackUiModule.FeedbackUiController({
        userSettingsManager,
        logger,
        onSubmitFeedback: (feedbackParams) => {
          submitUserFeedback(feedbackParams);
        }
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

  const responseTracker = responseTrackerModule
    ? new responseTrackerModule.ResponseStateTracker({
        logger,
        turnTracker,
        onStateChange: (newState, previousState, meta) => {
          // Record outcome feedback based on lifecycle transition
          if (outcomeFeedbackModule && typeof outcomeFeedbackModule.createOutcomeFeedback === 'function') {
            const routingMeta = latestTransientQueryEvent ? {
              coarseRoute: latestTransientQueryEvent.routing ? latestTransientQueryEvent.routing.route : null,
              modelRoute: latestTransientQueryEvent.backendOptimization ? latestTransientQueryEvent.backendOptimization.model_route : null,
              modelVersion: (latestTransientQueryEvent.backendOptimization && latestTransientQueryEvent.backendOptimization.execution_metadata)
                ? latestTransientQueryEvent.backendOptimization.execution_metadata.model_version
                : null,
              decisionType: latestTransientQueryEvent.backendOptimization ? latestTransientQueryEvent.backendOptimization.decision_type : null,
              taskCategory: latestTransientQueryEvent.taskClassification ? latestTransientQueryEvent.taskClassification.category : null,
              complexityLevel: (latestTransientQueryEvent.complexity && latestTransientQueryEvent.complexity.level)
                || (latestTransientQueryEvent.complexityScore && latestTransientQueryEvent.complexityScore.level)
                || null,
              cacheOutcome: latestTransientQueryEvent.backendOptimization ? latestTransientQueryEvent.backendOptimization.cache_outcome : null,
              ruleId: latestTransientQueryEvent.routing ? latestTransientQueryEvent.routing.ruleId : null,
              dryRun: Boolean(latestTransientQueryEvent.dryRunMode)
            } : {};

            if (newState === ResponseLifecycleState.RESPONSE_COMPLETED) {
              const compFeedback = outcomeFeedbackModule.createOutcomeFeedback({
                correlationId: (meta && meta.correlationId) || (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.correlationId : `corr_${Date.now()}`),
                requestId: (meta && meta.requestId) || (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null),
                outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
                source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
                routingMetadata: routingMeta,
                executionMetadata: {
                  durationMs: meta ? meta.durationMs : null,
                  failureReason: null
                }
              });
              if (msgProtocol && typeof msgProtocol.createOutcomeFeedbackMessage === 'function') {
                sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(compFeedback));
              }

              if (feedbackUiController) {
                feedbackUiController.notifyOptimization({
                  correlationId: compFeedback.correlationId,
                  requestId: compFeedback.requestId,
                  routingMetadata: routingMeta
                });
              }

              // Calculate and record tokens consumed for this completed Claude interaction
              if (metricsTracker) {
                const promptText = (latestTransientQueryEvent && latestTransientQueryEvent.content)
                  ? latestTransientQueryEvent.content.rawText
                  : '';
                const responseText = (meta && meta.rawResponseText) || '';
                const consumed = tokenCounterModule
                  ? tokenCounterModule.estimateConsumedTokens(promptText, responseText, turnTracker)
                  : { inputTokens: 0, outputTokens: 0, totalTokens: 0 };

                if (latestTransientQueryEvent && latestTransientQueryEvent._recordedActivity) {
                  latestTransientQueryEvent._recordedActivity.tokensConsumed = consumed.totalTokens;
                  if (consumed.totalTokens > 0) {
                    metricsTracker.totalTokensConsumed += consumed.totalTokens;
                    metricsTracker.persist();
                  }
                } else if (!latestTransientQueryEvent || !latestTransientQueryEvent._localAnswerResolved) {
                  const coarseRoute = (routingMeta && routingMeta.coarseRoute) || 'Claude Native Turn';
                  const compLevel = (routingMeta && routingMeta.complexityLevel) || '';
                  const activeOverride = userSettingsManager ? userSettingsManager.getRoutingOverride() : 'automatic';
                  const isHighComp = compLevel === 'HIGH' || compLevel === 'VERY_HIGH' || compLevel === 'COMPLEX';
                  const isStrong = (activeOverride === 'prefer-strong') ||
                    (activeOverride !== 'prefer-simple' && (
                      isHighComp ||
                      coarseRoute.toLowerCase().includes('strong') ||
                      coarseRoute.toLowerCase().includes('complex') ||
                      coarseRoute.toLowerCase().includes('evaluation')
                    ));

                  const finalRoute = (activeOverride === 'prefer-strong')
                    ? 'Complex Query'
                    : (activeOverride === 'prefer-simple')
                      ? 'Simple Query'
                      : (isStrong || coarseRoute.toLowerCase().includes('complex'))
                        ? 'Complex Query'
                        : (coarseRoute.toLowerCase().includes('simple') ? 'Simple Query' : coarseRoute);

                  let finalReasonCode = isStrong ? 'COMPLEX_TASK_SIGNAL' : 'SIMPLE_TASK_SIGNAL';
                  if (activeOverride && activeOverride !== 'automatic') {
                    finalReasonCode = 'USER_OVERRIDE';
                  }

                  metricsTracker.recordActivity({
                    route: finalRoute,
                    modelTier: isStrong ? 'strong' : 'simple',
                    cacheOutcome: (routingMeta && routingMeta.cacheOutcome) || 'NOT_CHECKED',
                    tokensSaved: 0,
                    tokensConsumed: consumed.totalTokens,
                    latencyMs: (meta && meta.durationMs) || 0,
                    status: 'COMPLETED',
                    diagnostics: buildRouteDiagnostics({
                      routeType: 'MODEL',
                      routing: {
                        coarseRoute: finalRoute,
                        reasonCode: finalReasonCode,
                        explanation: isStrong
                          ? 'Classified as complex query based on task complexity signals.'
                          : 'Classified as simple query based on standalone signals.'
                      }
                    })
                  });
                }
              }
            } else if (newState === ResponseLifecycleState.RESPONSE_FAILED) {
              const errFeedback = outcomeFeedbackModule.createOutcomeFeedback({
                correlationId: (meta && meta.correlationId) || (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.correlationId : `corr_${Date.now()}`),
                requestId: (meta && meta.requestId) || (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null),
                outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.ERROR,
                source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
                routingMetadata: routingMeta,
                executionMetadata: {
                  durationMs: meta ? meta.durationMs : null,
                  failureReason: (meta && meta.failureReason) || 'RESPONSE_FAILED'
                }
              });
              if (msgProtocol && typeof msgProtocol.createOutcomeFeedbackMessage === 'function') {
                sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(errFeedback));
              }
            }
          }

          if (newState === ResponseLifecycleState.RESPONSE_COMPLETED || newState === ResponseLifecycleState.RESPONSE_FAILED) {
            // Privacy guarantee: Do not persist conversation content longer than required.
            latestTransientQueryEvent = null;
          }
          if (msgProtocol && typeof msgProtocol.createResponseStateUpdateMessage === 'function') {
            const updateMsg = msgProtocol.createResponseStateUpdateMessage(newState, meta);
            sendTypedMessage(updateMsg);
          }
        }
      })
    : null;

  // Recover from potential aborted request due to page refresh
  if (responseTracker) {
    responseTracker.recoverFromSessionStorage();
  }

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
    if (unitConversionModule && unitConversionModule.unitConversionRule) {
      decisionEngine.registerRule(unitConversionModule.unitConversionRule);
    }
    if (advancedCalculatorModule && advancedCalculatorModule.advancedCalculatorRule) {
      decisionEngine.registerRule(advancedCalculatorModule.advancedCalculatorRule);
    }
  }

  const {
    MessageTypes,
    createInitMessage,
    createQueryObservedMessage,
    createOptimizeRequestMessage,
    createDryRunRecordMessage,
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
    let text = '';
    const pElements = editor.querySelectorAll ? editor.querySelectorAll('p') : null;
    if (pElements && pElements.length > 0) {
      const lines = [];
      pElements.forEach((p) => {
        lines.push(p.innerText || p.textContent || '');
      });
      text = lines.join(' ');
    } else {
      text = editor.innerText || editor.textContent || '';
    }
    return sanitizeQueryString(text);
  }

  // Detect active DOM attachments in Claude UI (attachment pills, upload previews, image thumbnails)
  function detectDomAttachments() {
    try {
      const candidates = [];

      // 1. Definite attachment remove buttons (only present on mounted attachment pills in prompt input)
      const removeBtnSelectors = [
        'button[aria-label*="Remove file" i]',
        'button[aria-label*="Remove attachment" i]',
        'button[aria-label*="Remove image" i]',
        'button[aria-label*="Remove" i][aria-label*="document" i]',
        'button[aria-label*="Remove" i][aria-label*="upload" i]',
        'button[aria-label*="Delete attachment" i]',
        'button[aria-label*="Delete file" i]'
      ];
      const removeButtons = document.querySelectorAll(removeBtnSelectors.join(', '));
      if (removeButtons && removeButtons.length > 0) {
        removeButtons.forEach((btn) => candidates.push(btn));
      }

      // 2. Definite preview containers, pills, and uploaded thumbnails
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
      const previewElements = document.querySelectorAll(previewSelectors.join(', '));
      if (previewElements && previewElements.length > 0) {
        previewElements.forEach((el) => {
          if (!candidates.includes(el)) candidates.push(el);
        });
      }

      // 3. File inputs that actually have files selected
      const fileInputs = document.querySelectorAll('input[type="file"]');
      if (fileInputs && fileInputs.length > 0) {
        fileInputs.forEach((inp) => {
          if (inp.files && inp.files.length > 0) {
            if (!candidates.includes(inp)) candidates.push(inp);
          }
        });
      }

      // 4. Fallback for custom attachment wrappers (e.g. [data-testid="file-upload"] in test harnesses)
      // Must NOT be an upload trigger button or empty file picker, and must contain an attached item or remove button
      const customContainers = document.querySelectorAll('[data-testid="file-upload"], [data-testid="attachment"]');
      if (customContainers && customContainers.length > 0) {
        customContainers.forEach((el) => {
          const testId = (el.getAttribute('data-testid') || '').toLowerCase();
          const aria = (el.getAttribute('aria-label') || '').toLowerCase();
          const isButton = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button';
          const isTrigger = testId.includes('button') || testId.includes('trigger') || testId.includes('menu');
          const isUploadAction = aria.includes('upload') || aria.includes('attach') || aria.includes('add');

          if (isButton && isUploadAction) return;
          if (isTrigger) return;

          // Check if this container has child remove buttons or preview elements
          const hasAttachedContent = el.querySelector && Boolean(
            el.querySelector('button[aria-label*="remove" i], button[aria-label*="delete" i], [data-testid*="preview"], img, .file-attachment')
          );
          if (hasAttachedContent) {
            const alreadyHasChild = candidates.some((c) => el === c || (el.contains && el.contains(c)));
            if (!alreadyHasChild) {
              candidates.push(el);
            }
          }
        });
      }

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
          } else {
            if (!types.includes('attachment')) types.push('attachment');
          }
        });
        if (types.length === 0) types.push('attachment');
      }

      return {
        hasAttachments,
        count: candidates.length,
        types
      };
    } catch (_) {
      return { hasAttachments: false, count: 0, types: [] };
    }
  }

  // 4. Observational Submission Detection
  // State guard to prevent double-counting within a single submission cycle
  let lastObservedTimestamp = 0;
  const SUBMIT_DEBOUNCE_WINDOW_MS = 600;

  // Transient in-memory reference to most recent query event (never persisted)
  let latestTransientQueryEvent = null;

  /**
   * Constructs a sanitized, developer-facing routing diagnostics record.
   * Explains route selection using standardized reason codes and internal signals.
   * STRICT PRIVACY GUARANTEE: Never includes raw user prompts or conversation text.
   * @param {object} [options]
   * @returns {object}
   */
  function buildRouteDiagnostics(options = {}) {
    const routing = options.routing || (latestTransientQueryEvent && latestTransientQueryEvent.routing) || null;
    const complexity = options.complexityScore || (latestTransientQueryEvent && latestTransientQueryEvent.complexityScore) || null;
    const decisionData = options.decisionData || null;
    const execMeta = decisionData && decisionData.execution_metadata ? decisionData.execution_metadata : null;
    const cacheOutcome = options.cacheOutcome || (decisionData ? decisionData.cache_outcome : 'MISS');
    const userOverride = options.userOverride || (latestTransientQueryEvent && latestTransientQueryEvent.userOverride) || 'automatic';

    const ReasonCode = RoutingReasonCode || {
      SIMPLE_TASK_SIGNAL: 'SIMPLE_TASK_SIGNAL',
      CONTEXT_DEPENDENCY: 'CONTEXT_DEPENDENCY',
      CACHE_HIT: 'CACHE_HIT',
      ESCALATION: 'ESCALATION',
      LOCAL_RULE_MATCH: 'LOCAL_RULE_MATCH',
      USER_OVERRIDE: 'USER_OVERRIDE',
      RICH_CONTENT_PRESERVATION: 'RICH_CONTENT_PRESERVATION',
      COMPLEX_TASK_SIGNAL: 'COMPLEX_TASK_SIGNAL'
    };

    let reasonCode = ReasonCode.SIMPLE_TASK_SIGNAL;
    let reasonExplanation = 'Query evaluated as self-contained with low complexity signals.';
    let signals = [];

    const routingReason = routing ? routing.reasonCode : null;
    const isCacheHit = cacheOutcome === 'HIT' || cacheOutcome === 'SEMANTIC_HIT';
    const escalationOccurred = execMeta ? Boolean(execMeta.escalation_occurred) : Boolean(options.escalationOccurred);

    if (isCacheHit) {
      reasonCode = ReasonCode.CACHE_HIT;
      reasonExplanation = 'Response served directly from cache without full query execution.';
    } else if (escalationOccurred) {
      reasonCode = ReasonCode.ESCALATION;
      const escReason = (execMeta && execMeta.escalation_reason) || options.escalationReason || 'Completeness check failed on initial route.';
      reasonExplanation = `Initial simple-query route escalated to complex query handling (${escReason}).`;
    } else if (userOverride && userOverride !== 'automatic') {
      reasonCode = ReasonCode.USER_OVERRIDE;
      reasonExplanation = `Route selected based on explicit user override preference (${userOverride}).`;
    } else if (routingReason === 'LOCAL_DETERMINISTIC_RULE_MATCH' || options.routeType === 'LOCAL') {
      reasonCode = ReasonCode.LOCAL_RULE_MATCH;
      reasonExplanation = 'Query handled directly by deterministic local rules (e.g. utility or formatting).';
    } else if (routingReason === 'CONTEXT_DEPENDENCY_DETECTED') {
      reasonCode = ReasonCode.CONTEXT_DEPENDENCY;
      reasonExplanation = 'Query requires prior conversation context, anaphoric reference, or turn history.';
    } else if (routingReason === 'COMPLEX_RICH_CONTENT' || routingReason === 'COMPLEX_ATTACHMENT_DEPENDENCY' || routingReason === 'COMPLEX_TABLE_DATA') {
      reasonCode = ReasonCode.RICH_CONTENT_PRESERVATION;
      reasonExplanation = 'Query contains rich content, attachments, or tables preserved on strong route.';
    } else if (routing && routing.reasonCode && routing.reasonCode.startsWith('COMPLEX_')) {
      reasonCode = ReasonCode.COMPLEX_TASK_SIGNAL;
      reasonExplanation = (routing && routing.explanation) || 'Code, technical keywords, or multi-factor complexity detected.';
    } else if (routing && (routing.route === 'complex-model candidate' || routing.route === 'needs-evaluation')) {
      reasonCode = ReasonCode.COMPLEX_TASK_SIGNAL;
      reasonExplanation = (routing && routing.explanation) || 'Code, reasoning cues, or structural complexity detected.';
    } else if (routing && routing.explanation) {
      reasonCode = ReasonCode.SIMPLE_TASK_SIGNAL;
      reasonExplanation = routing.explanation;
    }

    if (routing && Array.isArray(routing.matchedSignals)) {
      signals = routing.matchedSignals.slice(0, 8);
    } else if (routing && Array.isArray(routing.signals)) {
      signals = routing.signals.slice(0, 8);
    }

    let internalSignals = null;
    if (complexity) {
      internalSignals = {
        score: complexity.score,
        level: complexity.level,
        confidence: complexity.confidence,
        isSignalOnly: true,
        label: 'Internal Heuristic Signal',
        disclaimer: 'Indicative heuristic signal only; not an objective complexity measure',
        factorBreakdown: (complexity.breakdown && typeof complexity.breakdown === 'object')
          ? {
              length: (complexity.breakdown.length && complexity.breakdown.length.weighted) || 0,
              code: (complexity.breakdown.code && complexity.breakdown.code.weighted) || 0,
              list: (complexity.breakdown.listStructure && complexity.breakdown.listStructure.weighted) || 0,
              cues: (complexity.breakdown.cues && complexity.breakdown.cues.weighted) || 0,
              context: (complexity.breakdown.contextDependency && complexity.breakdown.contextDependency.weighted) || 0,
              task: (complexity.breakdown.taskType && complexity.breakdown.taskType.weighted) || 0
            }
          : null
      };
    }

    let escalationDetails = null;
    if (escalationOccurred) {
      escalationDetails = {
        evaluatorId: (execMeta && execMeta.evaluator_id) || options.evaluatorId || 'completeness_evaluator',
        completeness: (execMeta && execMeta.completeness_score !== undefined) ? execMeta.completeness_score : (options.completenessScore !== undefined ? options.completenessScore : null),
        detectedIssues: (execMeta && Array.isArray(execMeta.detected_issues)) ? execMeta.detected_issues : (Array.isArray(options.detectedIssues) ? options.detectedIssues : [])
      };
    }

    const taskCat = (latestTransientQueryEvent && latestTransientQueryEvent.taskClassification)
      ? latestTransientQueryEvent.taskClassification.category
      : null;

    return {
      reasonCode,
      reasonExplanation,
      taskCategory: taskCat,
      signals,
      internalSignals,
      escalationDetails
    };
  }

  function notifyQueryObserved(promptText, triggerSource, extraOptions = {}) {
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

    // Generate correlation ID spanning client observation and backend routing
    const correlationId = telemetryModule
      ? telemetryModule.generateCorrelationId()
      : `corr_${now}_${Math.random().toString(36).slice(2, 9)}`;

    const domAttachments = detectDomAttachments();

    // Construct the typed in-memory query event structure
    if (queryEventModule) {
      latestTransientQueryEvent = queryEventModule.createDetectedQueryEvent({
        rawPrompt: promptText,
        triggerType: triggerSource,
        context: safeContext,
        correlationId,
        domAttachments
      });
      if (extraOptions && extraOptions.substitution) {
        latestTransientQueryEvent.uiSubstitution = extraOptions.substitution;
        if (extraOptions.substitution.status === 'BYPASSED' && outcomeFeedbackModule && typeof outcomeFeedbackModule.createOutcomeFeedback === 'function') {
          const bypassFeedback = outcomeFeedbackModule.createOutcomeFeedback({
            correlationId,
            requestId: latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null,
            outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.OPTIMIZATION_BYPASS,
            source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
            routingMetadata: {
              dryRun: Boolean(latestTransientQueryEvent.dryRunMode)
            },
            executionMetadata: {
              substitutionStatus: 'BYPASSED',
              failureReason: extraOptions.substitution.reason || 'BYPASS'
            }
          });
          if (msgProtocol && typeof msgProtocol.createOutcomeFeedbackMessage === 'function') {
            sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(bypassFeedback));
          }
        } else if (extraOptions.substitution.status === 'APPLIED') {
          if (metricsTracker) {
            const origLen = extraOptions.substitution.originalLength || 0;
            const subLen = extraOptions.substitution.substitutedLength || 0;
            const savedTok = tokenCounterModule
              ? Math.max(0, tokenCounterModule.countTokens(promptText) - tokenCounterModule.countTokens(extraOptions.substitution.substitutedText || ''))
              : Math.max(0, Math.ceil((origLen - subLen) / 4));
            const diag = buildRouteDiagnostics({
              routeType: 'LOCAL',
              routing: {
                reasonCode: 'LOCAL_DETERMINISTIC_RULE_MATCH',
                explanation: 'Prompt normalized locally to optimize whitespace and formatting.',
                signals: ['PROMPT_NORMALIZATION_APPLIED']
              }
            });
            metricsTracker.recordActivity({
              route: 'Prompt Normalization',
              modelTier: 'local',
              cacheOutcome: 'NOT_CHECKED',
              tokensSaved: savedTok,
              tokensConsumed: 0,
              latencyMs: extraOptions.substitution.durationMs || 1,
              status: 'APPLIED',
              diagnostics: diag
            });
          }
          if (feedbackUiController) {
            feedbackUiController.notifyOptimization({
              correlationId,
              requestId: latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null,
              routingMetadata: { substitutionStatus: 'APPLIED' }
            });
          }
        }
      }

      // Content retention guard: auto-clear after transientEventTtlMs (default 60s)
      const retentionCfg = userSettingsManager && typeof userSettingsManager.getRetentionConfig === 'function'
        ? userSettingsManager.getRetentionConfig()
        : { transientEventTtlMs: 60000 };
      const currentEventId = latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null;
      setTimeout(() => {
        if (latestTransientQueryEvent && latestTransientQueryEvent.metadata && latestTransientQueryEvent.metadata.eventId === currentEventId) {
          latestTransientQueryEvent = null;
        }
      }, (retentionCfg && retentionCfg.transientEventTtlMs) || 60000);
    }

    // Rank recent conversation turns by relevance before recording current turn
    let contextRelevance = null;
    if (relevanceRankerModule && turnTracker && turnTracker.getTurnCount() > 0) {
      const contextDep = latestTransientQueryEvent ? latestTransientQueryEvent.contextDependency : null;
      contextRelevance = relevanceRankerModule.rankTurnsByRelevance(
        promptText,
        turnTracker.getRecentTurns(),
        { contextDependency: contextDep }
      );
      if (latestTransientQueryEvent) {
        latestTransientQueryEvent.contextRelevance = contextRelevance;
      }
    }

    // Decide context eligibility and produce internal candidate context package (does not modify Claude input)
    let candidateContextPackage = null;
    const isContextPruningActive = userSettingsManager && typeof userSettingsManager.isOptimizationCategoryEnabled === 'function'
      ? userSettingsManager.isOptimizationCategoryEnabled('contextPruning')
      : true;

    if (isContextPruningActive && contextPackagerModule && turnTracker && turnTracker.getTurnCount() > 0) {
      const contextDep = latestTransientQueryEvent ? latestTransientQueryEvent.contextDependency : null;
      candidateContextPackage = contextPackagerModule.buildCandidateContextPackage({
        queryText: promptText,
        recentTurns: turnTracker.getRecentTurns(),
        contextRelevance,
        contextDependency: contextDep
      });
      if (latestTransientQueryEvent) {
        latestTransientQueryEvent.candidateContextPackage = candidateContextPackage;
      }

      // Disabled-by-default experimental path: compare direct selection vs local summarization
      if (
        experimentalSummarizerModule &&
        candidateContextPackage &&
        candidateContextPackage.includedTurns.length > 0 &&
        experimentalSummarizerModule.isExperimentalSummarizationAllowed(experimentalConfig)
      ) {
        const expResult = experimentalSummarizerModule.evaluateExperimentalPath({
          directCandidatePackage: candidateContextPackage,
          config: experimentalConfig
        });
        if (expResult && logger) {
          logger.info(
            EventCategory.EXPERIMENTAL_METRICS,
            'Experimental context comparison metrics evaluated',
            expResult.telemetry
          );
        }
      }
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

    // Start response lifecycle state tracking (fail-open)
    if (responseTracker) {
      responseTracker.startRequest({
        requestId: latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null,
        correlationId,
        timestamp: now
      });
    }

    // Evaluate optimization decision interface (if localRules category is enabled)
    const isLocalRulesActive = userSettingsManager && typeof userSettingsManager.isOptimizationCategoryEnabled === 'function'
      ? userSettingsManager.isOptimizationCategoryEnabled('localRules')
      : true;

    if (isLocalRulesActive && decisionEngine && latestTransientQueryEvent) {
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

    // Compute indicative complexity score as an internal heuristic signal (never ground truth)
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

    // Deterministic Routing Policy: classify into coarse routes
    let routingClassification = null;
    const activeOverride = userSettingsManager ? userSettingsManager.getRoutingOverride() : 'automatic';
    if (routingPolicy && latestTransientQueryEvent) {
      routingClassification = routingPolicy.classify(latestTransientQueryEvent, { userOverride: activeOverride });
      latestTransientQueryEvent.routing = routingClassification;
      latestTransientQueryEvent.userOverride = activeOverride;

      if (logger) {
        logger.info(
          EventCategory.OPTIMIZATION_DECISION,
          'Deterministic coarse route classified',
          {
            route: routingClassification.route,
            ruleId: routingClassification.ruleId,
            reasonCode: routingClassification.reasonCode,
            confidence: routingClassification.confidence,
            userOverride: activeOverride
          }
        );
      }
    }

    // Produce sanitized summary safe for diagnostics/logging (zero raw prompt text)
    const safeSummary = (queryEventModule && latestTransientQueryEvent)
      ? queryEventModule.toSafeSummary(latestTransientQueryEvent)
      : { prompt_length: promptText.length, trigger: triggerSource, userOverride: activeOverride };

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

    // If dry-run mode is enabled (off by default), run the complete optimizer pipeline in dry-run mode
    const isDryRun = userSettingsManager && typeof userSettingsManager.isDryRunMode === 'function'
      ? userSettingsManager.isDryRunMode()
      : false;

    const isBackendRoutingEnabled = userSettingsManager && typeof userSettingsManager.isBackendEnabled === 'function'
      ? userSettingsManager.isBackendEnabled() && (
          typeof userSettingsManager.isOptimizationCategoryEnabled !== 'function' ||
          userSettingsManager.isOptimizationCategoryEnabled('backendRouting')
        )
      : true;

    if (isDryRun && optimizerPipelineModule && typeof optimizerPipelineModule.executeDryRunPipeline === 'function') {
      optimizerPipelineModule.executeDryRunPipeline({
        promptText,
        triggerSource,
        safeContext,
        turnTracker,
        userSettingsManager,
        deduplicator: null, // Already passed deduplicator earlier in notifyQueryObserved
        decisionEngine,
        routingPolicy,
        backendDispatcher: (queryPackage) => {
          return new Promise((resolve) => {
            if (!createOptimizeRequestMessage) {
              resolve(null);
              return;
            }
            const optMsg = createOptimizeRequestMessage(queryPackage);
            sendTypedMessage(optMsg, (response) => {
              resolve(response && response.data ? response.data : null);
            });
          });
        },
        logger,
        telemetry: telemetryModule,
        forceDryRun: true
      }).then((pipelineResult) => {
        if (pipelineResult && pipelineResult.proposedAction) {
          if (latestTransientQueryEvent) {
            latestTransientQueryEvent.proposedAction = pipelineResult.proposedAction;
            latestTransientQueryEvent.dryRunMode = true;
          }
          if (metricsTracker) {
            const act = pipelineResult.proposedAction;
            const diag = buildRouteDiagnostics({
              routing: act.routing,
              complexityScore: act.complexityScore || (latestTransientQueryEvent && latestTransientQueryEvent.complexityScore),
              cacheOutcome: (act.caching && act.caching.cacheOutcome) || 'NOT_CHECKED'
            });
            metricsTracker.recordActivity({
              route: (act.routing && act.routing.coarseRoute) || 'Dry Run Route',
              modelTier: (act.routing && act.routing.modelTier) || 'simple',
              cacheOutcome: (act.caching && act.caching.cacheOutcome) || 'NOT_CHECKED',
              tokensSaved: 0,
              latencyMs: (act.backend && act.backend.latencyMs) || 0,
              status: 'DRY_RUN',
              diagnostics: diag
            });
          }
          if (createDryRunRecordMessage) {
            sendTypedMessage(createDryRunRecordMessage(pipelineResult.proposedAction));
          }
        }
      }).catch((err) => {
        if (logger) {
          logger.warn(EventCategory.FAILURE, 'Dry-run pipeline execution error', { error: err.message });
        }
      });
    } else if (isBackendRoutingEnabled && createOptimizeRequestMessage) {
      // Standard non-dry-run path: asynchronously dispatch optimization package to backend via service worker (fail-open)
      const queryPackage = {
        request_id: (latestTransientQueryEvent && latestTransientQueryEvent.metadata)
          ? latestTransientQueryEvent.metadata.eventId
          : `req_${now}_${Math.random().toString(36).slice(2, 6)}`,
        correlation_id: correlationId,
        user_override: activeOverride,
        coarse_route: routingClassification ? routingClassification.route : null,
        task_category: (latestTransientQueryEvent && latestTransientQueryEvent.taskClassification)
          ? latestTransientQueryEvent.taskClassification.category
          : (routingClassification ? routingClassification.taskCategory : null),
        complexity_score: (latestTransientQueryEvent && latestTransientQueryEvent.complexity)
          ? latestTransientQueryEvent.complexity.score
          : null,
        complexity_level: (latestTransientQueryEvent && latestTransientQueryEvent.complexity)
          ? latestTransientQueryEvent.complexity.level
          : null,
        query_text: promptText,
        context_candidates: (candidateContextPackage && candidateContextPackage.includedTurns)
          ? candidateContextPackage.includedTurns.slice(0, 10).map((t) => ({
              turn_id: t.turnId,
              role: t.role,
              content: t.content,
              original_index: t.originalIndex,
              relevance_score: t.relevanceScore || 0.0,
              timestamp: t.timestamp
            }))
          : [],
        local_features: (latestTransientQueryEvent && latestTransientQueryEvent.features) ? {
          character_count: latestTransientQueryEvent.features.length ? latestTransientQueryEvent.features.length.characterCount : promptText.length,
          word_count: latestTransientQueryEvent.features.length ? latestTransientQueryEvent.features.length.wordCount : promptText.split(/\s+/).filter(Boolean).length,
          has_code: Boolean(latestTransientQueryEvent.features.code && latestTransientQueryEvent.features.code.hasCodeSyntax),
          has_math: Boolean(latestTransientQueryEvent.features.math && latestTransientQueryEvent.features.math.hasMathSymbols),
          has_questions: Boolean(latestTransientQueryEvent.features.questions && latestTransientQueryEvent.features.questions.questionCount > 0),
          has_urls: Boolean(latestTransientQueryEvent.features.urls && latestTransientQueryEvent.features.urls.hasUrl),
          is_normalized: Boolean(latestTransientQueryEvent.content && latestTransientQueryEvent.content.isNormalized),
          detected_cues: (latestTransientQueryEvent.features.cues && latestTransientQueryEvent.features.cues.detectedCues) || [],
          has_rich_input: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasRichInput),
          has_attachments: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasAttachments),
          has_images: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasImages),
          has_files: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasFiles),
          has_code_blocks: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasCodeBlocks),
          has_tables: Boolean(latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.hasTables),
          attachment_types: (latestTransientQueryEvent.features.richContent && latestTransientQueryEvent.features.richContent.types) || []
        } : null,
        client_metadata: {
          extension_version: '0.1.0',
          client_type: 'chrome_extension',
          schema_version: '1.0',
          hostname: (typeof window !== 'undefined' && window.location && window.location.hostname)
            ? String(window.location.hostname).replace(/[:/\\?#].*$/, '')
            : 'claude.ai'
        },
        // execute_route=false: The extension classifies queries and reports routing decisions.
        // Claude's website handles the actual AI model execution. Setting true would require
        // a separately configured LLM gateway with API keys.
        execute_route: false
      };

      const optMsg = createOptimizeRequestMessage(queryPackage);
      const clientStartMs = now;
      sendTypedMessage(optMsg, (response) => {
        const clientLatencyMs = Date.now() - clientStartMs;
        const decisionData = response && response.data ? response.data : null;
        if (latestTransientQueryEvent && decisionData) {
          latestTransientQueryEvent.backendOptimization = decisionData;
        }

        if (metricsTracker && decisionData) {
          const execMeta = decisionData.execution_metadata || null;
          const rawRoute = (execMeta && execMeta.route) || decisionData.coarse_route || '';
          const modelRoute = decisionData.model_route ||
            (execMeta && execMeta.model_id) ||
            decisionData.model_tier ||
            (decisionData.optimization_instructions && decisionData.optimization_instructions.suggested_model) ||
            '';

          // Determine effective route:
          // 1. User override takes absolute precedence ('prefer-strong' -> 'Complex Query', 'prefer-simple' -> 'Simple Query')
          // 2. Explicit backend route decision if available
          // 3. Robust on-device routing classification fallback (fail-open or local execution)
          const onDeviceRoute = routingClassification ? routingClassification.route : null;
          const compLevel = (latestTransientQueryEvent && latestTransientQueryEvent.complexity && latestTransientQueryEvent.complexity.level)
            || (latestTransientQueryEvent && latestTransientQueryEvent.complexityScore && latestTransientQueryEvent.complexityScore.level)
            || '';
          const isHighComplexity = compLevel === 'HIGH' || compLevel === 'VERY_HIGH' || compLevel === 'COMPLEX';

          let executedRoute;
          let isStrong = false;

          if (activeOverride === 'prefer-strong') {
            executedRoute = 'Complex Query';
            isStrong = true;
          } else if (activeOverride === 'prefer-simple') {
            executedRoute = 'Simple Query';
            isStrong = false;
          } else if (rawRoute.includes('complex') || modelRoute.includes('strong') || modelRoute.includes('complex')) {
            executedRoute = 'Complex Query';
            isStrong = true;
          } else if (rawRoute.includes('local') || rawRoute.includes('LOCAL')) {
            executedRoute = 'Local (On-Device)';
            isStrong = false;
          } else if (rawRoute.includes('simple') || rawRoute.includes('SIMPLE')) {
            executedRoute = 'Simple Query';
            isStrong = false;
          } else if (rawRoute.includes('evaluation')) {
            // Needs-evaluation conservatively defaults to Strong/Complex tier per gateway policy
            executedRoute = 'Complex Query';
            isStrong = true;
          } else {
            // Backend offline or returned fail-open with empty route:
            // Fall back to on-device deterministic routing classification
            if (onDeviceRoute === 'complex-model candidate' || isHighComplexity || onDeviceRoute === 'needs-evaluation') {
              executedRoute = 'Complex Query';
              isStrong = true;
            } else if (onDeviceRoute === 'local-eligible') {
              executedRoute = 'Local (On-Device)';
              isStrong = false;
            } else {
              executedRoute = 'Simple Query';
              isStrong = false;
            }
          }

          const cacheOutcome = decisionData.cache_outcome || 'MISS';
          const prunedCount = (candidateContextPackage && candidateContextPackage.metadata && candidateContextPackage.metadata.prunedTurnIds)
            ? candidateContextPackage.metadata.prunedTurnIds.length
            : 0;
          const promptToks = tokenCounterModule ? tokenCounterModule.countTokens(promptText) : Math.ceil(promptText.length / 4);
          const tokensSaved = (prunedCount * 35) + (cacheOutcome === 'HIT' ? (promptToks + 20) : 0);

          const diag = buildRouteDiagnostics({
            routing: routingClassification || (latestTransientQueryEvent && latestTransientQueryEvent.routing),
            complexityScore: complexityScore || (latestTransientQueryEvent && latestTransientQueryEvent.complexity),
            userOverride: activeOverride,
            decisionData,
            cacheOutcome
          });

          const recordedActivity = metricsTracker.recordActivity({
            route: executedRoute,
            modelTier: isStrong ? 'strong' : 'simple',
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

        // Construct telemetry performance record (strictly sanitized, zero raw query text)
        const isTelemetryActive = userSettingsManager && typeof userSettingsManager.isTelemetryEnabled === 'function'
          ? userSettingsManager.isTelemetryEnabled('performanceMetrics')
          : true;

        if (isTelemetryActive && telemetryModule && latestTransientQueryEvent) {
          const execMeta = decisionData && decisionData.execution_metadata ? decisionData.execution_metadata : null;
          const executedRoute = (execMeta && execMeta.route) || (decisionData && decisionData.coarse_route) || null;
          const modelVersion = execMeta ? execMeta.model_version : null;
          const failureCategory = execMeta ? execMeta.failure_category : 'NONE';
          const executionLatencyMs = execMeta ? execMeta.latency_ms : null;
          const escalationOccurred = execMeta ? Boolean(execMeta.escalation_occurred) : false;
          const escalationReason = execMeta ? execMeta.escalation_reason : null;

          if (escalationOccurred && outcomeFeedbackModule && typeof outcomeFeedbackModule.createOutcomeFeedback === 'function') {
            const escFeedback = outcomeFeedbackModule.createOutcomeFeedback({
              correlationId: (decisionData && decisionData.correlation_id) || correlationId,
              requestId: (latestTransientQueryEvent && latestTransientQueryEvent.metadata) ? latestTransientQueryEvent.metadata.eventId : null,
              outcomeType: outcomeFeedbackModule.FeedbackOutcomeType.ESCALATION,
              source: outcomeFeedbackModule.FeedbackSource.SYSTEM,
              routingMetadata: {
                coarseRoute: executedRoute,
                modelRoute: decisionData ? decisionData.model_route : null,
                modelVersion: modelVersion,
                escalationOccurred: true,
                escalationReason: escalationReason
              },
              executionMetadata: {
                durationMs: clientLatencyMs
              }
            });
            if (msgProtocol && typeof msgProtocol.createOutcomeFeedbackMessage === 'function') {
              sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(escFeedback));
            }
          }

          const perfRecord = telemetryModule.createPerformanceRecord({
            correlationId: (decisionData && decisionData.correlation_id) || correlationId,
            clientTimestamp: clientStartMs,
            backendTimestamp: decisionData ? decisionData.timestamp : null,
            decisionType: decisionData ? decisionData.decision_type : 'NO_OPTIMIZATION',
            coarseRoute: executedRoute,
            modelRoute: decisionData
              ? (decisionData.model_route ||
                (execMeta && execMeta.model_id) ||
                decisionData.model_tier ||
                (decisionData.optimization_instructions && decisionData.optimization_instructions.suggested_model) ||
                null)
              : null,
            modelVersion: modelVersion,
            cacheOutcome: (decisionData && decisionData.cache_outcome) || telemetryModule.CacheOutcome.NOT_CHECKED,
            latencyMs: clientLatencyMs,
            executionLatencyMs: executionLatencyMs,
            errorCategory: response && !response.success
              ? (response.errorCategory || telemetryModule.ErrorCategory.NETWORK_ERROR)
              : telemetryModule.ErrorCategory.NONE,
            failureCategory: failureCategory,
            escalationOccurred: escalationOccurred,
            escalationReason: escalationReason,
            localFeatures: latestTransientQueryEvent.features,
            candidateCount: (candidateContextPackage && candidateContextPackage.includedTurns)
              ? candidateContextPackage.includedTurns.length
              : 0,
            versionIdentifiers: {
              extension: '0.1.0',
              server: '0.1.0',
              schema: '1.0'
            },
            options: experimentalConfig // strictly production by default
          });
          latestTransientQueryEvent.performanceRecord = perfRecord;

          if (logger) {
            logger.info(
              EventCategory.BACKEND_CALL || 'BACKEND_CALL',
              'Optimization performance record recorded',
              {
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
              }
            );
          }
        }
      });
    }
  }

  /**
   * Evaluates if a prompt text is a deterministic query suitable for on-device answer resolution.
   * @param {string} text
   * @returns {{ canAnswerLocally: boolean, expression?: string, result?: any, ruleId?: string } | null}
   */
  function evaluateLocalAnswerCandidate(text) {
    if (!text || typeof text !== 'string') return null;
    const sanitized = sanitizeQueryString(text);
    if (!sanitized) return null;

    // 1. Evaluate Arithmetic (e.g. "5+2", "5 + 2", "what is 482 * 17?")
    if (arithmeticModule && typeof arithmeticModule.evaluateDeterministicArithmetic === 'function') {
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

    // 2. Evaluate Local Date/Time (e.g. "what time is it", "today's date", "days until Christmas", "days between Jan 1 and March 15", "date in 45 days")
    if (dateTimeModule && typeof dateTimeModule.classifyDateTimeQuery === 'function') {
      const dtClass = dateTimeModule.classifyDateTimeQuery(sanitized);
      if (dtClass && dtClass.eligible) {
        const now = new Date();
        let formattedResult = '';
        let expressionStr = sanitized;

        if (typeof dateTimeModule.evaluateDateTimeCalculation === 'function') {
          const calc = dateTimeModule.evaluateDateTimeCalculation(sanitized, now);
          if (calc && calc.success) {
            formattedResult = calc.result;
            expressionStr = calc.expression || sanitized;
          }
        }

        if (!formattedResult) {
          if (dtClass.category === 'CURRENT_TIME') {
            formattedResult = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          } else if (dtClass.category === 'CURRENT_DATE') {
            formattedResult = now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
          } else if (dtClass.category === 'CURRENT_DATETIME') {
            formattedResult = `${now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}, ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
          } else if (dtClass.category === 'CURRENT_DAY_OF_WEEK') {
            formattedResult = now.toLocaleDateString([], { weekday: 'long' });
          } else if (dtClass.category === 'CURRENT_YEAR') {
            formattedResult = String(now.getFullYear());
          } else if (dtClass.category === 'BROWSER_TIMEZONE') {
            formattedResult = dateTimeModule.getBrowserExposedTimezone ? dateTimeModule.getBrowserExposedTimezone() : 'Browser-Local';
          } else {
            formattedResult = now.toLocaleString();
          }
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

    // 3. Evaluate Advanced Calculations (percentages, financials, scientific, programmer/bitwise/CIDR, statistics, geometry)
    if (advancedCalculatorModule && typeof advancedCalculatorModule.evaluateAdvancedCalculation === 'function') {
      const adv = advancedCalculatorModule.evaluateAdvancedCalculation(sanitized);
      if (adv && adv.success) {
        return {
          canAnswerLocally: true,
          expression: adv.expression,
          result: adv.result,
          ruleId: advancedCalculatorModule.RULE_ID || 'RULE_LOCAL_DETERMINISTIC_ADVANCED_CALCULATOR',
          isMath: true
        };
      }
    }

    // 4. Evaluate Greetings and Pleasantries (e.g. "Hello", "Hi", "Thanks", "Good morning")
    // These are trivially handled on-device — no LLM tokens are needed.
    if (greetingModule && typeof greetingModule.classifyGreeting === 'function') {
      const greet = greetingModule.classifyGreeting(sanitized);
      if (greet && greet.isGreeting) {
        // Pick a canned on-device response based on greeting type
        const greetingType = greet.type || 'GREETING';
        const matched = (greet.matchedPhrase || sanitized).toLowerCase();

        let cannedResponse = '';
        if (greetingType === 'SIGN_OFF') {
          if (matched.startsWith('thank')) {
            cannedResponse = "You're welcome! 😊";
          } else {
            cannedResponse = 'Goodbye! Have a great day! 👋';
          }
        } else if (greetingType === 'PLEASANTRY') {
          if (matched.includes('what') && (matched.includes('up') || matched.includes('new'))) {
            cannedResponse = "Not much, just ready to help! What's on your mind today?";
          } else if (matched.includes('long time') || matched.includes('been a while')) {
            cannedResponse = "Good to see you! How can I help you today?";
          } else if (matched.includes('nice to meet') || matched.includes('good to see')) {
            cannedResponse = "Great to connect with you! How can I assist you today?";
          } else {
            cannedResponse = "I'm doing great, thanks for asking! How can I help you today?";
          }
        } else {
          // GREETING
          if (matched.includes('howdy')) {
            cannedResponse = 'Howdy! How can I assist you today? 🤠';
          } else if (matched.includes('morning')) {
            cannedResponse = 'Good morning! ☀️ How can I help you today?';
          } else if (matched.includes('afternoon')) {
            cannedResponse = 'Good afternoon! 🌤️ How can I help you today?';
          } else if (matched.includes('evening')) {
            cannedResponse = 'Good evening! 🌙 How can I help you today?';
          } else if (matched.includes('what') && (matched.includes('up') || matched.includes('new'))) {
            cannedResponse = "Not much, just ready to help! What's on your mind today?";
          } else if (matched.includes('good day')) {
            cannedResponse = 'Good day! How can I assist you today?';
          } else {
            cannedResponse = 'Hello! 👋 How can I help you today?';
          }
        }

        return {
          canAnswerLocally: true,
          expression: sanitized,
          result: cannedResponse,
          ruleId: greetingModule.RULE_ID || 'RULE_LOCAL_CONVERSATIONAL_GREETING',
          isMath: false
        };
      }
    }

    // 5. Evaluate Unit & Temperature Conversions (e.g. "100 F to C", "15 km to miles")
    if (unitConversionModule && typeof unitConversionModule.evaluateUnitConversion === 'function') {
      const conv = unitConversionModule.evaluateUnitConversion(sanitized);
      if (conv && conv.success) {
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

  /**
   * Helper to trigger native Claude submission when user clicks "Ask Claude Anyway"
   * @param {HTMLElement} editor
   * @param {string} text
   */
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
        altKey: true // bypass flag
      });
      editor.dispatchEvent(enterEvt);
    }
  }

  /**
   * Keyboard Trigger Observation:
   * Attaches to document using capture.
   * Cancels native submission ONLY if query is resolved locally on-device.
   */
  function handleKeyDown(event) {
    if (event.key !== 'Enter') return;
    if (event.shiftKey || event.ctrlKey || event.metaKey) return;
    if (event.isComposing) return; // IME composition in progress
    if (event._sqrHandled) return;

    // Verify event originated from or within a contenteditable prompt editor
    const target = event.target;
    if (!target) return;

    const elementTarget = target.nodeType === 3 ? target.parentElement : target;

    let editor = null;
    if (elementTarget && elementTarget.closest) {
      editor = elementTarget.closest('div[contenteditable="true"]') ||
        elementTarget.closest('[contenteditable="true"]') ||
        elementTarget.closest('.ProseMirror');
    }
    if (!editor) {
      const activePrompt = findPromptEditor();
      if (activePrompt && (activePrompt === document.activeElement || (activePrompt.contains && activePrompt.contains(elementTarget)))) {
        editor = activePrompt;
      }
    }

    if (!editor) return;

    let text = extractEditorText(editor);
    if (!text || text.length === 0) return;

    const isBypass = uiSubstitutor ? uiSubstitutor.isBypassTrigger(event) : false;

    // Check for on-device local resolution (e.g. arithmetic "5+2", datetime)
    const isLocalAnsweringActive = userSettingsManager && typeof userSettingsManager.isLocalAnsweringEnabled === 'function'
      ? (typeof userSettingsManager.isOptimizationEnabled !== 'function' || userSettingsManager.isOptimizationEnabled()) &&
        userSettingsManager.isLocalAnsweringEnabled()
      : true;

    if (isLocalAnsweringActive && !isBypass && localAnswerController) {
      const localCandidate = evaluateLocalAnswerCandidate(text);
      if (localCandidate && localCandidate.canAnswerLocally) {
        event._sqrHandled = true;
        if (typeof event.preventDefault === 'function') {
          event.preventDefault();
        }
        if (typeof event.stopPropagation === 'function') {
          event.stopPropagation();
        }
        if (typeof event.stopImmediatePropagation === 'function') {
          event.stopImmediatePropagation();
        }

        localAnswerController.showAnswer({
          expression: localCandidate.expression,
          result: localCandidate.result,
          ruleId: localCandidate.ruleId,
          rawQuery: text,
          isMath: localCandidate.isMath,
          editorElement: editor,
          onAskClaudeAnyway: () => {
            triggerNativeSubmission(editor, text);
          }
        });

        if (metricsTracker) {
          const diag = buildRouteDiagnostics({
            routeType: 'LOCAL',
            cacheOutcome: 'HIT'
          });
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

    // Least invasive supported mechanism: safe UI-level substitution
    // Single active behavior: semantics-preserving prompt normalization
    let substitutionResult = null;
    if (uiSubstitutor && typeof uiSubstitutor.applyPromptOptimization === 'function') {
      substitutionResult = uiSubstitutor.applyPromptOptimization(editor, {
        rawText: text,
        bypass: isBypass
      });
      if (substitutionResult && substitutionResult.status === 'APPLIED') {
        text = substitutionResult.substitutedText;
      }
    }

    notifyQueryObserved(text, 'keyboard_enter', { substitution: substitutionResult });
  }

  /**
   * Button Trigger Observation:
   * Attaches to document using capture.
   * Cancels native submission ONLY if query is resolved locally on-device.
   */
  function handleClick(event) {
    const target = event.target;
    if (!target) return;
    if (event._sqrHandled) return;

    const elementTarget = target.nodeType === 3 ? target.parentElement : target;
    if (!elementTarget || !elementTarget.closest) return;

    // Detect click on send button (aria-label containing send/prompt or submit button)
    const button = elementTarget.closest('button');
    if (!button || button.disabled) return;

    if (button._sqrBypass) return; // User explicitly clicked "Ask Claude Anyway"

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

    let text = extractEditorText(editor);
    if (!text || text.length === 0) return;

    const isBypass = uiSubstitutor ? uiSubstitutor.isBypassTrigger(event) : false;

    // Check for on-device local resolution
    const isLocalAnsweringActive = userSettingsManager && typeof userSettingsManager.isLocalAnsweringEnabled === 'function'
      ? (typeof userSettingsManager.isOptimizationEnabled !== 'function' || userSettingsManager.isOptimizationEnabled()) &&
        userSettingsManager.isLocalAnsweringEnabled()
      : true;

    if (isLocalAnsweringActive && !isBypass && localAnswerController) {
      const localCandidate = evaluateLocalAnswerCandidate(text);
      if (localCandidate && localCandidate.canAnswerLocally) {
        event._sqrHandled = true;
        if (typeof event.preventDefault === 'function') {
          event.preventDefault();
        }
        if (typeof event.stopPropagation === 'function') {
          event.stopPropagation();
        }
        if (typeof event.stopImmediatePropagation === 'function') {
          event.stopImmediatePropagation();
        }

        localAnswerController.showAnswer({
          expression: localCandidate.expression,
          result: localCandidate.result,
          ruleId: localCandidate.ruleId,
          rawQuery: text,
          isMath: localCandidate.isMath,
          editorElement: editor,
          onAskClaudeAnyway: () => {
            triggerNativeSubmission(editor, text);
          }
        });

        if (metricsTracker) {
          const diag = buildRouteDiagnostics({
            routeType: 'LOCAL',
            cacheOutcome: 'HIT'
          });
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

    // Least invasive supported mechanism: safe UI-level substitution
    let substitutionResult = null;
    if (uiSubstitutor && typeof uiSubstitutor.applyPromptOptimization === 'function') {
      substitutionResult = uiSubstitutor.applyPromptOptimization(editor, {
        rawText: text,
        bypass: isBypass
      });
      if (substitutionResult && substitutionResult.status === 'APPLIED') {
        text = substitutionResult.substitutedText;
      }
    }

    notifyQueryObserved(text, 'button_click', { substitution: substitutionResult });
  }

  // Attach global listeners (active cancellation permitted for local rules)
  window.addEventListener('keydown', handleKeyDown, { capture: true, passive: false });
  document.addEventListener('keydown', handleKeyDown, { capture: true, passive: false });
  window.addEventListener('click', handleClick, { capture: true, passive: false });
  document.addEventListener('click', handleClick, { capture: true, passive: false });

  // 5. Dynamic DOM & Route Change Monitoring
  let lastRecordedPath = window.location.pathname;

  // Passive observation of assistant turn from DOM when available and safe
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
      if (responseTracker) {
        responseTracker.handleNavigation(currentPath);
      }
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
    if (responseTracker) {
      responseTracker.handlePageUnload();
    }
    if (turnTracker) {
      turnTracker.clear();
    }
    if (logger) {
      logger.debug(EventCategory.PAGE_DETACH, 'Page detached from Claude view');
    }
    if (feedbackUiController) {
      feedbackUiController.hide();
    }
  }, { capture: true, once: true });
  /**
   * Internal programmatic interface for submitting user feedback
   * Prepares for a future optional user feedback control without adding intrusive UI prompts.
   * 
   * @param {object} options
   * @param {string} [options.correlationId]
   * @param {'POSITIVE'|'NEGATIVE'|'NEUTRAL'} [options.rating]
   * @param {string} [options.rejectionReason]
   * @param {string} [options.notes]
   * @param {object} [options.routingMetadata]
   * @returns {object|null}
   */
  function submitUserFeedback(options = {}) {
    if (!outcomeFeedbackModule || typeof outcomeFeedbackModule.createOutcomeFeedback !== 'function') {
      return null;
    }
    const correlationId = options.correlationId ||
      (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.correlationId : `corr_fb_${Date.now()}`);
    const isNegative = options.rating === outcomeFeedbackModule.UserRating.NEGATIVE || Boolean(options.rejectionReason);
    const outcomeType = isNegative
      ? outcomeFeedbackModule.FeedbackOutcomeType.USER_REJECTION
      : outcomeFeedbackModule.FeedbackOutcomeType.SUCCESSFUL_COMPLETION;

    const userFeedbackEvent = outcomeFeedbackModule.createOutcomeFeedback({
      correlationId,
      requestId: options.requestId || (latestTransientQueryEvent && latestTransientQueryEvent.metadata ? latestTransientQueryEvent.metadata.eventId : null),
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

    if (msgProtocol && typeof msgProtocol.createOutcomeFeedbackMessage === 'function') {
      sendTypedMessage(msgProtocol.createOutcomeFeedbackMessage(userFeedbackEvent));
    }
    return userFeedbackEvent;
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.__smartQueryRouterResponseTracker = responseTracker;
    globalThis.__smartQueryRouter_submitUserFeedback = submitUserFeedback;
    globalThis.__smartQueryRouterFeedbackUi = feedbackUiController;
  }
})();
