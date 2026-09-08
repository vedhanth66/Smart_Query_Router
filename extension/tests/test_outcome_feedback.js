/**
 * Test Suite: Outcome Feedback Data Model, Sanitization & Retention
 * 
 * Verifies:
 * 1. Outcome types: SUCCESSFUL_COMPLETION, USER_REJECTION, OPTIMIZATION_BYPASS, ESCALATION, ERROR.
 * 2. Strict privacy: References correlation_id and routing metadata; zero raw prompt/conversation text.
 * 3. Validation: Enforces required fields, enums, and rejects malformed objects.
 * 4. Prepared user feedback data model: rating, rejection reasons, notes for future optional UI control.
 * 5. Bounded in-memory store: FIFO max capacity and TTL eviction.
 * 6. Message passing: Serialization, typed message validation, and HealthTracker buffering.
 * 7. Programmatic submission: Non-intrusive submission helper for future UI controls.
 */

const assert = require('assert');
const outcomeFeedback = require('../src/shared/outcome_feedback');
const messages = require('../src/shared/messages');
const healthTrackerModule = require('../src/background/health_tracker');

const {
  FeedbackOutcomeType,
  FeedbackSource,
  UserRating,
  UserRejectionReason,
  generateFeedbackId,
  validateOutcomeFeedback,
  createOutcomeFeedback,
  FeedbackStore
} = outcomeFeedback;

const {
  MessageTypes,
  validateMessage,
  createOutcomeFeedbackMessage
} = messages;

const { HealthTracker } = healthTrackerModule;

console.log('--- Running Outcome Feedback & Telemetry Data Model Tests ---');

// Test 1: Supported Outcome Types
console.log('Test 1: Supported outcome types...');
assert.strictEqual(FeedbackOutcomeType.SUCCESSFUL_COMPLETION, 'SUCCESSFUL_COMPLETION');
assert.strictEqual(FeedbackOutcomeType.USER_REJECTION, 'USER_REJECTION');
assert.strictEqual(FeedbackOutcomeType.OPTIMIZATION_BYPASS, 'OPTIMIZATION_BYPASS');
assert.strictEqual(FeedbackOutcomeType.ESCALATION, 'ESCALATION');
assert.strictEqual(FeedbackOutcomeType.ERROR, 'ERROR');
assert.strictEqual(Object.keys(FeedbackOutcomeType).length, 5);
console.log('PASS: Exactly 5 outcome types verified');

// Test 2: Validation of required fields
console.log('Test 2: Schema validation of outcome feedback...');
const validFeedback = createOutcomeFeedback({
  correlationId: 'corr_test_123',
  outcomeType: FeedbackOutcomeType.SUCCESSFUL_COMPLETION
});
assert.strictEqual(validateOutcomeFeedback(validFeedback).valid, true);

// Missing correlationId
assert.strictEqual(validateOutcomeFeedback({
  feedbackId: 'fb_1',
  outcomeType: FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
  timestamp: Date.now(),
  source: FeedbackSource.SYSTEM
}).valid, false);

// Invalid outcomeType
assert.strictEqual(validateOutcomeFeedback({
  feedbackId: 'fb_1',
  correlationId: 'corr_1',
  outcomeType: 'INVALID_TYPE',
  timestamp: Date.now(),
  source: FeedbackSource.SYSTEM
}).valid, false);

// Invalid timestamp
assert.strictEqual(validateOutcomeFeedback({
  feedbackId: 'fb_1',
  correlationId: 'corr_1',
  outcomeType: FeedbackOutcomeType.ERROR,
  timestamp: -1,
  source: FeedbackSource.SYSTEM
}).valid, false);

console.log('PASS: Outcome feedback schema validation correctly accepts valid and rejects invalid objects');

// Test 3: Privacy Sanitization Guarantee
console.log('Test 3: Privacy sanitization guarantee (purges raw prompts, cookies, tokens)...');
const leakingFeedback = createOutcomeFeedback({
  correlationId: 'corr_privacy_123',
  outcomeType: FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
  routingMetadata: {
    coarseRoute: 'simple-model candidate',
    modelRoute: 'fast_cheap',
    taskCategory: 'greeting',
    // LEAK ATTEMPTS:
    prompt: 'Secret user password or question',
    raw_prompt: 'Do not store this text',
    query_text: 'User private notes',
    response_text: 'Assistant raw answer text',
    cookie: 'session_id=12345; auth=token_abc',
    token: 'bearer_token_xyz',
    conversationId: 'chat-uuid-1234'
  },
  executionMetadata: {
    durationMs: 450,
    // LEAK ATTEMPTS:
    session_id: 'session_999',
    turn_content: 'Confidential conversation'
  }
});

// Verify routing metadata retained allowed fields
assert.strictEqual(leakingFeedback.routingMetadata.coarseRoute, 'simple-model candidate');
assert.strictEqual(leakingFeedback.routingMetadata.modelRoute, 'fast_cheap');
assert.strictEqual(leakingFeedback.routingMetadata.taskCategory, 'greeting');

// Verify sensitive conversational fields were completely purged
assert.strictEqual(leakingFeedback.routingMetadata.prompt, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.raw_prompt, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.query_text, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.response_text, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.cookie, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.token, undefined);
assert.strictEqual(leakingFeedback.routingMetadata.conversationId, undefined);

// Verify execution metadata purged leaks
assert.strictEqual(leakingFeedback.executionMetadata.durationMs, 450);
assert.strictEqual(leakingFeedback.executionMetadata.session_id, undefined);
assert.strictEqual(leakingFeedback.executionMetadata.turn_content, undefined);

// Verify object is frozen
assert.strictEqual(Object.isFrozen(leakingFeedback), true);
assert.strictEqual(Object.isFrozen(leakingFeedback.routingMetadata), true);

console.log('PASS: Privacy sanitization guarantee verified (zero conversation text or auth leakage)');

// Test 4: Correlation ID and routing metadata linkage
console.log('Test 4: Correlation ID and routing metadata linkage...');
const escalationFeedback = createOutcomeFeedback({
  correlationId: 'corr_link_456',
  requestId: 'req_link_789',
  outcomeType: FeedbackOutcomeType.ESCALATION,
  routingMetadata: {
    coarseRoute: 'needs-evaluation',
    modelRoute: 'strong',
    modelVersion: 'gpt-4o-2024-08-06',
    escalationOccurred: true,
    escalationReason: 'Evaluator detected incomplete code block',
    taskCategory: 'coding'
  },
  executionMetadata: {
    durationMs: 1250,
    failureReason: null
  }
});

assert.strictEqual(escalationFeedback.correlationId, 'corr_link_456');
assert.strictEqual(escalationFeedback.requestId, 'req_link_789');
assert.strictEqual(escalationFeedback.outcomeType, FeedbackOutcomeType.ESCALATION);
assert.strictEqual(escalationFeedback.routingMetadata.escalationOccurred, true);
assert.strictEqual(escalationFeedback.routingMetadata.escalationReason, 'Evaluator detected incomplete code block');
assert.strictEqual(escalationFeedback.routingMetadata.modelRoute, 'strong');
assert.strictEqual(escalationFeedback.executionMetadata.durationMs, 1250);
console.log('PASS: Correlation ID and routing metadata cleanly linked');

// Test 5: Prepared data model for future optional user feedback control
console.log('Test 5: Prepared data model for future optional user feedback control...');
const userRejectionFeedback = createOutcomeFeedback({
  correlationId: 'corr_user_rej_001',
  outcomeType: FeedbackOutcomeType.USER_REJECTION,
  source: FeedbackSource.USER,
  routingMetadata: {
    coarseRoute: 'simple-model candidate',
    decisionType: 'CONTEXT_PRUNING'
  },
  userFeedback: {
    rating: UserRating.NEGATIVE,
    rejectionReason: UserRejectionReason.UNWANTED_REWRITE,
    notes: 'Preferred the unmodified query without abbreviations.'
  }
});

assert.strictEqual(userRejectionFeedback.source, FeedbackSource.USER);
assert.strictEqual(userRejectionFeedback.outcomeType, FeedbackOutcomeType.USER_REJECTION);
assert.notStrictEqual(userRejectionFeedback.userFeedback, null);
assert.strictEqual(userRejectionFeedback.userFeedback.rating, UserRating.NEGATIVE);
assert.strictEqual(userRejectionFeedback.userFeedback.rejectionReason, 'UNWANTED_REWRITE');
assert.strictEqual(userRejectionFeedback.userFeedback.notes, 'Preferred the unmodified query without abbreviations.');
assert.strictEqual(typeof userRejectionFeedback.userFeedback.submittedAt, 'number');
console.log('PASS: Prepared data model for future user feedback control verified');

// Test 6: Bounded In-Memory FeedbackStore (FIFO & TTL)
console.log('Test 6: Bounded in-memory FeedbackStore (FIFO and TTL eviction)...');
const t0 = Date.now();
const store = new FeedbackStore({ maxEntries: 3, retentionTtlMs: 500 });

store.recordFeedback(createOutcomeFeedback({ correlationId: 'corr_1', outcomeType: FeedbackOutcomeType.SUCCESSFUL_COMPLETION, recordedAt: t0 }));
store.recordFeedback(createOutcomeFeedback({ correlationId: 'corr_2', outcomeType: FeedbackOutcomeType.OPTIMIZATION_BYPASS, recordedAt: t0 + 100 }));
store.recordFeedback(createOutcomeFeedback({ correlationId: 'corr_3', outcomeType: FeedbackOutcomeType.ESCALATION, recordedAt: t0 + 200 }));

assert.strictEqual(store.size(t0 + 250), 3);

// Exceed maxEntries (FIFO eviction of oldest)
store.recordFeedback(createOutcomeFeedback({ correlationId: 'corr_4', outcomeType: FeedbackOutcomeType.ERROR, recordedAt: t0 + 300 }));
assert.strictEqual(store.size(t0 + 350), 3);
assert.strictEqual(store.findByCorrelationId('corr_1', t0 + 350), null); // Evicted!
assert.notStrictEqual(store.findByCorrelationId('corr_4', t0 + 350), null); // Newest present

// TTL eviction: at t0 + 750, cutoff is (t0 + 750) - 500 = t0 + 250.
// corr_2 (t0 + 100) and corr_3 (t0 + 200) are expired. Only corr_4 (t0 + 300) remains!
const pruned = store.pruneExpired(t0 + 750);
assert.strictEqual(pruned, 2);
assert.strictEqual(store.size(t0 + 750), 1);
assert.strictEqual(store.findByCorrelationId('corr_4', t0 + 750).correlationId, 'corr_4');
console.log('PASS: FeedbackStore FIFO and TTL eviction verified');

// Test 7: Typed message passing & HealthTracker integration
console.log('Test 7: Typed message passing and HealthTracker integration...');
const fbEvent = createOutcomeFeedback({
  correlationId: 'corr_msg_test',
  outcomeType: FeedbackOutcomeType.OPTIMIZATION_BYPASS,
  executionMetadata: { failureReason: 'USER_HOTKEY_BYPASS' }
});

const msg = createOutcomeFeedbackMessage(fbEvent);
assert.strictEqual(msg.type, MessageTypes.OUTCOME_FEEDBACK);
assert.strictEqual(validateMessage(msg).valid, true);

// Background HealthTracker integration
const healthTracker = new HealthTracker();
assert.strictEqual(healthTracker.getHealthSummary().outcomeFeedbackCount, 0);

healthTracker.recordOutcomeFeedback(fbEvent);
assert.strictEqual(healthTracker.getHealthSummary().outcomeFeedbackCount, 1);
const recent = healthTracker.getRecentOutcomeFeedback();
assert.strictEqual(recent.length, 1);
assert.strictEqual(recent[0].correlationId, 'corr_msg_test');
assert.strictEqual(recent[0].outcomeType, FeedbackOutcomeType.OPTIMIZATION_BYPASS);
console.log('PASS: Typed message passing and HealthTracker integration verified');

// Test 8: All 5 outcome types in pipeline context
console.log('Test 8: Verify creation of each outcome feedback type in real scenarios...');
const scenarios = [
  { type: FeedbackOutcomeType.SUCCESSFUL_COMPLETION, reason: null },
  { type: FeedbackOutcomeType.USER_REJECTION, reason: 'UNWANTED_REWRITE' },
  { type: FeedbackOutcomeType.OPTIMIZATION_BYPASS, reason: 'USER_HOTKEY_BYPASS' },
  { type: FeedbackOutcomeType.ESCALATION, reason: 'High complexity routing' },
  { type: FeedbackOutcomeType.ERROR, reason: 'ERROR_BANNER' }
];

scenarios.forEach((sc, idx) => {
  const ev = createOutcomeFeedback({
    correlationId: `corr_scenario_${idx}`,
    outcomeType: sc.type,
    executionMetadata: { failureReason: sc.reason }
  });
  assert.strictEqual(ev.outcomeType, sc.type);
  assert.strictEqual(ev.correlationId, `corr_scenario_${idx}`);
});
console.log('PASS: All 5 outcome scenarios cleanly created');

console.log('--- ALL OUTCOME FEEDBACK TESTS PASSED ---');
