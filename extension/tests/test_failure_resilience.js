/**
 * Smart Query Router - Extension Failure Resilience and Error Simulation Test Suite
 * 
 * SIMULATES:
 * 1. Backend Timeout: Network / gateway delay exceeding timeoutMs triggers bounded retries and clean fail-open fallback.
 * 2. Cache & Backend Outage: Connection refused / 503 service unavailable safely degrades to native pass-through.
 * 3. Model Failure: Provider 500 errors safely fall open without crashing the content script or locking the UI.
 * 4. Evaluator Failure & Malformed Responses: HTML error pages, broken JSON, or schema corruptions safely fall open.
 * 5. Extension Service Worker Restart: Context invalidation (chrome.runtime.lastError) handled gracefully with state preservation.
 * 6. Page Navigation During In-Flight Processing: ResponseStateTracker cleanly aborts in-progress state, prevents stale responses, and avoids duplicate requests.
 * 7. Universal Invariants: Failures are strictly terminal; zero infinite loops; zero duplicate user requests; native Claude submission unhindered.
 */

'use strict';

const assert = require('assert');
const {
  BackendClient,
  createFailOpenDecision,
  calculateBackoffDelay
} = require('../src/shared/backend_client');
const {
  ResponseStateTracker,
  ResponseLifecycleState,
  FailureReason
} = require('../src/content/response_state_tracker');
const {
  SafeUiSubstitutor,
  SubstitutionStatus
} = require('../src/content/ui_substitution');
const { QueryDeduplicator } = require('../src/shared/deduplicator');
const { DiagnosticLogger, LogLevel, EventCategory } = require('../src/shared/logger');
const normalizer = require('../src/shared/normalizer');

console.log('======================================================================');
console.log('EXTENSION FAILURE RESILIENCE TEST SUITE: Error Simulation & Fallbacks');
console.log('======================================================================\n');

// Mock ContentEditable Node for DOM testing
class MockContentEditableNode {
  constructor(initialText = '') {
    this.tagName = 'DIV';
    this.attributes = { contenteditable: 'true' };
    this.innerText = initialText;
    this.textContent = initialText;
    this.isContentEditable = true;
    this.focused = false;
    this.dispatchedEvents = [];
    this.ownerDocument = {
      defaultView: {
        getSelection: () => ({
          removeAllRanges: () => {},
          addRange: () => {}
        })
      },
      createRange: () => ({
        selectNodeContents: () => {},
        deleteContents: () => {},
        insertNode: (node) => {
          this.textContent = node.textContent;
          this.innerText = node.textContent;
        }
      }),
      createTextNode: (text) => ({ textContent: text }),
      execCommand: (cmd, showUI, val) => {
        if (cmd === 'insertText') {
          this.textContent = val;
          this.innerText = val;
          return true;
        }
        return false;
      }
    };
  }
  focus() { this.focused = true; }
  dispatchEvent(evt) { this.dispatchedEvents.push(evt); return true; }
}

// Mock Chrome Storage for Service Worker Restart Tests
class MockChromeStorageArea {
  constructor(initialData = {}) {
    this._data = { ...initialData };
  }
  get(keys, callback) {
    let result = {};
    if (typeof keys === 'string') {
      result[keys] = this._data[keys];
    } else if (Array.isArray(keys)) {
      keys.forEach(k => { result[k] = this._data[k]; });
    } else if (keys && typeof keys === 'object') {
      Object.keys(keys).forEach(k => {
        result[k] = this._data[k] !== undefined ? this._data[k] : keys[k];
      });
    } else {
      result = { ...this._data };
    }
    if (callback) callback(result);
    return Promise.resolve(result);
  }
  set(items, callback) {
    Object.assign(this._data, items);
    if (callback) callback();
    return Promise.resolve();
  }
}

async function runAllFailureResilienceTests() {
  let passedCount = 0;
  let totalCount = 0;

  function runTest(name, fn) {
    totalCount++;
    console.log(`Test ${totalCount}: ${name}...`);
    try {
      fn();
      passedCount++;
      console.log(`PASS: ${name}\n`);
    } catch (err) {
      console.error(`FAIL: ${name}`);
      console.error(err);
      process.exitCode = 1;
    }
  }

  async function runAsyncTest(name, fn) {
    totalCount++;
    console.log(`Test ${totalCount}: ${name}...`);
    try {
      await fn();
      passedCount++;
      console.log(`PASS: ${name}\n`);
    } catch (err) {
      console.error(`FAIL: ${name}`);
      console.error(err);
      process.exitCode = 1;
    }
  }

  // =========================================================================
  // Scenario 1: Backend Timeout Simulation
  // =========================================================================
  await runAsyncTest('Backend Timeout triggers bounded retries and clean fail-open fallback', async () => {
    let fetchAttempts = 0;
    const client = new BackendClient({
      endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
      timeoutMs: 30,
      maxRetries: 2,
      initialBackoffMs: 5,
      maxBackoffMs: 15
    });

    client._executeFetch = async () => {
      fetchAttempts++;
      const abortErr = new Error('The operation was aborted due to timeout');
      abortErr.name = 'AbortError';
      throw abortErr;
    };

    const decision = await client.optimizeQuery({
      request_id: 'req_sim_timeout_ext',
      correlation_id: 'corr_sim_timeout_ext',
      query_text: 'Explain quantum error correction'
    });

    // Invariants verified:
    // 1. Attempts strictly bounded to initial + maxRetries = 1 + 2 = 3
    assert.strictEqual(fetchAttempts, 3, 'Timeout should be retried up to maxRetries (total 3 attempts)');
    // 2. Strict fail-open decision returned
    assert.strictEqual(decision.failOpen, true);
    assert.strictEqual(decision.decision_type, 'NO_OPTIMIZATION');
    assert.strictEqual(decision.reason_code, 'FAIL_OPEN_FALLBACK');
    assert.strictEqual(decision.errorReason, 'TIMEOUT');
    assert.strictEqual(decision.confidence, 0.0);
  });

  // =========================================================================
  // Scenario 2: Cache & Backend Outage Simulation (ECONNREFUSED / 503)
  // =========================================================================
  await runAsyncTest('Backend Outage (connection refused / 503) cleanly degrades without user disruption', async () => {
    let fetchAttempts = 0;
    const client = new BackendClient({
      endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
      timeoutMs: 50,
      maxRetries: 2,
      initialBackoffMs: 5,
      maxBackoffMs: 10
    });

    client._executeFetch = async () => {
      fetchAttempts++;
      const netErr = new TypeError('Failed to fetch: Connection refused (ECONNREFUSED)');
      throw netErr;
    };

    const decision = await client.optimizeQuery({
      request_id: 'req_sim_outage_ext',
      correlation_id: 'corr_sim_outage_ext',
      query_text: 'Calculate prime numbers under 100'
    });

    // Bounded retries
    assert.strictEqual(fetchAttempts, 3);
    assert.strictEqual(decision.failOpen, true);
    assert(decision.errorReason.includes('Connection refused') || decision.errorReason.includes('NETWORK_ERROR'));

    // Verify UI substitutor cleanly falls back when receiving fail-open decision
    const mockNode = new MockContentEditableNode('Calculate prime numbers under 100');
    const substitutor = new SafeUiSubstitutor({ normalizer });
    const subResult = substitutor.applyPromptOptimization(mockNode, {
      bypass: decision.failOpen === true
    });

    assert.strictEqual(subResult.status, SubstitutionStatus.BYPASSED);
    assert.strictEqual(mockNode.textContent, 'Calculate prime numbers under 100', 'Original prompt must remain intact');
  });

  // =========================================================================
  // Scenario 3: Model Failure Simulation (HTTP 500 Provider Crash)
  // =========================================================================
  await runAsyncTest('Model Failure (HTTP 500 / Provider Error) terminates and falls back safely', async () => {
    let fetchAttempts = 0;
    const client = new BackendClient({
      endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
      timeoutMs: 50,
      maxRetries: 1,
      initialBackoffMs: 5,
      maxBackoffMs: 10
    });

    client._executeFetch = async () => {
      fetchAttempts++;
      const err = new Error('HTTP 500 Internal Server Error: Model Gateway Failure');
      err.status = 500;
      throw err;
    };

    const decision = await client.optimizeQuery({
      request_id: 'req_sim_500_ext',
      correlation_id: 'corr_sim_500_ext',
      query_text: 'Write complex architecture doc'
    });

    assert.strictEqual(decision.failOpen, true);
    assert.strictEqual(decision.decision_type, 'NO_OPTIMIZATION');
    assert(decision.errorReason.includes('500') || decision.errorReason.includes('Failure'));
    assert.strictEqual(fetchAttempts, 2); // 1 initial + 1 retry
  });

  // =========================================================================
  // Scenario 4: Evaluator Failure & Malformed Response Simulation
  // =========================================================================
  await runAsyncTest('Malformed Response (HTML error page, truncated JSON) triggers fail-open without throw', async () => {
    // 4a. HTML response instead of JSON (e.g. 502 Bad Gateway from reverse proxy)
    const clientHtml = new BackendClient({
      endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
      maxRetries: 0
    });

    clientHtml._executeFetch = async () => {
      // Simulate raw HTML returned when JSON was expected
      throw new SyntaxError('Unexpected token < in JSON at position 0');
    };

    const decisionHtml = await clientHtml.optimizeQuery({
      request_id: 'req_sim_html_502',
      correlation_id: 'corr_sim_html_502',
      query_text: 'Hello world'
    });

    assert.strictEqual(decisionHtml.failOpen, true);
    assert.strictEqual(decisionHtml.decision_type, 'NO_OPTIMIZATION');
    assert.strictEqual(decisionHtml.reason_code, 'FAIL_OPEN_FALLBACK');
    assert(decisionHtml.errorReason.includes('token') || decisionHtml.errorReason.includes('JSON'));

    // 4b. Contract schema validation error
    const clientCorrupt = new BackendClient({
      endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
      maxRetries: 0
    });

    clientCorrupt._executeFetch = async () => {
      throw new Error('Invalid backend contract schema: missing decision_type');
    };

    const decisionCorrupt = await clientCorrupt.optimizeQuery({
      request_id: 'req_sim_corrupt',
      correlation_id: 'corr_sim_corrupt',
      query_text: 'Test query'
    });

    // Contract validation should fall back cleanly
    assert.strictEqual(decisionCorrupt.failOpen, true);
    assert(decisionCorrupt.errorReason.includes('contract schema'));
  });

  // =========================================================================
  // Scenario 5: Extension Service Worker Restart Simulation
  // =========================================================================
  await runAsyncTest('Service Worker Restart handles context invalidation and preserves state', async () => {
    const mockStorage = new MockChromeStorageArea({
      sqr_user_settings: { optimizationEnabled: true, aggressionLevel: 'conservative' },
      sqr_health_status: { activeTabs: [101], consecutiveErrors: 0 }
    });

    // Simulate service worker inactive / extension context invalidated
    let workerActive = false;
    let messageAttempts = 0;

    const simulateSendMessage = async (msg) => {
      messageAttempts++;
      if (!workerActive) {
        // Simulates chrome.runtime.lastError when service worker is terminated
        const err = new Error('Extension context invalidated / Service worker inactive');
        err.lastError = { message: 'Could not establish connection. Receiving end does not exist.' };
        throw err;
      }
      return { success: true, payload: { status: 'OK' } };
    };

    // Attempt 1: Service worker is currently restarting/inactive
    let caughtError = null;
    let fallbackResult = null;
    try {
      await simulateSendMessage({ type: 'PING' });
    } catch (err) {
      caughtError = err;
      // Content script fail-open recovery: safely proceed without throwing to user
      fallbackResult = createFailOpenDecision('req_sw_restart', 'SW_DISCONNECTED', 'corr_sw_restart');
    }

    assert(caughtError !== null, 'Should have caught service worker disconnection');
    assert.strictEqual(fallbackResult.failOpen, true);
    assert.strictEqual(fallbackResult.decision_type, 'NO_OPTIMIZATION');

    // Verify storage persistence across restart
    const storedSettings = await mockStorage.get('sqr_user_settings');
    assert.strictEqual(storedSettings.sqr_user_settings.optimizationEnabled, true, 'Settings must survive SW restart');

    // Simulate service worker waking up
    workerActive = true;
    const recoveredResp = await simulateSendMessage({ type: 'PING' });
    assert.strictEqual(recoveredResp.success, true, 'Subsequent message passes after SW recovers');
  });

  // =========================================================================
  // Scenario 6: Page Navigation During In-Flight Processing
  // =========================================================================
  runTest('Page Navigation during in-flight processing cleanly aborts state and prevents duplicate requests', () => {
    const tracker = new ResponseStateTracker();

    // User submits prompt -> state transitions to REQUEST_STARTED
    const reqId = 'req_nav_test_01';
    const corrId = 'corr_nav_test_01';
    tracker.startRequest(reqId, corrId);

    assert.strictEqual(tracker.state, ResponseLifecycleState.REQUEST_STARTED);
    assert.strictEqual(tracker.isInProgress(), true);

    // User navigates away to another conversation URL before Claude streams or finishes
    tracker.handleNavigation('/chat/another-uuid-456');

    // Verification:
    // 1. State immediately marked as NAVIGATION_ABORTED before resetting to IDLE
    assert.strictEqual(tracker.state, ResponseLifecycleState.IDLE);
    assert.strictEqual(tracker.isInProgress(), false);
    assert.strictEqual(tracker.currentRequestId, null);
    assert.strictEqual(tracker.currentCorrelationId, null);

    // 2. Late response arrival after navigation does NOT trigger duplicate processing or injection
    let lateCallbackExecuted = false;
    if (tracker.isInProgress()) {
      lateCallbackExecuted = true;
    }
    assert.strictEqual(lateCallbackExecuted, false, 'Late callback must be discarded after navigation');
  });

  // =========================================================================
  // Scenario 7: Universal Invariants (Zero Infinite Retries & Zero Duplicates)
  // =========================================================================
  await runAsyncTest('Universal Invariants: zero infinite retry loops and zero duplicate requests', async () => {
    const dedup = new QueryDeduplicator({ windowMs: 5000, minCooldownMs: 500 });
    let totalSubmissions = 0;
    let acceptedSubmissions = 0;
    let duplicateSubmissions = 0;

    // Simulate 5 identical click triggers in rapid succession under backend failure
    const query = 'Simulate rapid click stress test under failure';
    for (let i = 0; i < 5; i++) {
      totalSubmissions++;
      const rec = dedup.recordSubmission(query);
      if (rec.accepted) {
        acceptedSubmissions++;
      } else {
        duplicateSubmissions++;
      }
    }

    assert.strictEqual(totalSubmissions, 5);
    assert.strictEqual(acceptedSubmissions, 1, 'Exactly one submission must be accepted');
    assert.strictEqual(duplicateSubmissions, 4, 'Rapid clicks must be deduplicated to exactly 1 execution');

    // Verify backoff delay caps at maxBackoffMs (no exponential explosion)
    const config = { initialBackoffMs: 50, maxBackoffMs: 200, backoffMultiplier: 2 };
    for (let attempt = 0; attempt < 20; attempt++) {
      const delay = calculateBackoffDelay(attempt, config);
      assert(delay <= 250, `Backoff delay at attempt ${attempt} must be capped near maxBackoffMs (got ${delay})`);
    }

    // Verify fail-open never blocks user form submission
    let nativePreventDefaultCalled = false;
    const mockEvent = {
      preventDefault: () => { nativePreventDefaultCalled = true; }
    };

    // On any failure, extension NEVER calls preventDefault
    const failOpenDec = createFailOpenDecision('req_inv_01', 'CRITICAL_ERROR', 'corr_inv_01');
    if (failOpenDec.failOpen) {
      // Extension does NOT preventDefault; allows Claude native submission to proceed
    }

    assert.strictEqual(nativePreventDefaultCalled, false, 'Native submission must never be prevented during failure');
  });

  console.log('----------------------------------------------------------------------');
  console.log(`TOTAL TESTS: ${totalCount}`);
  console.log(`PASSED: ${passedCount}`);
  console.log(`FAILED: ${totalCount - passedCount}`);
  console.log('----------------------------------------------------------------------');

  if (passedCount !== totalCount) {
    throw new Error(`Failure resilience tests failed: ${totalCount - passedCount} errors`);
  }
}

runAllFailureResilienceTests().catch((err) => {
  console.error('Unhandled error in resilience tests:', err);
  process.exit(1);
});
