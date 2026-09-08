/**
 * Smart Query Router - Response State Tracker
 * 
 * Non-intrusively tracks the lifecycle of Claude responses:
 * - REQUEST_STARTED: User submitted prompt, awaiting response or stream initiation.
 * - RESPONSE_STREAMING: Claude response is progressively streaming into the DOM.
 * - RESPONSE_COMPLETED: Streaming has finished cleanly; final response is stable.
 * - RESPONSE_FAILED: Request failed, errored, was cancelled, or was interrupted by navigation/refresh.
 * 
 * GUARANTEES:
 * 1. Strictly Non-Interfering: Read-only DOM inspection; NEVER modifies, blocks, or interrupts response rendering.
 * 2. Never Treat Partial Output as Final: Intermediate streaming text is strictly ignored.
 * 3. Zero Message Duplication: Completed responses are captured at most once per turn.
 * 4. Refresh & Navigation Resilience: Aborted in-flight requests cleanly transition to FAILED and reset to IDLE.
 * 5. Zero Privacy Leakage: Zero user prompt or response text stored in sessionStorage or logs.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterResponseTracker = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const ResponseLifecycleState = Object.freeze({
    IDLE: 'IDLE',
    REQUEST_STARTED: 'REQUEST_STARTED',
    RESPONSE_STREAMING: 'RESPONSE_STREAMING',
    RESPONSE_COMPLETED: 'RESPONSE_COMPLETED',
    RESPONSE_FAILED: 'RESPONSE_FAILED'
  });

  const FailureReason = Object.freeze({
    NONE: 'NONE',
    ERROR_BANNER: 'ERROR_BANNER',
    RETRY_PROMPT: 'RETRY_PROMPT',
    STREAM_ABORTED: 'STREAM_ABORTED',
    NAVIGATION_ABORTED: 'NAVIGATION_ABORTED',
    PAGE_REFRESH_ABORTED: 'PAGE_REFRESH_ABORTED',
    TIMEOUT: 'TIMEOUT',
    DOM_EXCEPTION: 'DOM_EXCEPTION'
  });

  const SESSION_STORAGE_KEY = 'smart_query_router_active_request';
  const DEFAULT_STREAM_TIMEOUT_MS = 120000; // 2 minutes max for streaming
  const DEFAULT_START_TIMEOUT_MS = 30000;   // 30 seconds max to start streaming

  class ResponseStateTracker {
    /**
     * @param {object} [options]
     * @param {object} [options.logger] - DiagnosticLogger instance
     * @param {object} [options.turnTracker] - RecentTurnsTracker instance
     * @param {Function} [options.onStateChange] - Callback (newState, previousState, metadata)
     * @param {object} [options.storage] - Storage interface (defaults to globalThis.sessionStorage)
     * @param {number} [options.streamTimeoutMs] - Max duration for streaming
     * @param {number} [options.startTimeoutMs] - Max duration to wait for stream start
     */
    constructor(options = {}) {
      this.logger = options.logger || null;
      this.turnTracker = options.turnTracker || null;
      this.onStateChange = typeof options.onStateChange === 'function' ? options.onStateChange : null;
      this.storage = options.storage !== undefined ? options.storage : (typeof sessionStorage !== 'undefined' ? sessionStorage : null);
      this.streamTimeoutMs = options.streamTimeoutMs || DEFAULT_STREAM_TIMEOUT_MS;
      this.startTimeoutMs = options.startTimeoutMs || DEFAULT_START_TIMEOUT_MS;

      // In-memory lifecycle state
      this.state = ResponseLifecycleState.IDLE;
      this.currentRequestId = null;
      this.currentCorrelationId = null;
      this.requestStartTime = 0;
      this.streamingStartTime = 0;
      this.lastUpdateTime = 0;
      this.failureReason = FailureReason.NONE;

      // Anti-duplication state
      this.lastCapturedTextHash = null;
      this.lastCapturedTurnTimestamp = 0;
      this.completedTurnsCount = 0;
    }

    /**
     * Safe sessionStorage reader
     * @private
     */
    _readSessionState() {
      if (!this.storage) return null;
      try {
        const raw = this.storage.getItem(SESSION_STORAGE_KEY);
        if (!raw) return null;
        return JSON.parse(raw);
      } catch (_) {
        return null;
      }
    }

    /**
     * Safe sessionStorage writer (strictly non-identifying metadata only)
     * @private
     */
    _saveSessionState() {
      if (!this.storage) return;
      try {
        if (this.state === ResponseLifecycleState.IDLE || this.state === ResponseLifecycleState.RESPONSE_COMPLETED) {
          this.storage.removeItem(SESSION_STORAGE_KEY);
          return;
        }

        const payload = {
          requestId: this.currentRequestId,
          correlationId: this.currentCorrelationId,
          state: this.state,
          requestStartTime: this.requestStartTime,
          streamingStartTime: this.streamingStartTime,
          lastUpdated: Date.now()
        };
        this.storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
      } catch (_) {
        // Fail-open: ignore storage write errors
      }
    }

    /**
     * Safe sessionStorage clearer
     * @private
     */
    _clearSessionState() {
      if (!this.storage) return;
      try {
        this.storage.removeItem(SESSION_STORAGE_KEY);
      } catch (_) {
        // Fail-open
      }
    }

    /**
     * Notify state change listener and logger
     * @private
     */
    _notifyStateChange(newState, previousState, metadata = {}) {
      if (this.logger) {
        this.logger.debug(
          'QUERY_DETECTION',
          `Response lifecycle transition: ${previousState} -> ${newState}`,
          {
            requestId: this.currentRequestId,
            correlationId: this.currentCorrelationId,
            state: newState,
            previousState,
            failureReason: this.failureReason,
            ...metadata
          }
        );
      }

      if (this.onStateChange) {
        try {
          this.onStateChange(newState, previousState, {
            requestId: this.currentRequestId,
            correlationId: this.currentCorrelationId,
            failureReason: this.failureReason,
            timestamp: Date.now(),
            ...metadata
          });
        } catch (_) {
          // Fail-open
        }
      }
    }

    /**
     * Check if a request is currently active (either started or streaming)
     * @returns {boolean}
     */
    isInProgress() {
      return this.state === ResponseLifecycleState.REQUEST_STARTED ||
        this.state === ResponseLifecycleState.RESPONSE_STREAMING;
    }

    /**
     * Check if response is actively streaming
     * @returns {boolean}
     */
    isStreaming() {
      return this.state === ResponseLifecycleState.RESPONSE_STREAMING;
    }

    /**
     * Get current lifecycle state
     * @returns {string}
     */
    getState() {
      return this.state;
    }

    /**
     * Get current failure reason if any
     * @returns {string}
     */
    getFailureReason() {
      return this.failureReason;
    }

    /**
     * Recover from sessionStorage on extension startup or page refresh.
     * If an in-flight request was left in REQUEST_STARTED or RESPONSE_STREAMING when the page refreshed,
     * it transitions to RESPONSE_FAILED (PAGE_REFRESH_ABORTED) and resets to IDLE cleanly.
     * @returns {boolean} True if an aborted request was recovered and finalized
     */
    recoverFromSessionStorage() {
      const persisted = this._readSessionState();
      if (!persisted) return false;

      const wasInFlight = persisted.state === ResponseLifecycleState.REQUEST_STARTED ||
        persisted.state === ResponseLifecycleState.RESPONSE_STREAMING;

      if (wasInFlight) {
        this.currentRequestId = persisted.requestId || null;
        this.currentCorrelationId = persisted.correlationId || null;
        this.requestStartTime = persisted.requestStartTime || Date.now();
        this.failureReason = FailureReason.PAGE_REFRESH_ABORTED;
        const prevState = persisted.state;
        this.state = ResponseLifecycleState.RESPONSE_FAILED;

        this._clearSessionState();
        this._notifyStateChange(
          ResponseLifecycleState.RESPONSE_FAILED,
          prevState,
          { recoveredFromRefresh: true, failureReason: FailureReason.PAGE_REFRESH_ABORTED }
        );

        // Reset to IDLE so new queries proceed cleanly
        this.state = ResponseLifecycleState.IDLE;
        this.failureReason = FailureReason.NONE;
        this.currentRequestId = null;
        this.currentCorrelationId = null;
        return true;
      }

      this._clearSessionState();
      return false;
    }

    /**
     * Initiate a new request lifecycle (called when user submits prompt via Enter or Send button)
     * @param {object} params
     * @param {string} [params.requestId]
     * @param {string} [params.correlationId]
     * @param {number} [params.timestamp]
     */
    startRequest({ requestId = null, correlationId = null, timestamp = Date.now() } = {}) {
      const prevState = this.state;

      // If previous request was still in-flight, mark it aborted
      if (this.isInProgress()) {
        this.failureReason = FailureReason.STREAM_ABORTED;
        this._notifyStateChange(ResponseLifecycleState.RESPONSE_FAILED, prevState, {
          reason: 'Interrupted by new request'
        });
      }

      this.state = ResponseLifecycleState.REQUEST_STARTED;
      this.currentRequestId = requestId || `req_${timestamp}_${Math.random().toString(36).slice(2, 6)}`;
      this.currentCorrelationId = correlationId || null;
      this.requestStartTime = timestamp;
      this.streamingStartTime = 0;
      this.lastUpdateTime = timestamp;
      this.failureReason = FailureReason.NONE;

      this._saveSessionState();
      this._notifyStateChange(ResponseLifecycleState.REQUEST_STARTED, prevState);
    }

    /**
     * Transition to RESPONSE_STREAMING (called when streaming indicators are detected)
     * @param {number} [timestamp]
     */
    markStreaming(timestamp = Date.now()) {
      if (this.state === ResponseLifecycleState.RESPONSE_STREAMING) {
        this.lastUpdateTime = timestamp;
        return;
      }

      const prevState = this.state;
      this.state = ResponseLifecycleState.RESPONSE_STREAMING;
      this.streamingStartTime = timestamp;
      this.lastUpdateTime = timestamp;
      this.failureReason = FailureReason.NONE;

      this._saveSessionState();
      this._notifyStateChange(ResponseLifecycleState.RESPONSE_STREAMING, prevState);
    }

    /**
     * Transition to RESPONSE_COMPLETED (called when streaming ends cleanly and final text is stable)
     * GUARANTEE: Never duplicates messages; captures assistant turn exactly once.
     * @param {object} params
     * @param {string} params.text - Final completed assistant response text
     * @param {string|null} [params.conversationId] - Conversation ID
     * @param {number} [params.timestamp]
     * @returns {boolean} True if completed turn was successfully recorded
     */
    markCompleted({ text, conversationId = null, timestamp = Date.now() }) {
      const cleanText = (typeof text === 'string' ? text : '').trim();
      const textSample = cleanText.slice(0, 60);
      const textHash = `${conversationId || 'curr'}_${cleanText.length}_${textSample}`;

      // Anti-duplication check: if identical response was completed within 2000ms, ignore duplicate
      if (this.lastCapturedTextHash === textHash && (timestamp - this.lastCapturedTurnTimestamp) < 2000) {
        return false;
      }

      const prevState = this.state;
      this.state = ResponseLifecycleState.RESPONSE_COMPLETED;
      this.lastUpdateTime = timestamp;
      this.failureReason = FailureReason.NONE;
      this.lastCapturedTextHash = textHash;
      this.lastCapturedTurnTimestamp = timestamp;
      this.completedTurnsCount += 1;

      const durationMs = this.requestStartTime > 0 ? (timestamp - this.requestStartTime) : 0;

      // Safely record turn in turnTracker (at most once!)
      let turnRecorded = false;
      if (this.turnTracker && cleanText.length > 0) {
        try {
          const res = this.turnTracker.recordTurn({
            role: 'assistant',
            text: cleanText,
            conversationId,
            timestamp
          });
          turnRecorded = Boolean(res);
        } catch (_) {
          // Fail-open
        }
      }

      this._clearSessionState();
      this._notifyStateChange(ResponseLifecycleState.RESPONSE_COMPLETED, prevState, {
        durationMs,
        turnRecorded,
        responseLength: cleanText.length
      });

      return true;
    }

    /**
     * Transition to RESPONSE_FAILED (called when error, cancellation, or abort occurs)
     * GUARANTEE: Discards any partial text; NEVER records partial output as a turn.
     * @param {string} [reason] - FailureReason code
     * @param {number} [timestamp]
     */
    markFailed(reason = FailureReason.STREAM_ABORTED, timestamp = Date.now()) {
      if (this.state === ResponseLifecycleState.RESPONSE_FAILED || this.state === ResponseLifecycleState.IDLE) {
        return;
      }

      const prevState = this.state;
      this.state = ResponseLifecycleState.RESPONSE_FAILED;
      this.failureReason = reason || FailureReason.STREAM_ABORTED;
      this.lastUpdateTime = timestamp;

      const durationMs = this.requestStartTime > 0 ? (timestamp - this.requestStartTime) : 0;

      this._clearSessionState();
      this._notifyStateChange(ResponseLifecycleState.RESPONSE_FAILED, prevState, {
        failureReason: this.failureReason,
        durationMs
      });
    }

    /**
     * Handle page navigation or conversation route change
     * If request is in progress, cleanly mark failed (NAVIGATION_ABORTED) and reset to IDLE.
     * @param {string} [newPath]
     */
    handleNavigation(newPath = null) {
      if (this.isInProgress()) {
        this.markFailed(FailureReason.NAVIGATION_ABORTED);
      }
      this.reset();
    }

    /**
     * Handle page unload (pagehide / beforeunload)
     */
    handlePageUnload() {
      if (this.isInProgress()) {
        // Keep active state in sessionStorage so recoverFromSessionStorage can detect refresh
        this._saveSessionState();
      }
    }

    /**
     * Reset tracker back to IDLE state
     */
    reset() {
      this.state = ResponseLifecycleState.IDLE;
      this.currentRequestId = null;
      this.currentCorrelationId = null;
      this.requestStartTime = 0;
      this.streamingStartTime = 0;
      this.lastUpdateTime = 0;
      this.failureReason = FailureReason.NONE;
      this._clearSessionState();
    }

    /**
     * Inspect DOM passively for streaming, error, and completion indicators.
     * Non-intrusive read-only DOM evaluation.
     * @param {Document|HTMLElement} rootDocument
     * @param {string|null} [conversationId]
     */
    processDomUpdate(rootDocument, conversationId = null) {
      if (!rootDocument) return;

      try {
        const now = Date.now();

        // 1. Check for active Error Banners
        const hasError = this._detectErrorBanner(rootDocument);
        if (hasError) {
          if (this.isInProgress()) {
            this.markFailed(FailureReason.ERROR_BANNER, now);
          }
          return;
        }

        // 2. Locate assistant nodes
        const assistantNodes = rootDocument.querySelectorAll(
          '[data-message-author-role="assistant"], .font-claude-message'
        );

        if (!assistantNodes || assistantNodes.length === 0) {
          // If in REQUEST_STARTED and waiting too long, check start timeout
          if (this.state === ResponseLifecycleState.REQUEST_STARTED) {
            if (now - this.requestStartTime > this.startTimeoutMs) {
              this.markFailed(FailureReason.TIMEOUT, now);
            }
          }
          return;
        }

        const latestNode = assistantNodes[assistantNodes.length - 1];

        // 3. Detect if currently actively streaming
        const isStreaming = this._detectIsStreaming(rootDocument, latestNode);

        if (isStreaming) {
          // Progressively streaming: update state to STREAMING
          // CRITICAL: NEVER capture or treat partial output as final!
          this.markStreaming(now);

          // Check stream timeout
          if (this.streamingStartTime > 0 && (now - this.streamingStartTime > this.streamTimeoutMs)) {
            this.markFailed(FailureReason.TIMEOUT, now);
          }
          return;
        }

        // 4. If was streaming or request started, and streaming has ceased:
        if (this.isInProgress()) {
          const text = (latestNode.innerText || latestNode.textContent || '').trim();
          if (text.length > 0) {
            this.markCompleted({
              text,
              conversationId,
              timestamp: now
            });
          }
        }
      } catch (err) {
        if (this.logger) {
          this.logger.debug('FAILURE', 'Error in processDomUpdate', { error: err.message });
        }
      }
    }

    /**
     * Detect if DOM indicates active streaming
     * @private
     */
    _detectIsStreaming(doc, latestAssistantNode) {
      if (!latestAssistantNode) return false;

      // Check explicit attribute
      if (latestAssistantNode.getAttribute('data-is-streaming') === 'true') {
        return true;
      }

      // Check for stop generation button in document
      // Claude replaces the send button with a stop button during streaming
      const stopButton = doc.querySelector(
        'button[aria-label*="Stop" i], button[data-testid="stop-button"], button svg[data-icon="square"]'
      );
      if (stopButton && !stopButton.disabled) {
        return true;
      }

      // Check for dynamic streaming indicator classes
      if (
        latestAssistantNode.classList &&
        (latestAssistantNode.classList.contains('streaming') ||
         latestAssistantNode.classList.contains('animate-pulse'))
      ) {
        return true;
      }

      const streamingChild = latestAssistantNode.querySelector &&
        latestAssistantNode.querySelector('.streaming, [data-is-streaming="true"], [data-testid="streaming-indicator"]');
      if (streamingChild) {
        return true;
      }

      return false;
    }

    /**
     * Detect if DOM indicates generation error or failure banner
     * @private
     */
    _detectErrorBanner(doc) {
      if (!doc || !doc.querySelector) return false;

      const errorNode = doc.querySelector(
        '[data-testid="error-message"], .bg-danger, [role="alert"]'
      );
      if (errorNode) {
        const txt = (errorNode.textContent || '').toLowerCase();
        if (
          txt.includes('error generating') ||
          txt.includes('unable to respond') ||
          txt.includes('failed to generate') ||
          txt.includes('something went wrong')
        ) {
          return true;
        }
      }

      // Check for retry button
      const retryBtn = doc.querySelector(
        'button[aria-label*="Retry" i], button[aria-label*="Try again" i]'
      );
      if (retryBtn) {
        return true;
      }

      return false;
    }
  }

  return {
    ResponseLifecycleState,
    FailureReason,
    SESSION_STORAGE_KEY,
    ResponseStateTracker
  };
});
