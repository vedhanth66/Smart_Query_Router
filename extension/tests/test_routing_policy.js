/**
 * Smart Query Router - Unit Tests for Deterministic Routing Policy Engine
 * 
 * Verifies:
 * 1. Classification into coarse routes:
 *    - 'local-eligible'
 *    - 'simple-model candidate'
 *    - 'complex-model candidate'
 *    - 'needs-evaluation'
 * 2. Strict guardrail: NEVER route on word count alone!
 *    - Short query with code -> complex-model candidate
 *    - Short query with math & reasoning -> complex-model candidate
 *    - Long query without code/math/cues -> simple-model candidate
 * 3. Full explainability: explicit ruleId, reasonCode, human-readable explanation, matchedSignals, and ruleTrace.
 * 4. Policy configuration decoupled from UI: validation, overrides, and independent instantiation.
 */

const assert = require('assert');
const {
  DEFAULT_ROUTING_POLICY_CONFIG,
  validateRoutingPolicyConfig,
  createRoutingPolicyConfig
} = require('../src/shared/routing_policy_config');

const {
  CoarseRoute,
  RoutingReasonCode,
  DeterministicRoutingPolicy,
  defaultRoutingPolicy
} = require('../src/shared/routing_policy');

const { extractQueryFeatures } = require('../src/shared/feature_extractor');
const { detectContextDependency } = require('../src/shared/context_detector');

console.log('--- Running Deterministic Routing Policy Tests ---');

// Helper to build a complete test queryEvent
function buildTestQueryEvent(promptText, options = {}) {
  const text = promptText.trim();
  const features = extractQueryFeatures(text);
  const contextDep = detectContextDependency(text, features);

  return {
    metadata: {
      eventId: 'evt_test_1',
      timestamp: Date.now(),
      triggerType: 'keyboard_enter'
    },
    content: {
      rawPrompt: text,
      normalizedPrompt: text,
      characterCount: text.length,
      wordCount: text.split(/\s+/).filter(Boolean).length
    },
    features,
    contextDependency: options.contextDependency || contextDep,
    optimization: {
      status: options.optOutcome ? 'EVALUATED' : 'PENDING',
      decision: options.optOutcome ? {
        outcome: options.optOutcome,
        ruleId: options.optRuleId || 'RULE_MOCK_LOCAL',
        reason: options.optReason || 'Mock local rule match',
        confidence: 1.0
      } : null
    }
  };
}

// Test 1: Policy Configuration Schema & UI Decoupling
console.log('Test 1: Policy configuration validation & independence...');
assert.strictEqual(validateRoutingPolicyConfig(DEFAULT_ROUTING_POLICY_CONFIG).valid, true);

// Rejection of invalid config
assert.strictEqual(validateRoutingPolicyConfig(null).valid, false);
assert.strictEqual(validateRoutingPolicyConfig({ version: '' }).valid, false);
assert.strictEqual(validateRoutingPolicyConfig({ version: '1.0', enabled: 'yes' }).valid, false);

// Custom override without UI dependency
const customConfig = createRoutingPolicyConfig({
  thresholds: { minConfidence: 0.85 }
});
assert.strictEqual(customConfig.thresholds.minConfidence, 0.85);
assert.strictEqual(customConfig.enabledRoutes['local-eligible'], true);
console.log('PASS: Policy configuration validation & independence verified');

// Test 2: Local-Eligible Classification
console.log('Test 2: Local-eligible classification...');
const policy = new DeterministicRoutingPolicy();

// Arithmetic local rule match
const arithmeticEvent = buildTestQueryEvent('what is 15 * 4?', {
  optOutcome: 'LOCAL_ANSWER_CANDIDATE',
  optRuleId: 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC',
  optReason: 'Deterministic arithmetic: 15 * 4 = 60'
});
const arithResult = policy.classify(arithmeticEvent);
assert.strictEqual(arithResult.route, CoarseRoute.LOCAL_ELIGIBLE);
assert.strictEqual(arithResult.reasonCode, RoutingReasonCode.LOCAL_DETERMINISTIC_RULE_MATCH);
assert(arithResult.explanation.includes('LOCAL_DETERMINISTIC_ARITHMETIC'));
assert(arithResult.matchedSignals.includes('RULE_LOCAL_DETERMINISTIC_ARITHMETIC'));

// Date/Time local rule match
const dateTimeEvent = buildTestQueryEvent('what time is it?', {
  optOutcome: 'LOCAL_ANSWER_CANDIDATE',
  optRuleId: 'RULE_LOCAL_DETERMINISTIC_DATETIME',
  optReason: 'Deterministic date/time request'
});
const dateResult = policy.classify(dateTimeEvent);
assert.strictEqual(dateResult.route, CoarseRoute.LOCAL_ELIGIBLE);
assert.strictEqual(dateResult.reasonCode, RoutingReasonCode.LOCAL_DETERMINISTIC_RULE_MATCH);

// Greeting local rule match
const greetingEvent = buildTestQueryEvent('hello claude', {
  optOutcome: 'LOCAL_ANSWER_CANDIDATE',
  optRuleId: 'RULE_LOCAL_CONVERSATIONAL_GREETING',
  optReason: 'Trivial greeting'
});
const greetResult = policy.classify(greetingEvent);
assert.strictEqual(greetResult.route, CoarseRoute.LOCAL_ELIGIBLE);
assert.strictEqual(greetResult.reasonCode, RoutingReasonCode.LOCAL_DETERMINISTIC_RULE_MATCH);
console.log('PASS: Local-eligible classifications verified');

// Test 3: Complex-Model Candidate Classification (Multiple Signals)
console.log('Test 3: Complex-model candidate classification...');

// 3a. Code Syntax (even if very short word count!)
const shortCodeEvent = buildTestQueryEvent('def quicksort(arr): pass');
assert(shortCodeEvent.content.wordCount <= 5, 'Query must be 5 words or fewer');
const shortCodeResult = policy.classify(shortCodeEvent);
assert.strictEqual(
  shortCodeResult.route,
  CoarseRoute.COMPLEX_MODEL_CANDIDATE,
  'Short query with code syntax must be complex-model candidate regardless of small word count'
);
assert.strictEqual(shortCodeResult.reasonCode, RoutingReasonCode.COMPLEX_CODE_SYNTAX);
assert(shortCodeResult.matchedSignals.includes('hasCodeSyntax') || shortCodeResult.matchedSignals.includes('hasCodeKeywords'));

// 3b. Math Notation & Formula (short word count!)
const mathEvent = buildTestQueryEvent('Calculate \\int x^2 dx and solve for x != 0');
const mathResult = policy.classify(mathEvent);
assert.strictEqual(mathResult.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(mathResult.reasonCode, RoutingReasonCode.COMPLEX_MATH_NOTATION);

// 3c. Reasoning Cues
const reasoningEvent = buildTestQueryEvent('Explain step by step why database indexing improves query performance');
const reasonResult = policy.classify(reasoningEvent);
assert.strictEqual(reasonResult.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(reasonResult.reasonCode, RoutingReasonCode.COMPLEX_REASONING_CUE);
assert(reasonResult.matchedSignals.includes('STEP_BY_STEP') || reasonResult.matchedSignals.includes('WHY'));

// 3d. Comparison Cues
const comparisonEvent = buildTestQueryEvent('Compare PostgreSQL vs MySQL and detail the trade-offs');
const compResult = policy.classify(comparisonEvent);
assert.strictEqual(compResult.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(compResult.reasonCode, RoutingReasonCode.COMPLEX_COMPARISON_CUE);
assert(compResult.matchedSignals.includes('VERSUS') || compResult.matchedSignals.includes('COMPARE') || compResult.matchedSignals.includes('TRADEOFFS'));

// 3e. Multi-question
const multiQEvent = buildTestQueryEvent('What is the CAP theorem? How does CockroachDB handle network partitions?');
const multiQResult = policy.classify(multiQEvent);
assert.strictEqual(multiQResult.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(multiQResult.reasonCode, RoutingReasonCode.COMPLEX_MULTI_QUESTION);
console.log('PASS: Complex-model candidate classifications verified');

// Test 4: Simple-Model Candidate Classification (Word Count Neutrality)
console.log('Test 4: Simple-model candidate classification...');

// 4a. Short standalone inquiry
const shortSimpleEvent = buildTestQueryEvent('What is the capital of Australia?');
const shortSimpleResult = policy.classify(shortSimpleEvent);
assert.strictEqual(shortSimpleResult.route, CoarseRoute.SIMPLE_MODEL_CANDIDATE);
assert.strictEqual(shortSimpleResult.reasonCode, RoutingReasonCode.SIMPLE_DIRECT_INQUIRY);

// 4b. LONG prompt with zero code, zero math, zero reasoning/comparison cues
// Proves long length alone NEVER forces a query into complex-model candidate!
const longDescriptivePrompt = 'The gentle morning breeze swept through the quiet meadow as wild flowers swayed under the golden sunlight while several sparrows chirped merrily on the wooden fence post near the old country barn and the distant hills remained shrouded in soft white morning mist.';
const longSimpleEvent = buildTestQueryEvent(longDescriptivePrompt);
assert(longSimpleEvent.content.wordCount >= 40, 'Must be long word count');
const longSimpleResult = policy.classify(longSimpleEvent);
assert.strictEqual(
  longSimpleResult.route,
  CoarseRoute.SIMPLE_MODEL_CANDIDATE,
  'Long narrative with no code/math/cues must route to simple-model candidate, not complex-model candidate'
);
assert.strictEqual(longSimpleResult.reasonCode, RoutingReasonCode.SIMPLE_DIRECT_INQUIRY);
console.log('PASS: Simple-model candidate and word count neutrality verified');

// Test 5: Needs-Evaluation Classification (Context Dependencies & Borderline Signals)
console.log('Test 5: Needs-evaluation classification...');

// 5a. Anaphoric pronoun reference
const anaphoricEvent = buildTestQueryEvent('Can you fix it?');
const anaphoricResult = policy.classify(anaphoricEvent);
assert.strictEqual(
  anaphoricResult.route,
  CoarseRoute.NEEDS_EVALUATION,
  'Query with anaphoric reference "it" must route to needs-evaluation'
);
assert.strictEqual(anaphoricResult.reasonCode, RoutingReasonCode.CONTEXT_DEPENDENCY_DETECTED);

// 5b. Standalone continuation
const continueEvent = buildTestQueryEvent('continue');
const continueResult = policy.classify(continueEvent);
assert.strictEqual(continueResult.route, CoarseRoute.NEEDS_EVALUATION);
assert.strictEqual(continueResult.reasonCode, RoutingReasonCode.CONTEXT_DEPENDENCY_DETECTED);

// 5c. Follow-up "why did that happen?"
const followUpEvent = buildTestQueryEvent('why did that happen?');
const followUpResult = policy.classify(followUpEvent);
assert.strictEqual(followUpResult.route, CoarseRoute.NEEDS_EVALUATION);
console.log('PASS: Needs-evaluation classifications verified');

// Test 6: Explainability Audit
console.log('Test 6: Explainability audit across all decisions...');
const sampleEvents = [arithmeticEvent, shortCodeEvent, shortSimpleEvent, anaphoricEvent];
for (const evt of sampleEvents) {
  const res = policy.classify(evt);
  assert(typeof res.route === 'string' && res.route.length > 0, 'Must have non-empty route');
  assert(typeof res.ruleId === 'string' && res.ruleId.startsWith('RULE_'), 'Must have ruleId');
  assert(typeof res.reasonCode === 'string' && res.reasonCode.length > 0, 'Must have reasonCode');
  assert(typeof res.explanation === 'string' && res.explanation.length > 10, 'Must have detailed explanation');
  assert(typeof res.confidence === 'number' && res.confidence >= 0 && res.confidence <= 1, 'Valid confidence');
  assert(Array.isArray(res.matchedSignals) && res.matchedSignals.length > 0, 'Must have matchedSignals');
  assert(Array.isArray(res.ruleTrace) && res.ruleTrace.length > 0, 'Must have ruleTrace');
}
console.log('PASS: Explainability audit verified');

// Test 7: Policy Disabled Fallthrough
console.log('Test 7: Policy disabled behavior...');
const disabledPolicy = new DeterministicRoutingPolicy({ enabled: false });
const disabledResult = disabledPolicy.classify(shortCodeEvent);
assert.strictEqual(disabledResult.route, CoarseRoute.NEEDS_EVALUATION);
assert.strictEqual(disabledResult.reasonCode, RoutingReasonCode.POLICY_DISABLED_FALLTHROUGH);
console.log('PASS: Policy disabled behavior verified');

console.log('--- ALL DETERMINISTIC ROUTING POLICY TESTS PASSED ---');
