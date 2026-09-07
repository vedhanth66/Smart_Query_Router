/**
 * Smart Query Router - Unit Tests for Backend Client
 * 
 * Verifies:
 * 1. Secure transport validation (requires https:// unless 127.0.0.1/localhost)
 * 2. Exponential backoff delay calculation with jitter
 * 3. Strict fail-open decision structure
 * 4. Successful call handling
 * 5. Timeout abort and fail-open recovery
 * 6. Bounded retries and backoff
 * 7. Non-retry of 4xx client errors
 * 8. Privacy audit: Zero raw query text or prompt strings logged on failure
 * 9. Storage-backed dynamic endpoint resolution
 */

const assert = require('assert');
const {
  DEFAULT_CONFIG,
  validateEndpoint,
  calculateBackoffDelay,
  createFailOpenDecision,
  BackendClient
} = require('../src/shared/backend_client');

console.log('--- Running Backend Client & Fail-Open Tests ---');

// Test 1: Secure transport validation
console.log('Test 1: Secure transport validation...');
assert.strictEqual(validateEndpoint('https://api.router.example.com/api/v1/optimize').valid, true);
assert.strictEqual(validateEndpoint('http://127.0.0.1:8000/api/v1/optimize').valid, true);
assert.strictEqual(validateEndpoint('http://localhost:8000/api/v1/optimize').valid, true);

// Insecure remote transport must be rejected
const insecureRemote = validateEndpoint('http://insecure-backend.com/api/v1/optimize');
assert.strictEqual(insecureRemote.valid, false);
assert(insecureRemote.error.includes('Insecure transport rejected'));

// Malformed URL must be rejected
assert.strictEqual(validateEndpoint('not-a-valid-url').valid, false);
assert.strictEqual(validateEndpoint('').valid, false);
assert.strictEqual(validateEndpoint(null).valid, false);
console.log('PASS: Secure transport validation verified');

// Test 2: Exponential backoff delay calculation
console.log('Test 2: Exponential backoff delay calculation...');
const cfg = { initialBackoffMs: 50, maxBackoffMs: 400, backoffMultiplier: 2 };
const d0 = calculateBackoffDelay(0, cfg);
const d1 = calculateBackoffDelay(1, cfg);
const d2 = calculateBackoffDelay(2, cfg);
assert(d0 >= 50, 'Attempt 0 should be >= initial');
assert(d1 >= d0, 'Attempt 1 delay should grow');
assert(d2 <= 500, 'Delay must be bounded near maxBackoff');
console.log('PASS: Exponential backoff delay verified');

// Test 3: Fail-open decision structure
console.log('Test 3: Fail-open decision structure...');
const failOpen = createFailOpenDecision('req_test_1', 'CONNECTION_TIMEOUT', 'corr_test_1');
assert.strictEqual(failOpen.request_id, 'req_test_1');
assert.strictEqual(failOpen.correlation_id, 'corr_test_1');
assert.strictEqual(failOpen.decision_type, 'NO_OPTIMIZATION');
assert.strictEqual(failOpen.confidence, 0.0);
assert.strictEqual(failOpen.reason_code, 'FAIL_OPEN_FALLBACK');
assert.strictEqual(failOpen.failOpen, true);
assert.strictEqual(failOpen.errorReason, 'CONNECTION_TIMEOUT');
assert(typeof failOpen.timestamp === 'number');
console.log('PASS: Fail-open decision structure verified');

// Test 4: Dynamic endpoint resolution from storage
console.log('Test 4: Storage-backed endpoint resolution...');
const mockStorage = {
  get: (keys, cb) => cb({ backendEndpoint: 'http://127.0.0.1:9999/api/v1/optimize' })
};
const clientWithStorage = new BackendClient({ storage: mockStorage });
clientWithStorage.getEffectiveEndpoint().then(async (ep) => {
  assert.strictEqual(ep, 'http://127.0.0.1:9999/api/v1/optimize');
  console.log('PASS: Storage-backed endpoint resolution verified');

  // Test 5: Successful query optimization using mock execute
  console.log('Test 5: Successful query optimization...');
  const mockSuccessFetch = async (endpoint, pkg, timeout) => {
    return {
      request_id: 'req_success',
      correlation_id: 'corr_success_123',
      decision_type: 'NO_OPTIMIZATION',
      confidence: 0.9,
      reason_code: 'PASSTHROUGH',
      optimization_instructions: null
    };
  };

  const clientSuccess = new BackendClient({ endpoint: 'http://127.0.0.1:8000/api/v1/optimize' });
  clientSuccess._executeFetch = mockSuccessFetch;

  const successDecision = await clientSuccess.optimizeQuery({
    request_id: 'req_success',
    correlation_id: 'corr_success_123',
    query_text: 'hello'
  });
  assert.strictEqual(successDecision.request_id, 'req_success');
  assert.strictEqual(successDecision.correlation_id, 'corr_success_123');
  assert.strictEqual(successDecision.decision_type, 'NO_OPTIMIZATION');
  assert.strictEqual(successDecision.failOpen, undefined);
  console.log('PASS: Successful query optimization verified');

  // Test 6: Timeout and fail-open recovery
  console.log('Test 6: Timeout and fail-open recovery...');
  const logsCaptured = [];
  const mockLogger = {
    info: (cat, msg, meta) => logsCaptured.push({ level: 'info', msg, meta }),
    warn: (cat, msg, meta) => logsCaptured.push({ level: 'warn', msg, meta }),
    debug: (cat, msg, meta) => logsCaptured.push({ level: 'debug', msg, meta })
  };

  const clientTimeout = new BackendClient({
    endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
    timeoutMs: 50,
    maxRetries: 1,
    initialBackoffMs: 10,
    maxBackoffMs: 20,
    logger: mockLogger
  });

  // Simulate timeout abort
  clientTimeout._executeFetch = async () => {
    const err = new Error('The operation was aborted');
    err.name = 'AbortError';
    throw err;
  };

  const timeoutDecision = await clientTimeout.optimizeQuery({
    request_id: 'req_timeout_test',
    correlation_id: 'corr_timeout_test',
    query_text: 'A sensitive secret user query'
  });

  assert.strictEqual(timeoutDecision.failOpen, true, 'Must return failOpen: true on timeout');
  assert.strictEqual(timeoutDecision.correlation_id, 'corr_timeout_test');
  assert.strictEqual(timeoutDecision.decision_type, 'NO_OPTIMIZATION');
  assert.strictEqual(timeoutDecision.reason_code, 'FAIL_OPEN_FALLBACK');
  assert.strictEqual(timeoutDecision.errorReason, 'TIMEOUT');
  console.log('PASS: Timeout and fail-open recovery verified');

  // Test 7: Privacy audit on failure (Zero query text in logs)
  console.log('Test 7: Privacy audit on network failure...');
  assert(logsCaptured.length > 0, 'Should have logged failure warning');
  const failureLog = logsCaptured.find((l) => l.level === 'warn');
  assert(failureLog, 'Warning log must be recorded');
  const logMetaStr = JSON.stringify(failureLog.meta);
  assert(!logMetaStr.includes('sensitive'), 'Log must not contain query text');
  assert(!logMetaStr.includes('secret user query'), 'Log must not contain user query');
  assert(logMetaStr.includes('req_timeout_test'), 'Log should include safe request ID');
  assert(logMetaStr.includes('corr_timeout_test'), 'Log should include safe correlation ID');
  console.log('PASS: Zero raw query text logged on network failure');

  // Test 8: Non-retry of 4xx client errors
  console.log('Test 8: Non-retry of 4xx client errors...');
  let fetchAttempts = 0;
  const client422 = new BackendClient({
    endpoint: 'http://127.0.0.1:8000/api/v1/optimize',
    maxRetries: 3
  });
  client422._executeFetch = async () => {
    fetchAttempts++;
    const err = new Error('HTTP error 422');
    err.status = 422;
    throw err;
  };

  const decision422 = await client422.optimizeQuery({ request_id: 'req_422', query_text: '' });
  assert.strictEqual(fetchAttempts, 1, 'Client error 422 must not be retried');
  assert.strictEqual(decision422.failOpen, true);
  console.log('PASS: 4xx errors not retried');

  console.log('--- ALL BACKEND CLIENT TESTS PASSED ---');
});
