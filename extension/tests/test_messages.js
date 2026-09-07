/**
 * Automated Unit Test Suite for Typed Message Protocol
 * Tests message generation, schema validation, and rejection of unknown/malformed types.
 */

const assert = require('assert');
const messages = require('../src/shared/messages');

console.log('--- Running Typed Message Protocol Tests ---');

const {
  MessageTypes,
  ErrorCodes,
  validateMessage,
  createInitMessage,
  createStatusRequestMessage,
  createTestEventMessage,
  createSuccessResponse,
  createErrorResponse
} = messages;

// Test 1: Factory functions produce valid messages
console.log('Test 1: Factory message creation and validation...');
const initMsg = createInitMessage('https://claude.ai');
assert.strictEqual(initMsg.type, MessageTypes.INIT);
assert.strictEqual(initMsg.payload.origin, 'https://claude.ai');
assert.strictEqual(typeof initMsg.timestamp, 'number');
assert.strictEqual(validateMessage(initMsg).valid, true);

const statusMsg = createStatusRequestMessage();
assert.strictEqual(statusMsg.type, MessageTypes.STATUS_REQUEST);
assert.strictEqual(validateMessage(statusMsg).valid, true);

const testMsg = createTestEventMessage('probe-123');
assert.strictEqual(testMsg.type, MessageTypes.TEST_EVENT);
assert.strictEqual(testMsg.payload.testId, 'probe-123');
assert.strictEqual(validateMessage(testMsg).valid, true);

const healthMsg = messages.createHealthCheckMessage();
assert.strictEqual(healthMsg.type, MessageTypes.HEALTH_CHECK);
assert.strictEqual(validateMessage(healthMsg).valid, true);

const diagMsg = messages.createDiagnosticsRequestMessage();
assert.strictEqual(diagMsg.type, MessageTypes.DIAGNOSTICS_REQUEST);
assert.strictEqual(validateMessage(diagMsg).valid, true);

const queryObsMsg = messages.createQueryObservedMessage(42, 'keyboard');
assert.strictEqual(queryObsMsg.type, MessageTypes.QUERY_OBSERVED);
assert.strictEqual(queryObsMsg.payload.prompt_length, 42);
assert.strictEqual(validateMessage(queryObsMsg).valid, true);

const optMsg = messages.createOptimizeRequestMessage({ request_id: 'req_1', query_text: 'hello' });
assert.strictEqual(optMsg.type, MessageTypes.OPTIMIZE_REQUEST);
assert.strictEqual(optMsg.payload.package.request_id, 'req_1');
assert.strictEqual(validateMessage(optMsg).valid, true);
console.log('PASS: Valid factory messages verified');

// Test 2: Reject non-object or null messages
console.log('Test 2: Rejection of non-object messages...');
assert.strictEqual(validateMessage(null).valid, false);
assert.strictEqual(validateMessage(null).code, ErrorCodes.INVALID_MESSAGE_STRUCTURE);

assert.strictEqual(validateMessage(undefined).valid, false);
assert.strictEqual(validateMessage(undefined).code, ErrorCodes.INVALID_MESSAGE_STRUCTURE);

assert.strictEqual(validateMessage('not a message').valid, false);
assert.strictEqual(validateMessage([1, 2, 3]).valid, false);
console.log('PASS: Non-object messages properly rejected');

// Test 3: Reject unknown message types
console.log('Test 3: Rejection of unknown message types...');
const unknownTypeMsg = {
  type: 'QUERY_CONVERSATION_TEXT', // Must be rejected
  timestamp: Date.now(),
  payload: { text: 'Hello' }
};
const unknownResult = validateMessage(unknownTypeMsg);
assert.strictEqual(unknownResult.valid, false);
assert.strictEqual(unknownResult.code, ErrorCodes.UNKNOWN_MESSAGE_TYPE);

const maliciousMsg = {
  type: '__proto__',
  timestamp: Date.now(),
  payload: {}
};
assert.strictEqual(validateMessage(maliciousMsg).valid, false);
assert.strictEqual(validateMessage(maliciousMsg).code, ErrorCodes.UNKNOWN_MESSAGE_TYPE);
console.log('PASS: Unknown message types strictly rejected');

// Test 4: Reject invalid payloads
console.log('Test 4: Rejection of malformed payloads...');
const badTimestampMsg = {
  type: MessageTypes.STATUS_REQUEST,
  timestamp: 'invalid-time',
  payload: {}
};
assert.strictEqual(validateMessage(badTimestampMsg).valid, false);
assert.strictEqual(validateMessage(badTimestampMsg).code, ErrorCodes.INVALID_PAYLOAD);

const badInitMsg = {
  type: MessageTypes.INIT,
  timestamp: Date.now(),
  payload: { origin: '' } // empty origin
};
assert.strictEqual(validateMessage(badInitMsg).valid, false);
assert.strictEqual(validateMessage(badInitMsg).code, ErrorCodes.INVALID_PAYLOAD);

const badTestMsg = {
  type: MessageTypes.TEST_EVENT,
  timestamp: Date.now(),
  payload: {} // missing testId
};
assert.strictEqual(validateMessage(badTestMsg).valid, false);
assert.strictEqual(validateMessage(badTestMsg).code, ErrorCodes.INVALID_PAYLOAD);
console.log('PASS: Malformed payloads properly rejected');

// Test 5: Response helpers
console.log('Test 5: Response helpers formatting...');
const successResp = createSuccessResponse({ status: 'READY' });
assert.strictEqual(successResp.success, true);
assert.strictEqual(successResp.data.status, 'READY');
assert.strictEqual(typeof successResp.timestamp, 'number');

const errorResp = createErrorResponse(ErrorCodes.UNKNOWN_MESSAGE_TYPE, 'Unknown type');
assert.strictEqual(errorResp.success, false);
assert.strictEqual(errorResp.code, ErrorCodes.UNKNOWN_MESSAGE_TYPE);
assert.strictEqual(errorResp.error, 'Unknown type');
console.log('PASS: Response helpers verified');

console.log('--- ALL MESSAGE PROTOCOL TESTS PASSED ---');
