/**
 * Automated Unit Test Suite for Deterministic Arithmetic Rule
 * Verifies exact arithmetic evaluation without eval(),
 * conservative rejection of word problems, units, variables, and division by zero,
 * and integration with the OptimizationDecisionEngine.
 */

const assert = require('assert');
const {
  evaluateDeterministicArithmetic,
  arithmeticRule,
  RULE_ID
} = require('../src/rules/arithmetic_rule');

const {
  DecisionOutcome,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Deterministic Arithmetic Rule Tests ---');

// Test 1: Valid deterministic expressions evaluate correctly
console.log('Test 1: Valid deterministic expressions evaluation...');
const validCases = [
  { input: '2 + 2', expected: 4 },
  { input: '2 + 3 * 4', expected: 14 },
  { input: '(100 - 25) / 5', expected: 15 },
  { input: '45.5 + 12.3', expected: 57.8 },
  { input: '2 ^ 8', expected: 256 },
  { input: '10 % 3', expected: 1 },
  { input: '-5 + 10', expected: 5 },
  { input: 'what is 15 * 4?', expected: 60 },
  { input: 'calculate 125 / 5 =', expected: 25 },
  { input: 'compute (10 + 20) * (30 - 25)', expected: 150 }
];

for (const { input, expected } of validCases) {
  const res = evaluateDeterministicArithmetic(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.result, expected, `Expected "${input}" = ${expected}, got ${res.result}`);
}
console.log('PASS: All valid deterministic expressions evaluated accurately');

// Test 2: Conservative rejection of natural language and word problems
console.log('Test 2: Conservative rejection of natural language...');
const naturalLanguageCases = [
  'If a train travels 60 mph for 2 hours, what is the distance?',
  'What is the square root of 144?',
  'Explain why 1 + 1 equals 2',
  'Tell me a story about 3 bears and 1 bowl'
];

for (const input of naturalLanguageCases) {
  const res = evaluateDeterministicArithmetic(input);
  assert.strictEqual(res, null, `Expected word problem "${input}" to be rejected`);
}
console.log('PASS: Natural language and word problems strictly rejected');

// Test 3: Conservative rejection of units, currency, and variables
console.log('Test 3: Conservative rejection of units and variables...');
const unitCases = [
  '5 km to miles',
  '$50 + $20',
  '100px - 20px',
  'x + 5 = 10',
  '2 + 2 in base 3',
  '5 kg * 10'
];

for (const input of unitCases) {
  const res = evaluateDeterministicArithmetic(input);
  assert.strictEqual(res, null, `Expected unit/currency query "${input}" to be rejected`);
}
console.log('PASS: Units, currency, and variables strictly rejected');

// Test 4: Rejection of division by zero, invalid numbers, and standalone values
console.log('Test 4: Rejection of division by zero and invalid syntax...');
const invalidSyntaxCases = [
  '10 / 0',               // Division by zero
  '10 % 0',               // Modulo by zero
  '42',                   // Standalone number (not an expression)
  '1.2.3 + 4',            // Malformed float
  '(2 + 3',               // Unbalanced parenthesis
  '2 + * 3',              // Consecutive operators
  '',                     // Empty
  '    '                  // Whitespace only
];

for (const input of invalidSyntaxCases) {
  const res = evaluateDeterministicArithmetic(input);
  assert.strictEqual(res, null, `Expected invalid syntax "${input}" to be rejected`);
}
console.log('PASS: Division by zero and invalid syntax safely rejected');

// Test 5: Integration with OptimizationDecisionEngine
console.log('Test 5: Decision engine integration with arithmeticRule...');
const engine = new OptimizationDecisionEngine();
engine.registerRule(arithmeticRule);

// Case A: Arithmetic query produces LOCAL_ANSWER_CANDIDATE
const mathEvent = createDetectedQueryEvent({
  rawPrompt: 'what is 25 * 4?',
  triggerType: 'keyboard_enter'
});

const mathDecision = engine.evaluate(mathEvent);
assert.strictEqual(mathDecision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(mathDecision.ruleId, RULE_ID);
assert.strictEqual(mathDecision.metadata.calculatedResult, 100);
assert(mathDecision.reason.includes('100'));
console.log('PASS: Arithmetic query marked as LOCAL_ANSWER_CANDIDATE');

// Case B: General coding query falls back to NO_OPTIMIZATION
const codeEvent = createDetectedQueryEvent({
  rawPrompt: 'Write a quicksort function in TypeScript',
  triggerType: 'keyboard_enter'
});

const codeDecision = engine.evaluate(codeEvent);
assert.strictEqual(codeDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
console.log('PASS: Non-arithmetic query falls through to baseline NO_OPTIMIZATION');

console.log('--- ALL DETERMINISTIC ARITHMETIC TESTS PASSED ---');
