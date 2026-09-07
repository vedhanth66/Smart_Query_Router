/**
 * Automated Unit Test Suite for Conversational Greeting Rule
 * Verifies classification of obvious greetings and pleasantries,
 * near-matches, and normal substantive queries containing greeting words.
 * Verifies configurability and integration with OptimizationDecisionEngine.
 */

const assert = require('assert');
const {
  classifyGreeting,
  createGreetingRule,
  greetingRule,
  RULE_ID,
  GreetingType
} = require('../src/rules/greeting_rule');

const {
  DecisionOutcome,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Conversational Greeting Rule Tests ---');

// Test 1: Obvious greetings and pleasantries
console.log('Test 1: Classification of obvious greetings and pleasantries...');
const obviousGreetings = [
  { input: 'hi', expectedType: GreetingType.GREETING },
  { input: 'hello', expectedType: GreetingType.GREETING },
  { input: 'hey!', expectedType: GreetingType.GREETING },
  { input: 'hello claude', expectedType: GreetingType.GREETING },
  { input: 'hi there', expectedType: GreetingType.GREETING },
  { input: 'good morning', expectedType: GreetingType.GREETING },
  { input: 'good evening claude', expectedType: GreetingType.GREETING },
  { input: 'how are you?', expectedType: GreetingType.PLEASANTRY },
  { input: 'how are you doing?', expectedType: GreetingType.PLEASANTRY },
  { input: "how's it going", expectedType: GreetingType.PLEASANTRY },
  { input: 'thanks', expectedType: GreetingType.SIGN_OFF },
  { input: 'thank you so much!', expectedType: GreetingType.SIGN_OFF },
  { input: 'bye', expectedType: GreetingType.SIGN_OFF },
  { input: 'have a great day claude', expectedType: GreetingType.SIGN_OFF }
];

for (const { input, expectedType } of obviousGreetings) {
  const res = classifyGreeting(input);
  assert.strictEqual(res.isGreeting, true, `Expected "${input}" to be recognized as a greeting`);
  assert.strictEqual(res.type, expectedType, `Expected type ${expectedType}, got ${res.type}`);
  assert(res.reason.includes('Eligible'), 'Reason notes eligibility');
}
console.log('PASS: All obvious greetings and pleasantries recognized');

// Test 2: Near-matches (words starting with hi/hey/hell that are not greetings)
console.log('Test 2: Rejection of near-matches...');
const nearMatches = [
  'hide and seek',
  'history of the Roman empire',
  'hike in Yosemite',
  'hell no',
  'highlight the main points',
  'heyday of rock and roll',
  'him and her',
  'hit the ball'
];

for (const input of nearMatches) {
  const res = classifyGreeting(input);
  assert.strictEqual(res.isGreeting, false, `Expected near-match "${input}" to be rejected`);
}
console.log('PASS: Near-matches strictly rejected');

// Test 3: Normal substantive queries containing greeting words
console.log('Test 3: Rejection of substantive queries containing greeting words...');
const substantiveQueriesWithGreetings = [
  'Hello, can you help me refactor this Python function?',
  'Hi, what is the capital of France?',
  'Hey Claude, write a bash script to backup my directory',
  'Show me a hello world example in Rust',
  'Explain how "good morning" is translated into Japanese',
  'How are you able to process visual images in multimodality?',
  'Thank you for the advice, now please generate the unit tests',
  'Hello! Please tell me why the sky is blue',
  'Good morning routine for high productivity and focus'
];

for (const input of substantiveQueriesWithGreetings) {
  const res = classifyGreeting(input);
  assert.strictEqual(
    res.isGreeting,
    false,
    `Substantive query containing greeting "${input}" must NOT be classified as trivial greeting`
  );
  assert(res.reason.includes('Ineligible'), 'Reason indicates ineligibility');
}
console.log('PASS: Substantive queries containing greeting words strictly preserved for model inference');

// Test 4: Custom configurability
console.log('Test 4: Configurable custom rule set...');
const customRule = createGreetingRule({
  standaloneGreetings: ['salut', 'yo']
});

// "yo" should be accepted with custom rule
const customEvent = createDetectedQueryEvent({ rawPrompt: 'yo', triggerType: 'keyboard_enter' });
const customDecision = customRule.evaluate(customEvent);
assert.strictEqual(customDecision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(customDecision.metadata.matchedPhrase, 'yo');

// "yo" is rejected by default rule (not in default English whitelist)
const defaultClassification = classifyGreeting('yo');
assert.strictEqual(defaultClassification.isGreeting, false);
console.log('PASS: Custom configuration verified');

// Test 5: OptimizationDecisionEngine integration
console.log('Test 5: OptimizationDecisionEngine integration with greetingRule...');
const engine = new OptimizationDecisionEngine();
engine.registerRule(greetingRule);

// Case A: Standalone greeting produces LOCAL_ANSWER_CANDIDATE
const greetingEvent = createDetectedQueryEvent({
  rawPrompt: 'hello claude!',
  triggerType: 'keyboard_enter'
});
const decision = engine.evaluate(greetingEvent);
assert.strictEqual(decision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(decision.ruleId, RULE_ID);
assert.strictEqual(decision.metadata.greetingType, GreetingType.GREETING);
assert.strictEqual(decision.metadata.isTrivial, true);
assert.strictEqual(decision.metadata.actionable, false);

// Case B: Substantive coding query containing greeting falls through to baseline
const codingGreetingEvent = createDetectedQueryEvent({
  rawPrompt: 'Hi Claude, write an async generator in Node.js',
  triggerType: 'keyboard_enter'
});
const codingDecision = engine.evaluate(codingGreetingEvent);
assert.strictEqual(codingDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
console.log('PASS: Decision engine integration correctly routes trivial vs substantive queries');

console.log('--- ALL GREETING RULE TESTS PASSED ---');
