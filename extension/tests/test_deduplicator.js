/**
 * Automated Unit Test Suite for Query Deduplicator
 * Tests idempotency, time window eviction, cooldown handling, and memory boundedness.
 */

const assert = require('assert');
const {
  QueryDeduplicator,
  fnv1a,
  normalizeText,
  DEFAULT_WINDOW_MS,
  DEFAULT_MIN_COOLDOWN_MS,
  DEFAULT_MAX_ENTRIES
} = require('../src/shared/deduplicator');

console.log('--- Running Query Deduplicator Tests ---');

// Test 1: First submission is accepted
console.log('Test 1: First submission is accepted...');
const dedup = new QueryDeduplicator({
  windowMs: 2000,
  minCooldownMs: 500,
  maxEntries: 10
});

const t0 = 1000000;
const promptA = 'How do I implement a binary heap in Python?';
const contextA = { conversationId: 'conv_123' };

const res1 = dedup.recordSubmission(promptA, contextA, t0);
assert.strictEqual(res1.accepted, true, 'First submission accepted');
assert.strictEqual(dedup.size(), 1);
console.log('PASS: Initial submission accepted');

// Test 2: Identical submission in rapid succession (e.g. Enter + click or DOM re-render) is rejected
console.log('Test 2: Rapid identical submission rejected...');
const resDuplicateRapid = dedup.recordSubmission(promptA, contextA, t0 + 50);
assert.strictEqual(resDuplicateRapid.accepted, false, 'Rapid identical submission rejected');
assert(resDuplicateRapid.reason === 'COOLDOWN_ACTIVE' || resDuplicateRapid.reason === 'IDENTICAL_QUERY_IN_WINDOW');
assert.strictEqual(dedup.size(), 1, 'Size did not increase');
console.log('PASS: Rapid identical submission rejected');

// Test 3: Identical submission after cooldown but within window is rejected
console.log('Test 3: Identical submission within window rejected...');
const resDuplicateInWindow = dedup.recordSubmission(promptA, contextA, t0 + 800); // 800ms > 500ms cooldown, but < 2000ms window
assert.strictEqual(resDuplicateInWindow.accepted, false, 'In-window identical submission rejected');
assert.strictEqual(resDuplicateInWindow.reason, 'IDENTICAL_QUERY_IN_WINDOW');
console.log('PASS: Identical submission within window rejected');

// Test 4: Different prompt submitted too quickly (within cooldown) is rejected
console.log('Test 4: Consecutive distinct prompt within cooldown rejected...');
const promptB = 'What is the time complexity of heapify?';
const resCooldownActive = dedup.recordSubmission(promptB, contextA, t0 + 200); // 200ms < 500ms
assert.strictEqual(resCooldownActive.accepted, false);
assert.strictEqual(resCooldownActive.reason, 'COOLDOWN_ACTIVE');
console.log('PASS: Cooldown rejects immediate consecutive queries');

// Test 5: Different prompt after cooldown is accepted
console.log('Test 5: Distinct prompt after cooldown accepted...');
const resDistinctAccepted = dedup.recordSubmission(promptB, contextA, t0 + 600); // 600ms > 500ms
assert.strictEqual(resDistinctAccepted.accepted, true, 'Distinct submission accepted');
assert.strictEqual(dedup.size(), 2);
console.log('PASS: Distinct query after cooldown accepted');

// Test 6: Identical prompt accepted after window expires
console.log('Test 6: Identical prompt accepted after window expiration...');
const tExpired = t0 + 3000; // 3000ms > 2000ms window
const resExpiredAccepted = dedup.recordSubmission(promptA, contextA, tExpired);
assert.strictEqual(resExpiredAccepted.accepted, true, 'Identical submission accepted after window expires');
console.log('PASS: Identical submission accepted after time window expires');

// Test 7: Memory boundedness (size does not exceed maxEntries)
console.log('Test 7: Bounded memory enforcement...');
const smallDedup = new QueryDeduplicator({
  windowMs: 10000,
  minCooldownMs: 0, // No cooldown for batch test
  maxEntries: 3
});

let testTime = 2000000;
for (let i = 0; i < 10; i++) {
  testTime += 10;
  const res = smallDedup.recordSubmission(`Unique query number ${i}`, {}, testTime);
  assert.strictEqual(res.accepted, true);
  assert(smallDedup.size() <= 3, `Size (${smallDedup.size()}) must not exceed maxEntries (3)`);
}
assert.strictEqual(smallDedup.size(), 3, 'Tracker strictly capped at maxEntries');
console.log('PASS: Bounded memory strictly enforced');

// Test 8: Reset functionality
console.log('Test 8: Reset state...');
smallDedup.reset();
assert.strictEqual(smallDedup.size(), 0);
console.log('PASS: Reset clears internal state');

console.log('--- ALL DEDUPLICATOR TESTS PASSED ---');
