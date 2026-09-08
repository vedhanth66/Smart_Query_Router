/**
 * Automated Unit Test Suite for Background Health Tracker
 * Tests tab registration, timeout handling, graceful recovery, and storage persistence.
 */

const assert = require('assert');
const { HealthTracker, HEALTH_STORAGE_KEY } = require('../src/background/health_tracker');
const messages = require('../src/shared/messages');

console.log('--- Running Health Tracker Tests ---');

// Test 1: Initial state is STANDBY with 0 tabs
console.log('Test 1: Initial state verification...');
const tracker = new HealthTracker();
let summary = tracker.getHealthSummary();
assert.strictEqual(summary.status, 'STANDBY');
assert.strictEqual(summary.connectedTabsCount, 0);
assert.strictEqual(summary.serviceWorkerActive, true);
console.log('PASS: Initial state is STANDBY');

// Test 2: Tab registration updates status to HEALTHY
console.log('Test 2: Tab registration...');
summary = tracker.registerTab(101, 'https://claude.ai');
assert.strictEqual(summary.status, 'HEALTHY');
assert.strictEqual(summary.connectedTabsCount, 1);
assert.strictEqual(summary.activeTabs[0].tabId, 101);
assert.strictEqual(summary.activeTabs[0].origin, 'https://claude.ai');
console.log('PASS: Registered tab updates health to HEALTHY');

// Test 3: Tab navigation marks tab disconnected (STANDBY)
console.log('Test 3: Graceful recovery on tab navigation...');
summary = tracker.markTabNavigating(101);
assert.strictEqual(summary.status, 'STANDBY');
assert.strictEqual(summary.connectedTabsCount, 0);

// Re-register tab on page reload
summary = tracker.registerTab(101, 'https://claude.ai');
assert.strictEqual(summary.status, 'HEALTHY');
assert.strictEqual(summary.connectedTabsCount, 1);
console.log('PASS: Navigation and re-registration cycle verified');

// Test 4: Tab closure unregisters tab
console.log('Test 4: Tab closure unregistering...');
summary = tracker.unregisterTab(101);
assert.strictEqual(summary.status, 'STANDBY');
assert.strictEqual(summary.connectedTabsCount, 0);
console.log('PASS: Unregister tab removes tab from active registry');

// Test 5: Tab health check with timeout and graceful recovery
console.log('Test 5: Tab health check timeout handling...');
(async function runAsyncTests() {
  const timeoutTracker = new HealthTracker();
  timeoutTracker.registerTab(202, 'https://claude.ai');

  // Simulated hung tab that never replies
  const hungSendFn = (tabId, msg, cb) => {
    // Intentionally never call cb() to trigger timeout
  };

  const timeoutResult = await timeoutTracker.checkTabHealth(202, hungSendFn, 50);
  assert.strictEqual(timeoutResult.healthy, false);
  assert.strictEqual(timeoutResult.error, messages.ErrorCodes.HEALTH_CHECK_TIMEOUT);

  // Verify graceful recovery: tab marked disconnected
  const afterTimeoutSummary = timeoutTracker.getHealthSummary();
  assert.strictEqual(afterTimeoutSummary.connectedTabsCount, 0);
  assert.strictEqual(afterTimeoutSummary.status, 'STANDBY');
  console.log('PASS: Hung tab timed out gracefully and marked disconnected');

  // Test 6: Successful health check
  console.log('Test 6: Successful responsive tab health check...');
  const responsiveSendFn = (tabId, msg, cb) => {
    assert.strictEqual(msg.type, messages.MessageTypes.HEALTH_CHECK);
    setTimeout(() => {
      cb(messages.createSuccessResponse({ active: true, hostname: 'claude.ai' }));
    }, 10);
  };

  const successResult = await timeoutTracker.checkTabHealth(202, responsiveSendFn, 100);
  assert.strictEqual(successResult.healthy, true);
  assert.strictEqual(successResult.tabId, 202);
  const recoveredSummary = timeoutTracker.getHealthSummary();
  assert.strictEqual(recoveredSummary.connectedTabsCount, 1);
  assert.strictEqual(recoveredSummary.status, 'HEALTHY');
  console.log('PASS: Responsive tab confirmed healthy');

  // Test 7: Persistence to storage
  console.log('Test 7: Internal diagnostics storage persistence...');
  let storedData = null;
  const mockStorage = {
    set: (data, callback) => {
      storedData = data;
      if (callback) callback();
    }
  };

  await timeoutTracker.persistHealth(mockStorage);
  assert(storedData !== null, 'Data was persisted');
  assert(storedData[HEALTH_STORAGE_KEY] !== undefined, 'Stored under router_health key');
  assert.strictEqual(storedData[HEALTH_STORAGE_KEY].status, 'HEALTHY');
  assert.strictEqual(storedData[HEALTH_STORAGE_KEY].connectedTabsCount, 1);
  console.log('PASS: Health state persisted to storage without private user data');

  // Test 8: Recording dry-run actions
  console.log('Test 8: Recording dry-run proposed actions...');
  const dtTracker = new HealthTracker();
  assert.strictEqual(dtTracker.getHealthSummary().dryRunActionsCount, 0);
  assert.strictEqual(dtTracker.getRecentDryRunActions().length, 0);

  const sampleAction = {
    actionId: 'dry_123',
    mode: 'DRY_RUN',
    safetyGuarantees: { userVisibleBehaviorAltered: false }
  };
  dtTracker.recordDryRunAction(sampleAction);
  assert.strictEqual(dtTracker.getHealthSummary().dryRunActionsCount, 1);
  const actions = dtTracker.getRecentDryRunActions();
  assert.strictEqual(actions.length, 1);
  assert.strictEqual(actions[0].actionId, 'dry_123');
  console.log('PASS: Dry-run actions recorded and bounded in HealthTracker');

  console.log('--- ALL HEALTH TRACKER TESTS PASSED ---');
})();
