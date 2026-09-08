/**
 * Smart Query Router - Background Service Worker (Manifest V3)
 * Handles lifecycle, typed message passing, health tracking, and diagnostic logging.
 * Enforces quiet-by-default behavior and privacy redaction.
 */

// Import shared modules
importScripts('/src/shared/messages.js');
importScripts('/src/shared/logger.js');
importScripts('/src/shared/telemetry.js');
importScripts('/src/shared/task_classifier.js');
importScripts('/src/shared/complexity_scorer_config.js');
importScripts('/src/shared/complexity_scorer.js');
importScripts('/src/shared/privacy_config.js');
importScripts('/src/shared/outcome_feedback.js');
importScripts('/src/shared/user_settings.js');
importScripts('/src/shared/optimizer_metrics.js');
importScripts('/src/shared/routing_policy_config.js');
importScripts('/src/shared/routing_policy.js');
importScripts('/src/background/health_tracker.js');
importScripts('/src/shared/backend_client.js');
importScripts('/src/shared/optimizer_pipeline.js');

const EXTENSION_NAME = 'Smart Query Router';
const EXTENSION_VERSION = '0.1.0';

const metricsTracker = self.SmartQueryRouterMetrics
  ? self.SmartQueryRouterMetrics.defaultMetricsTracker
  : null;

const {
  MessageTypes,
  ErrorCodes,
  validateMessage,
  createSuccessResponse,
  createErrorResponse
} = self.SmartQueryRouterMessages;

const {
  EventCategory,
  defaultLogger: logger,
  redactIdentifier
} = self.SmartQueryRouterLogger;

const {
  HealthTracker
} = self.SmartQueryRouterHealth;

const {
  BackendClient
} = self.SmartQueryRouterBackendClient;

const healthTracker = new HealthTracker();
const backendClient = new BackendClient({ logger, storage: chrome.storage.local });

// Log startup event (recorded in ring buffer, quiet in console by default unless WARN/ERROR)
logger.info(EventCategory.STARTUP, 'Service worker active', { version: EXTENSION_VERSION });

// Persist initial health state
healthTracker.persistHealth(chrome.storage.local);

chrome.runtime.onInstalled.addListener((details) => {
  logger.info(EventCategory.STARTUP, 'Extension installed/updated', { reason: details.reason });
  healthTracker.persistHealth(chrome.storage.local);
});

// Tab removal listener (Page detach)
if (chrome.tabs && chrome.tabs.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    logger.info(EventCategory.PAGE_DETACH, 'Claude tab closed', { tabId: redactIdentifier(tabId) }, tabId);
    healthTracker.unregisterTab(tabId);
    healthTracker.persistHealth(chrome.storage.local);
  });
}

// Tab update listener (Navigation / page detach)
if (chrome.tabs && chrome.tabs.onUpdated) {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (changeInfo.status === 'loading') {
      logger.debug(EventCategory.PAGE_DETACH, 'Claude tab navigating', { tabId: redactIdentifier(tabId) }, tabId);
      healthTracker.markTabNavigating(tabId);
      healthTracker.persistHealth(chrome.storage.local);
    }
  });
}

// Typed message listener
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1. Validate structure and allowed vocabulary
  const validation = validateMessage(message);
  if (!validation.valid) {
    logger.warn(EventCategory.FAILURE, 'Rejected invalid message', {
      errorCode: validation.code,
      details: validation.error
    });
    sendResponse(createErrorResponse(validation.code, validation.error));
    return false;
  }

  const senderTabId = sender.tab ? sender.tab.id : null;

  // 2. Dispatch known message types
  switch (message.type) {
    case MessageTypes.INIT:
      if (typeof senderTabId === 'number') {
        logger.info(EventCategory.PAGE_ATTACH, 'Claude page attached', {
          tabId: redactIdentifier(senderTabId)
        }, senderTabId);
        healthTracker.registerTab(senderTabId, message.payload.origin);
        healthTracker.persistHealth(chrome.storage.local);
      }
      sendResponse(
        createSuccessResponse({
          initialized: true,
          version: EXTENSION_VERSION,
          runtime: 'SERVICE_WORKER'
        })
      );
      return false;

    case MessageTypes.STATUS_REQUEST:
      sendResponse(
        createSuccessResponse({
          status: 'READY',
          version: EXTENSION_VERSION,
          health: healthTracker.getHealthSummary(),
          metrics: metricsTracker ? metricsTracker.getMetricsSummary() : null
        })
      );
      return false;

    case MessageTypes.DIAGNOSTICS_REQUEST:
      // Expose health state, recent dry run actions, recent outcome feedback, metrics, and recent sanitized logs strictly to internal diagnostics
      sendResponse(
        createSuccessResponse({
          health: healthTracker.getHealthSummary(),
          metrics: metricsTracker ? metricsTracker.getMetricsSummary() : null,
          recentDryRunActions: healthTracker.getRecentDryRunActions(),
          recentOutcomeFeedback: healthTracker.getRecentOutcomeFeedback(),
          recentLogs: logger.getRecentLogs()
        })
      );
      return false;

    case MessageTypes.TEST_EVENT:
      sendResponse(
        createSuccessResponse({
          echoedTestId: message.payload.testId,
          receivedAt: message.timestamp
        })
      );
      return false;

    case MessageTypes.QUERY_OBSERVED:
      logger.info(
        EventCategory.QUERY_DETECTION,
        'Prompt submission observed',
        {
          prompt_length: message.payload.prompt_length,
          trigger: message.payload.trigger,
          tabId: redactIdentifier(senderTabId)
        },
        senderTabId
      );
      sendResponse(createSuccessResponse({ observed: true }));
      return false;

    case MessageTypes.DRY_RUN_RECORD:
      if (message.payload && message.payload.action) {
        healthTracker.recordDryRunAction(message.payload.action);
        logger.info(
          EventCategory.ROUTING_DECISION,
          'Dry-run proposed action recorded',
          {
            actionId: message.payload.action.actionId,
            taskType: message.payload.action.detection ? message.payload.action.detection.taskType : null,
            targetModel: message.payload.action.proposedRoute ? message.payload.action.proposedRoute.targetModel : null,
            backendCalled: message.payload.action.backendResponse ? message.payload.action.backendResponse.called : false,
            cacheOutcome: message.payload.action.cacheOutcome ? message.payload.action.cacheOutcome.status : null,
            dryRun: true,
            tabId: redactIdentifier(senderTabId)
          },
          senderTabId
        );
      }
      sendResponse(createSuccessResponse({ recorded: true }));
      return false;

    case MessageTypes.RESPONSE_STATE_UPDATE:
      logger.debug(
        EventCategory.QUERY_DETECTION,
        'Response state transition observed',
        {
          state: message.payload.state,
          correlationId: message.payload.correlationId,
          failureReason: message.payload.failureReason,
          durationMs: message.payload.durationMs,
          tabId: redactIdentifier(senderTabId)
        },
        senderTabId
      );
      sendResponse(createSuccessResponse({ recorded: true }));
      return false;

    case MessageTypes.OUTCOME_FEEDBACK:
      if (message.payload && message.payload.feedback) {
        healthTracker.recordOutcomeFeedback(message.payload.feedback);
        logger.info(
          EventCategory.ROUTING_DECISION,
          'Outcome feedback recorded',
          {
            feedbackId: message.payload.feedback.feedbackId,
            outcomeType: message.payload.feedback.outcomeType,
            source: message.payload.feedback.source,
            correlationId: message.payload.feedback.correlationId,
            tabId: redactIdentifier(senderTabId)
          },
          senderTabId
        );
      }
      sendResponse(createSuccessResponse({ recorded: true }));
      return false;

    case MessageTypes.OPTIMIZE_REQUEST:
      backendClient.optimizeQuery(message.payload.package)
        .then((decision) => {
          sendResponse(createSuccessResponse(decision));
        })
        .catch((err) => {
          sendResponse(createSuccessResponse(
            self.SmartQueryRouterBackendClient.createFailOpenDecision(
              message.payload.package ? message.payload.package.request_id : null,
              err.message,
              message.payload.package ? message.payload.package.correlation_id : null
            )
          ));
        });
      return true; // Keep channel open for async response

    default:
      sendResponse(createErrorResponse(ErrorCodes.UNKNOWN_MESSAGE_TYPE, 'Unknown message type'));
      return false;
  }
});
