/**
 * Automated Unit Test Suite for Diagnostic Logger
 * Tests log levels, categories, privacy redaction (tokens, queries, cookies),
 * and ring buffer behavior.
 */

const assert = require('assert');
const {
  LogLevel,
  LogLevelNames,
  EventCategory,
  DiagnosticLogger,
  redactIdentifier,
  sanitizeMetadata
} = require('../src/shared/logger');

console.log('--- Running Diagnostic Logger Tests ---');

// Test 1: Event categories completeness
console.log('Test 1: Event categories completeness...');
const expectedCategories = [
  'STARTUP',
  'PAGE_ATTACH',
  'PAGE_DETACH',
  'QUERY_DETECTION',
  'OPTIMIZATION_DECISION',
  'CACHE_DECISION',
  'BACKEND_CALL',
  'FAILURE'
];
for (const cat of expectedCategories) {
  assert(EventCategory[cat] !== undefined, `Category ${cat} must exist`);
}
console.log('PASS: All 8 required event categories present');

// Test 2: Identifier redaction
console.log('Test 2: Identifier redaction...');
assert.strictEqual(redactIdentifier(null), 'anon');
assert.strictEqual(redactIdentifier(undefined), 'anon');
assert.strictEqual(redactIdentifier(101), 'id-1***');
const redactedTab = redactIdentifier('tab-998877');
assert(redactedTab.includes('***'), 'Contains mask');
assert(!redactedTab.includes('9988'), 'Middle digits concealed');
console.log('PASS: Identifier redaction verified');

// Test 3: Sensitive metadata and raw query text redaction
console.log('Test 3: Sensitive metadata and raw query text redaction...');
const sensitivePayload = {
  safeKey: 'normal_value',
  cookie: 'session_id=secret123',
  token: 'Bearer sk-ant-secret-token',
  prompt: 'Can you solve this private coding problem?',
  conversationId: 'chat-uuid-12345',
  sessionId: 'sess-abc-789',
  authToken: 'secret-token-xyz',
  query_text: 'sensitive query',
  apiKey: 'key-123',
  nested: {
    authorization: 'Bearer token456',
    query: 'select * from users',
    rawPrompt: 'nested secret prompt',
    safeCount: 42
  }
};

const sanitized = sanitizeMetadata(sensitivePayload);
assert.strictEqual(sanitized.safeKey, 'normal_value');
assert.strictEqual(sanitized.cookie, '[REDACTED]');
assert.strictEqual(sanitized.token, '[REDACTED]');
assert.strictEqual(sanitized.prompt, '[REDACTED]');
assert.strictEqual(sanitized.prompt_length, 42); // Records length metric only
assert.strictEqual(sanitized.conversationId, '[REDACTED]');
assert.strictEqual(sanitized.sessionId, '[REDACTED]');
assert.strictEqual(sanitized.authToken, '[REDACTED]');
assert.strictEqual(sanitized.query_text, '[REDACTED]');
assert.strictEqual(sanitized.apiKey, '[REDACTED]');
assert.strictEqual(sanitized.nested.authorization, '[REDACTED]');
assert.strictEqual(sanitized.nested.query, '[REDACTED]');
assert.strictEqual(sanitized.nested.query_length, 19);
assert.strictEqual(sanitized.nested.rawPrompt, '[REDACTED]');
assert.strictEqual(sanitized.nested.safeCount, 42);
console.log('PASS: Zero raw query text, cookies, or tokens exposed (including compound keys)');

// Test 4: Quiet by default in production
console.log('Test 4: Quiet by default behavior...');
let consoleCalls = 0;
const testLogger = new DiagnosticLogger({
  level: LogLevel.WARN,
  enableConsole: true
});
// Override console to detect if quiet threshold works
const origDebug = console.debug;
const origInfo = console.info;
console.debug = () => { consoleCalls++; };
console.info = () => { consoleCalls++; };

testLogger.debug(EventCategory.QUERY_DETECTION, 'Trivial event');
testLogger.info(EventCategory.OPTIMIZATION_DECISION, 'Info event');
assert.strictEqual(consoleCalls, 0, 'Debug and Info must not call console when level is WARN');

// Restore console
console.debug = origDebug;
console.info = origInfo;
console.log('PASS: Logger is quiet by default for DEBUG and INFO');

// Test 5: In-memory ring buffer records entries regardless of console level
console.log('Test 5: In-memory ring buffer recording and cap...');
const bufferLogger = new DiagnosticLogger({
  maxBufferSize: 3,
  enableConsole: false
});
bufferLogger.info(EventCategory.STARTUP, 'Event 1');
bufferLogger.info(EventCategory.PAGE_ATTACH, 'Event 2');
bufferLogger.warn(EventCategory.BACKEND_CALL, 'Event 3');
bufferLogger.error(EventCategory.FAILURE, 'Event 4');

const logs = bufferLogger.getRecentLogs();
assert.strictEqual(logs.length, 3, 'Buffer capped at max size 3');
assert.strictEqual(logs[0].message, 'Event 2', 'Oldest event 1 dropped');
assert.strictEqual(logs[2].message, 'Event 4', 'Newest event 4 retained');
assert.strictEqual(logs[2].category, EventCategory.FAILURE);
console.log('PASS: In-memory ring buffer maintains fixed capacity and ordering');

console.log('--- ALL DIAGNOSTIC LOGGER TESTS PASSED ---');
