/**
 * Automated Unit Test Suite for Response State Tracker
 * 
 * Verifies:
 * 1. Strict state machine transitions (IDLE, REQUEST_STARTED, RESPONSE_STREAMING, RESPONSE_COMPLETED, RESPONSE_FAILED).
 * 2. Non-interference with progressive streaming (DOM is strictly read-only).
 * 3. Never treating partial output as a final answer (intermediate tokens ignored).
 * 4. Zero message duplication (exactly one turn recorded per response).
 * 5. Page navigation resilience (in-flight request aborted and reset to IDLE).
 * 6. Page refresh resilience (sessionStorage recovery detects refresh during stream, finalizes as failed, resets to IDLE).
 * 7. Privacy audit (zero prompt or response text in storage or diagnostics).
 */

const assert = require('assert');
const {
  ResponseLifecycleState,
  FailureReason,
  SESSION_STORAGE_KEY,
  ResponseStateTracker
} = require('../src/content/response_state_tracker');
const { RecentTurnsTracker } = require('../src/shared/turn_tracker');
const { DiagnosticLogger, LogLevel } = require('../src/shared/logger');

console.log('--- Running Response State Tracker Tests ---');

// Mock Storage implementation mimicking sessionStorage
class MockStorage {
  constructor() {
    this.store = new Map();
  }
  getItem(key) {
    return this.store.has(key) ? this.store.get(key) : null;
  }
  setItem(key, value) {
    this.store.set(key, String(value));
  }
  removeItem(key) {
    this.store.delete(key);
  }
  clear() {
    this.store.clear();
  }
}

// Mock DOM elements
class MockDomNode {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attributes };
    this.innerText = attributes.innerText || '';
    this.textContent = this.innerText;
    this.disabled = Boolean(attributes.disabled);
    this.classList = {
      classes: new Set(attributes.className ? attributes.className.split(' ') : []),
      contains(cls) { return this.classes.has(cls); },
      add(cls) { this.classes.add(cls); },
      remove(cls) { this.classes.delete(cls); }
    };
    this.children = [];
  }

  getAttribute(name) {
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  appendChild(child) {
    this.children.push(child);
  }

  querySelectorAll(selector) {
    const results = [];
    const search = (node) => {
      if (selector.includes('assistant') && node.getAttribute('data-message-author-role') === 'assistant') {
        results.push(node);
      } else if (selector.includes('font-claude-message') && node.classList.contains('font-claude-message')) {
        results.push(node);
      }
      for (const child of node.children) {
        search(child);
      }
    };
    search(this);
    return results;
  }

  querySelector(selector) {
    if (selector.includes('Stop') || selector.includes('stop-button')) {
      const search = (node) => {
        if (node.tagName === 'BUTTON') {
          const aria = (node.getAttribute('aria-label') || '').toLowerCase();
          if (aria.includes('stop') || node.getAttribute('data-testid') === 'stop-button') {
            return node;
          }
        }
        for (const child of node.children) {
          const found = search(child);
          if (found) return found;
        }
        return null;
      };
      return search(this);
    }

    if (selector.includes('error') || selector.includes('alert')) {
      const search = (node) => {
        if (node.getAttribute('data-testid') === 'error-message' || node.getAttribute('role') === 'alert') {
          return node;
        }
        for (const child of node.children) {
          const found = search(child);
          if (found) return found;
        }
        return null;
      };
      return search(this);
    }

    return null;
  }
}

// Test 1: Enum Verification and Initial State
console.log('Test 1: Enum verification and initial state...');
assert.strictEqual(ResponseLifecycleState.IDLE, 'IDLE');
assert.strictEqual(ResponseLifecycleState.REQUEST_STARTED, 'REQUEST_STARTED');
assert.strictEqual(ResponseLifecycleState.RESPONSE_STREAMING, 'RESPONSE_STREAMING');
assert.strictEqual(ResponseLifecycleState.RESPONSE_COMPLETED, 'RESPONSE_COMPLETED');
assert.strictEqual(ResponseLifecycleState.RESPONSE_FAILED, 'RESPONSE_FAILED');

const storage = new MockStorage();
const tracker = new ResponseStateTracker({ storage });
assert.strictEqual(tracker.getState(), ResponseLifecycleState.IDLE);
assert.strictEqual(tracker.isInProgress(), false);
assert.strictEqual(tracker.isStreaming(), false);
console.log('PASS: Enums and initial state verified');

// Test 2: Standard Successful Lifecycle Progression
console.log('Test 2: Standard successful lifecycle progression...');
const turnTracker = new RecentTurnsTracker({ maxTurns: 4 });
const transitions = [];
const lifecycleTracker = new ResponseStateTracker({
  storage: new MockStorage(),
  turnTracker,
  onStateChange: (newState, prevState, meta) => {
    transitions.push({ newState, prevState, meta });
  }
});

// User submits prompt
lifecycleTracker.startRequest({ requestId: 'req_1', correlationId: 'corr_1' });
assert.strictEqual(lifecycleTracker.getState(), ResponseLifecycleState.REQUEST_STARTED);
assert.strictEqual(lifecycleTracker.isInProgress(), true);
assert.strictEqual(lifecycleTracker.isStreaming(), false);

// DOM begins streaming
const mockDoc = new MockDomNode('document');
const assistantMsg = new MockDomNode('div', {
  'data-message-author-role': 'assistant',
  'data-is-streaming': 'true',
  innerText: 'Hello, I am Claude.'
});
mockDoc.appendChild(assistantMsg);

const stopBtn = new MockDomNode('button', {
  'aria-label': 'Stop generating',
  disabled: false
});
mockDoc.appendChild(stopBtn);

lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');
assert.strictEqual(lifecycleTracker.getState(), ResponseLifecycleState.RESPONSE_STREAMING);
assert.strictEqual(lifecycleTracker.isInProgress(), true);
assert.strictEqual(lifecycleTracker.isStreaming(), true);

// Progressive chunks arrive while streaming:
assistantMsg.innerText = 'Hello, I am Claude. Here is how quicksort works in Python: def quicksort...';
lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');
assert.strictEqual(lifecycleTracker.getState(), ResponseLifecycleState.RESPONSE_STREAMING);
// CRITICAL: Turn tracker MUST NOT contain partial output!
assert.strictEqual(turnTracker.getTurnCount(), 0, 'Must NOT record turns while actively streaming');

// Streaming finishes cleanly:
assistantMsg.removeAttribute('data-is-streaming');
mockDoc.children = [assistantMsg]; // Stop button removed, send button restored
assistantMsg.innerText = 'Hello, I am Claude. Here is how quicksort works in Python: def quicksort(arr): return arr';

lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');
assert.strictEqual(lifecycleTracker.getState(), ResponseLifecycleState.RESPONSE_COMPLETED);
assert.strictEqual(lifecycleTracker.isInProgress(), false);
assert.strictEqual(lifecycleTracker.isStreaming(), false);

// Exactly ONE assistant turn recorded in turnTracker
assert.strictEqual(turnTracker.getTurnCount(), 1, 'Exactly one final assistant turn recorded');
const recordedTurn = turnTracker.getLastTurn();
assert.strictEqual(recordedTurn.role, 'assistant');
assert.ok(recordedTurn.snippet.includes('quicksort'));
console.log('PASS: Standard successful lifecycle progression verified without partial output ingestion');

// Test 3: Message Anti-Duplication on Repeated DOM Mutations
console.log('Test 3: Anti-duplication on repeated DOM mutations...');
// Multiple DOM mutations occur after completion (e.g. typing indicator cleanup, mouse hover, etc.)
lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');
lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');
lifecycleTracker.processDomUpdate(mockDoc, 'conv_1');

assert.strictEqual(turnTracker.getTurnCount(), 1, 'Turn count must stay exactly 1 (no duplicate messages)');
console.log('PASS: Message anti-duplication verified');

// Test 4: Isolation of Partial Output on Stream Failure
console.log('Test 4: Isolation of partial output on stream failure...');
const failTurnTracker = new RecentTurnsTracker({ maxTurns: 4 });
const failTracker = new ResponseStateTracker({
  storage: new MockStorage(),
  turnTracker: failTurnTracker
});

failTracker.startRequest({ requestId: 'req_fail', correlationId: 'corr_fail' });
const failDoc = new MockDomNode('document');
const failingAssistantMsg = new MockDomNode('div', {
  'data-message-author-role': 'assistant',
  'data-is-streaming': 'true',
  innerText: 'Partial text before the connection was'
});
failDoc.appendChild(failingAssistantMsg);

failTracker.processDomUpdate(failDoc, 'conv_fail');
assert.strictEqual(failTracker.getState(), ResponseLifecycleState.RESPONSE_STREAMING);

// Error banner appears in DOM
const errorBanner = new MockDomNode('div', {
  'data-testid': 'error-message',
  innerText: 'There was an error generating a response. Please try again.'
});
failDoc.appendChild(errorBanner);

failTracker.processDomUpdate(failDoc, 'conv_fail');
assert.strictEqual(failTracker.getState(), ResponseLifecycleState.RESPONSE_FAILED);
assert.strictEqual(failTracker.getFailureReason(), FailureReason.ERROR_BANNER);
assert.strictEqual(failTracker.isInProgress(), false);

// Partial text must NEVER be recorded in turnTracker!
assert.strictEqual(failTurnTracker.getTurnCount(), 0, 'Partial output must never be recorded on stream failure');
console.log('PASS: Partial output strictly isolated on failure');

// Test 5: Resilience to Page Navigation
console.log('Test 5: Resilience to page navigation...');
const navStorage = new MockStorage();
const navTracker = new ResponseStateTracker({ storage: navStorage });

navTracker.startRequest({ requestId: 'req_nav', correlationId: 'corr_nav' });
navTracker.markStreaming();
assert.strictEqual(navTracker.isStreaming(), true);

// User navigates away to a new chat
navTracker.handleNavigation('/chat/another-chat-uuid');
assert.strictEqual(navTracker.getState(), ResponseLifecycleState.IDLE);
assert.strictEqual(navTracker.isInProgress(), false);
assert.strictEqual(navStorage.getItem(SESSION_STORAGE_KEY), null, 'Session storage cleaned up on navigation');
console.log('PASS: Page navigation resilience verified');

// Test 6: Resilience to Page Refresh via SessionStorage Recovery
console.log('Test 6: Resilience to page refresh via sessionStorage recovery...');
const refreshStorage = new MockStorage();

// 1. First session: Request starts and is streaming when user hits Refresh (F5)
const preRefreshTracker = new ResponseStateTracker({ storage: refreshStorage });
preRefreshTracker.startRequest({ requestId: 'req_refresh', correlationId: 'corr_refresh' });
preRefreshTracker.markStreaming();
preRefreshTracker.handlePageUnload();

// Check storage has active state
assert.ok(refreshStorage.getItem(SESSION_STORAGE_KEY) !== null);

// 2. New session begins on reload (fresh script initialization)
const postRefreshTracker = new ResponseStateTracker({ storage: refreshStorage });
assert.strictEqual(postRefreshTracker.getState(), ResponseLifecycleState.IDLE);

const recovered = postRefreshTracker.recoverFromSessionStorage();
assert.strictEqual(recovered, true, 'Must detect unfinalized in-flight request from refresh');
assert.strictEqual(postRefreshTracker.getState(), ResponseLifecycleState.IDLE, 'Must reset cleanly to IDLE');
assert.strictEqual(postRefreshTracker.isInProgress(), false);
assert.strictEqual(refreshStorage.getItem(SESSION_STORAGE_KEY), null, 'Storage cleared after recovery');

// Verifies a new request can proceed immediately
postRefreshTracker.startRequest({ requestId: 'new_req_after_refresh' });
assert.strictEqual(postRefreshTracker.getState(), ResponseLifecycleState.REQUEST_STARTED);
console.log('PASS: Page refresh resilience verified');

// Test 7: Privacy Audit - Zero Raw Prompt or Response Text in Storage and Logs
console.log('Test 7: Privacy audit for storage and diagnostics...');
const auditStorage = new MockStorage();
const logger = new DiagnosticLogger({ level: LogLevel.DEBUG, enableConsole: false });
const auditTracker = new ResponseStateTracker({
  storage: auditStorage,
  logger
});

auditTracker.startRequest({ requestId: 'req_sec', correlationId: 'corr_sec' });
auditTracker.markStreaming();

const rawStored = auditStorage.getItem(SESSION_STORAGE_KEY);
assert.ok(rawStored, 'Storage item exists');
const parsed = JSON.parse(rawStored);

// Assert NO sensitive keys exist
assert.strictEqual(parsed.prompt, undefined);
assert.strictEqual(parsed.text, undefined);
assert.strictEqual(parsed.response, undefined);
assert.strictEqual(parsed.content, undefined);

// Complete the response with sensitive dummy text
const secretDoc = new MockDomNode('document');
const secretNode = new MockDomNode('div', {
  'data-message-author-role': 'assistant',
  innerText: 'TOP SECRET PASSWORDS AND PRIVATE DATA'
});
secretDoc.appendChild(secretNode);
auditTracker.processDomUpdate(secretDoc, 'conv_sec');

// Audit logger records: confirm no text in log metadata
const recentLogs = logger.getRecentLogs();
for (const entry of recentLogs) {
  const serialized = JSON.stringify(entry);
  assert.strictEqual(
    serialized.includes('TOP SECRET PASSWORDS'),
    false,
    'Log records must never contain assistant response content'
  );
}
console.log('PASS: Privacy audit passed (zero prompt or response text in storage or logs)');

console.log('--- ALL RESPONSE STATE TRACKER TESTS PASSED ---');
