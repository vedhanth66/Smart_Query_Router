/**
 * Automated Unit Test Suite for Optimization Decision Interface
 * Tests decision outcomes, decision schema, baseline evaluation,
 * and rule pluggability.
 */

const assert = require('assert');
const {
  DecisionOutcome,
  BaseRuleIds,
  createOptimizationDecision,
  validateOptimizationDecision,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Optimization Decision Interface Tests ---');

// Test 1: All required decision outcomes are defined
console.log('Test 1: Explicit decision outcomes verification...');
assert.strictEqual(DecisionOutcome.LOCAL_ANSWER_CANDIDATE, 'LOCAL_ANSWER_CANDIDATE');
assert.strictEqual(DecisionOutcome.CACHE_CANDIDATE, 'CACHE_CANDIDATE');
assert.strictEqual(DecisionOutcome.BACKEND_CANDIDATE, 'BACKEND_CANDIDATE');
assert.strictEqual(DecisionOutcome.NO_OPTIMIZATION, 'NO_OPTIMIZATION');
console.log('PASS: All 4 explicit decision outcomes verified');

// Test 2: Factory creates valid decision with human-readable reason & machine-readable ruleId
console.log('Test 2: OptimizationDecision factory validation...');
const decision = createOptimizationDecision({
  outcome: DecisionOutcome.CACHE_CANDIDATE,
  ruleId: 'RULE_EXACT_MATCH_CACHE',
  reason: 'Query fingerprint matched a recently cached assistant response.',
  confidence: 0.95,
  metadata: { cacheKey: 'abc-123' }
});

assert.strictEqual(decision.outcome, 'CACHE_CANDIDATE');
assert.strictEqual(decision.ruleId, 'RULE_EXACT_MATCH_CACHE');
assert.strictEqual(decision.reason, 'Query fingerprint matched a recently cached assistant response.');
assert.strictEqual(decision.confidence, 0.95);
assert.strictEqual(typeof decision.timestamp, 'number');
assert.strictEqual(decision.metadata.cacheKey, 'abc-123');
assert.strictEqual(validateOptimizationDecision(decision).valid, true);
console.log('PASS: Decision factory creates conformant objects');

// Test 3: Factory rejects invalid inputs
console.log('Test 3: Factory rejects invalid outcomes and missing fields...');
assert.throws(() => {
  createOptimizationDecision({
    outcome: 'MAGIC_OPTIMIZE', // Invalid outcome
    ruleId: 'RULE_1',
    reason: 'test'
  });
}, /Invalid decision outcome/);

assert.throws(() => {
  createOptimizationDecision({
    outcome: DecisionOutcome.NO_OPTIMIZATION,
    ruleId: '', // Empty ruleId
    reason: 'test'
  });
}, /requires a non-empty string "ruleId"/);

assert.throws(() => {
  createOptimizationDecision({
    outcome: DecisionOutcome.NO_OPTIMIZATION,
    ruleId: 'RULE_1',
    reason: '   ' // Empty reason
  });
}, /requires a non-empty string "reason"/);
console.log('PASS: Factory strictly rejects invalid inputs');

// Test 4: Default baseline engine returns NO_OPTIMIZATION
console.log('Test 4: Default baseline engine evaluation...');
const engine = new OptimizationDecisionEngine();
const mockEvent = createDetectedQueryEvent({
  rawPrompt: 'What is tail call optimization in recursion?',
  triggerType: 'keyboard_enter'
});

const defaultDecision = engine.evaluate(mockEvent);
assert.strictEqual(defaultDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
assert.strictEqual(defaultDecision.ruleId, BaseRuleIds.DEFAULT_PASS_THROUGH);
assert(defaultDecision.reason.length > 10, 'Contains human-readable reason');
assert.strictEqual(validateOptimizationDecision(defaultDecision).valid, true);
console.log('PASS: Baseline evaluation returns NO_OPTIMIZATION with explicit ruleId and reason');

// Test 5: Pluggable rule execution
console.log('Test 5: Extensible rule pluggability...');
engine.registerRule({
  id: 'RULE_MOCK_LOCAL_ANSWER',
  evaluate: (evt) => {
    if (evt.content.rawPrompt === '/help') {
      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: 'RULE_MOCK_LOCAL_ANSWER',
        reason: 'Matched local help command.',
        confidence: 1.0
      });
    }
    return null; // Fall through
  }
});

// Case A: Rule matches
const helpEvent = createDetectedQueryEvent({ rawPrompt: '/help', triggerType: 'keyboard_enter' });
const matchedDecision = engine.evaluate(helpEvent);
assert.strictEqual(matchedDecision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(matchedDecision.ruleId, 'RULE_MOCK_LOCAL_ANSWER');

// Case B: Rule falls through to baseline
const regularDecision = engine.evaluate(mockEvent);
assert.strictEqual(regularDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
assert.strictEqual(regularDecision.ruleId, BaseRuleIds.DEFAULT_PASS_THROUGH);

// Reset
engine.reset();
const afterResetDecision = engine.evaluate(helpEvent);
assert.strictEqual(afterResetDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
console.log('PASS: Rule pluggability verified without altering base engine');

console.log('--- ALL DECISION INTERFACE TESTS PASSED ---');
