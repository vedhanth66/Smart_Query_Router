/**
 * Smart Query Router - Typed Message Protocol
 * Defines the strict, minimal message vocabulary and validation logic
 * shared between the page content script and background service worker.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    // Node.js test environment
    module.exports = factory();
  } else {
    // Browser / Service Worker global environment
    root.SmartQueryRouterMessages = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Allowed message types vocabulary (strictly closed set)
  const MessageTypes = Object.freeze({
    INIT: 'ROUTER_INIT',
    STATUS_REQUEST: 'ROUTER_STATUS_REQUEST',
    TEST_EVENT: 'ROUTER_TEST_EVENT',
    HEALTH_CHECK: 'ROUTER_HEALTH_CHECK',
    DIAGNOSTICS_REQUEST: 'ROUTER_DIAGNOSTICS_REQUEST',
    QUERY_OBSERVED: 'ROUTER_QUERY_OBSERVED',
    OPTIMIZE_REQUEST: 'ROUTER_OPTIMIZE_REQUEST',
    DRY_RUN_RECORD: 'ROUTER_DRY_RUN_RECORD',
    RESPONSE_STATE_UPDATE: 'ROUTER_RESPONSE_STATE_UPDATE',
    OUTCOME_FEEDBACK: 'ROUTER_OUTCOME_FEEDBACK'
  });

  const ErrorCodes = Object.freeze({
    INVALID_MESSAGE_STRUCTURE: 'INVALID_MESSAGE_STRUCTURE',
    UNKNOWN_MESSAGE_TYPE: 'UNKNOWN_MESSAGE_TYPE',
    INVALID_PAYLOAD: 'INVALID_PAYLOAD',
    HEALTH_CHECK_TIMEOUT: 'HEALTH_CHECK_TIMEOUT',
    TAB_DISCONNECTED: 'TAB_DISCONNECTED'
  });

  /**
   * Validate incoming message structure and type against strict vocabulary
   * @param {any} message
   * @returns {{ valid: boolean, error?: string, code?: string }}
   */
  function validateMessage(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message)) {
      return {
        valid: false,
        code: ErrorCodes.INVALID_MESSAGE_STRUCTURE,
        error: 'Message must be a non-null object'
      };
    }

    const { type, payload, timestamp } = message;

    if (typeof type !== 'string') {
      return {
        valid: false,
        code: ErrorCodes.INVALID_MESSAGE_STRUCTURE,
        error: 'Message type must be a string'
      };
    }

    // Verify type belongs to allowed vocabulary
    const allowedTypes = Object.values(MessageTypes);
    if (!allowedTypes.includes(type)) {
      return {
        valid: false,
        code: ErrorCodes.UNKNOWN_MESSAGE_TYPE,
        error: `Unknown message type: "${type}". Allowed types: ${allowedTypes.join(', ')}`
      };
    }

    if (typeof timestamp !== 'number' || !Number.isFinite(timestamp)) {
      return {
        valid: false,
        code: ErrorCodes.INVALID_PAYLOAD,
        error: 'Message timestamp must be a valid numeric timestamp'
      };
    }

    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return {
        valid: false,
        code: ErrorCodes.INVALID_PAYLOAD,
        error: 'Message payload must be a non-null object'
      };
    }

    // Type-specific payload validation
    switch (type) {
      case MessageTypes.INIT:
        if (typeof payload.origin !== 'string' || !payload.origin) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'INIT payload requires non-empty string "origin"'
          };
        }
        break;

      case MessageTypes.STATUS_REQUEST:
      case MessageTypes.HEALTH_CHECK:
      case MessageTypes.DIAGNOSTICS_REQUEST:
        // No required payload properties, payload can be empty object
        break;

      case MessageTypes.TEST_EVENT:
        if (typeof payload.testId !== 'string' || !payload.testId) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'TEST_EVENT payload requires non-empty string "testId"'
          };
        }
        break;

      case MessageTypes.QUERY_OBSERVED:
        if (typeof payload.prompt_length !== 'number' || payload.prompt_length < 0) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'QUERY_OBSERVED requires non-negative numeric "prompt_length"'
          };
        }
        break;

      case MessageTypes.OPTIMIZE_REQUEST:
        if (!payload.package || typeof payload.package !== 'object') {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'OPTIMIZE_REQUEST requires non-null object "package"'
          };
        }
        break;

      case MessageTypes.DRY_RUN_RECORD:
        if (!payload.action || typeof payload.action !== 'object') {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'DRY_RUN_RECORD requires non-null object "action"'
          };
        }
        break;

      case MessageTypes.RESPONSE_STATE_UPDATE:
        if (typeof payload.state !== 'string' || !payload.state) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'RESPONSE_STATE_UPDATE requires non-empty string "state"'
          };
        }
        break;

      case MessageTypes.OUTCOME_FEEDBACK:
        if (!payload.feedback || typeof payload.feedback !== 'object' || Array.isArray(payload.feedback)) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'OUTCOME_FEEDBACK requires non-null object "feedback"'
          };
        }
        if (typeof payload.feedback.correlationId !== 'string' || !payload.feedback.correlationId) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'OUTCOME_FEEDBACK requires string "correlationId" in feedback'
          };
        }
        if (typeof payload.feedback.outcomeType !== 'string' || !payload.feedback.outcomeType) {
          return {
            valid: false,
            code: ErrorCodes.INVALID_PAYLOAD,
            error: 'OUTCOME_FEEDBACK requires string "outcomeType" in feedback'
          };
        }
        break;
    }

    return { valid: true };
  }

  // Factory functions for creating strictly valid outgoing messages
  function createInitMessage(origin) {
    return {
      type: MessageTypes.INIT,
      timestamp: Date.now(),
      payload: { origin: origin || 'unknown' }
    };
  }

  function createStatusRequestMessage() {
    return {
      type: MessageTypes.STATUS_REQUEST,
      timestamp: Date.now(),
      payload: {}
    };
  }

  function createTestEventMessage(testId) {
    return {
      type: MessageTypes.TEST_EVENT,
      timestamp: Date.now(),
      payload: { testId: String(testId) }
    };
  }

  function createHealthCheckMessage() {
    return {
      type: MessageTypes.HEALTH_CHECK,
      timestamp: Date.now(),
      payload: {}
    };
  }

  function createDiagnosticsRequestMessage() {
    return {
      type: MessageTypes.DIAGNOSTICS_REQUEST,
      timestamp: Date.now(),
      payload: {}
    };
  }

  function createQueryObservedMessage(promptLength, triggerType) {
    return {
      type: MessageTypes.QUERY_OBSERVED,
      timestamp: Date.now(),
      payload: {
        prompt_length: typeof promptLength === 'number' ? promptLength : 0,
        trigger: triggerType || 'keyboard'
      }
    };
  }

  function createOptimizeRequestMessage(queryPackage) {
    return {
      type: MessageTypes.OPTIMIZE_REQUEST,
      timestamp: Date.now(),
      payload: { package: queryPackage || {} }
    };
  }

  function createDryRunRecordMessage(action) {
    return {
      type: MessageTypes.DRY_RUN_RECORD,
      timestamp: Date.now(),
      payload: { action: action || {} }
    };
  }

  function createResponseStateUpdateMessage(state, metadata) {
    return {
      type: MessageTypes.RESPONSE_STATE_UPDATE,
      timestamp: Date.now(),
      payload: {
        state: String(state),
        requestId: (metadata && metadata.requestId) ? String(metadata.requestId) : null,
        correlationId: (metadata && metadata.correlationId) ? String(metadata.correlationId) : null,
        failureReason: (metadata && metadata.failureReason) ? String(metadata.failureReason) : null,
        durationMs: (metadata && typeof metadata.durationMs === 'number') ? metadata.durationMs : null
      }
    };
  }

  function createOutcomeFeedbackMessage(feedback) {
    return {
      type: MessageTypes.OUTCOME_FEEDBACK,
      timestamp: Date.now(),
      payload: { feedback: feedback || {} }
    };
  }

  // Helper response creators
  function createSuccessResponse(data) {
    return {
      success: true,
      data: data || {},
      timestamp: Date.now()
    };
  }

  function createErrorResponse(code, error) {
    return {
      success: false,
      code: code || ErrorCodes.INVALID_PAYLOAD,
      error: error || 'An error occurred processing the message',
      timestamp: Date.now()
    };
  }

  return {
    MessageTypes,
    ErrorCodes,
    validateMessage,
    createInitMessage,
    createStatusRequestMessage,
    createTestEventMessage,
    createHealthCheckMessage,
    createDiagnosticsRequestMessage,
    createQueryObservedMessage,
    createOptimizeRequestMessage,
    createDryRunRecordMessage,
    createResponseStateUpdateMessage,
    createOutcomeFeedbackMessage,
    createSuccessResponse,
    createErrorResponse
  };
});
