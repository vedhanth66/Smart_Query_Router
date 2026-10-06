/**
 * Automated Unit Test Suite for Deterministic Advanced Calculator Rule
 *
 * Verifies on-device evaluation across 5 computational domains:
 * 1. Percentages & Financial Calculations
 * 2. Scientific & Combinatoric Mathematics
 * 3. Programmer Bases, Bitwise & CIDR Subnetting
 * 4. Summary Statistics on Lists
 * 5. Geometry & Mensuration
 * 6. Strict boundaries (rejecting live currency rates, crypto, code questions, essays)
 * 7. DecisionEngine plugin integration
 */

const assert = require('assert');
const {
  RULE_ID,
  AdvancedCalcCategory,
  evaluateAdvancedCalculation,
  advancedCalculatorRule
} = require('../src/rules/advanced_calculator_rule');

const {
  DecisionOutcome,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Deterministic Advanced Calculator Rule Tests ---');

// Test 1: Percentages & Financial Math
console.log('Test 1: Percentages & Financial Math...');
const percentageCases = [
  { input: '15% of 850', expectedRes: '127.5' },
  { input: 'what is 20% of 1500?', expectedRes: '300' },
  { input: 'how much is 12.5% of $240', expectedRes: '$30' },
  { input: 'what percentage is 45 of 200?', expectedRes: '22.5%' },
  { input: '45 as a percent of 200', expectedRes: '22.5%' },
  { input: 'percentage increase from 80 to 120', expectedRes: '+50%' },
  { input: 'percent decrease from 150 to 90', expectedRes: '-40%' },
  { input: '250 + 18%', expectedRes: '295' },
  { input: '120 - 20%', expectedRes: '96' },
  { input: 'tip on $85 at 18%', expectedRes: 'Tip: $15.30 | Total: $100.30' },
  { input: 'split $180 by 4', expectedRes: '$45.00 per person' },
  { input: 'split 180 among 4', expectedRes: '45.00 per person' }
];

for (const { input, expectedRes } of percentageCases) {
  const res = evaluateAdvancedCalculation(input);
  assert(res, `Expected calculation result for "${input}"`);
  assert.strictEqual(res.category, AdvancedCalcCategory.PERCENTAGE);
  assert.strictEqual(res.result, expectedRes, `Expected "${expectedRes}", got "${res.result}" for "${input}"`);
}
console.log('PASS: All Percentage and Financial math cases verified');

// Test 2: Scientific & Combinatorics
console.log('Test 2: Scientific & Combinatorics...');
const scientificCases = [
  { input: 'sqrt(144)', expectedRes: '12' },
  { input: 'what is the square root of 144?', expectedRes: '12' },
  { input: 'cbrt(125)', expectedRes: '5' },
  { input: 'cube root of 125', expectedRes: '5' },
  { input: '5!', expectedRes: '120' },
  { input: 'factorial of 5', expectedRes: '120' },
  { input: '10!', expectedRes: '3,628,800' },
  { input: 'nCr(5, 2)', expectedRes: '10' },
  { input: '5 choose 2', expectedRes: '10' },
  { input: 'nPr(5, 2)', expectedRes: '20' },
  { input: 'log(100)', expectedRes: '2' },
  { input: 'log2(256)', expectedRes: '8' },
  { input: 'ln(e)', expectedRes: '1' },
  { input: 'sin(90 deg)', expectedRes: '1' },
  { input: 'cos(0)', expectedRes: '1' },
  { input: 'tan(45 deg)', expectedRes: '1' },
  { input: 'gcd(24, 36)', expectedRes: '12' },
  { input: 'greatest common divisor of 24 and 36', expectedRes: '12' },
  { input: 'lcm(12, 18)', expectedRes: '36' },
  { input: 'least common multiple of 12 and 18', expectedRes: '36' }
];

for (const { input, expectedRes } of scientificCases) {
  const res = evaluateAdvancedCalculation(input);
  assert(res, `Expected scientific calculation for "${input}"`);
  assert.strictEqual(res.category, AdvancedCalcCategory.SCIENTIFIC);
  assert.strictEqual(res.result, expectedRes, `Expected "${expectedRes}", got "${res.result}" for "${input}"`);
}
console.log('PASS: All Scientific & Combinatoric cases verified');

// Test 3: Programmer Bases, Bitwise & CIDR
console.log('Test 3: Programmer Bases, Bitwise & CIDR...');
const programmerCases = [
  { input: 'hex to dec 0xFF', expectedRes: '255' },
  { input: '0xFF to decimal', expectedRes: '255' },
  { input: '42 to binary', expectedRes: '0b101010' },
  { input: 'bin to dec 1101', expectedRes: '13' },
  { input: 'dec to hex 255', expectedRes: '0xFF' },
  { input: '0xFF & 0x0F', expectedRes: '15 (0x0F)' },
  { input: '1 << 10', expectedRes: '1024' },
  { input: '/24 subnet mask', expectedRes: '255.255.255.0 (256 IPs, 254 usable)' },
  { input: '/16 subnet mask', expectedRes: '255.255.0.0 (65,536 IPs, 65,534 usable)' }
];

for (const { input, expectedRes } of programmerCases) {
  const res = evaluateAdvancedCalculation(input);
  assert(res, `Expected programmer calculation for "${input}"`);
  assert.strictEqual(res.category, AdvancedCalcCategory.PROGRAMMER);
  assert.strictEqual(res.result, expectedRes, `Expected "${expectedRes}", got "${res.result}" for "${input}"`);
}
console.log('PASS: All Programmer & CIDR subnet cases verified');

// Test 4: Summary Statistics
console.log('Test 4: Summary Statistics on Number Lists...');
const statsCases = [
  { input: 'mean of 12, 18, 24, 30', expectedRes: '21' },
  { input: 'average of 10, 20, 30', expectedRes: '20' },
  { input: 'median of 5, 2, 9, 1, 7', expectedRes: '5' },
  { input: 'median of 10, 20, 30, 40', expectedRes: '25' },
  { input: 'min and max of 45, 12, 89, 3', expectedRes: 'Min: 3 | Max: 89 | Range: 86' },
  { input: 'standard deviation of 4, 8, 6, 5, 3', expectedRes: '1.9235' }
];

for (const { input, expectedRes } of statsCases) {
  const res = evaluateAdvancedCalculation(input);
  assert(res, `Expected statistics calculation for "${input}"`);
  assert.strictEqual(res.category, AdvancedCalcCategory.STATISTICS);
  assert.strictEqual(res.result, expectedRes, `Expected "${expectedRes}", got "${res.result}" for "${input}"`);
}
console.log('PASS: All Statistics cases verified');

// Test 5: Geometry & Mensuration
console.log('Test 5: Geometry & Mensuration...');
const geometryCases = [
  { input: 'area of circle radius 5', expectedRes: '78.5398' },
  { input: 'circumference of circle radius 10', expectedRes: '62.8319' },
  { input: 'hypotenuse of 3 and 4', expectedRes: '5' },
  { input: 'what is the hypotenuse of 3 and 4', expectedRes: '5' },
  { input: 'area of rectangle 12 by 8', expectedRes: '96' },
  { input: 'perimeter of rectangle 12 by 8', expectedRes: '40' }
];

for (const { input, expectedRes } of geometryCases) {
  const res = evaluateAdvancedCalculation(input);
  assert(res, `Expected geometry calculation for "${input}"`);
  assert.strictEqual(res.category, AdvancedCalcCategory.GEOMETRY);
  assert.strictEqual(res.result, expectedRes, `Expected "${expectedRes}", got "${res.result}" for "${input}"`);
}
console.log('PASS: All Geometry cases verified');

// Test 6: Strict Boundary Rejections
console.log('Test 6: Strict Boundary Rejections...');
const prohibitedCases = [
  '100 usd to eur',
  'convert usd to inr',
  'current bitcoin price',
  'how to calculate sqrt in python',
  'write code for gcd in javascript',
  'explain why 5 choose 2 is 10',
  'proof of pythagorean theorem',
  'sqrt(-16)',
  '200!' // overflows float max
];

for (const input of prohibitedCases) {
  const res = evaluateAdvancedCalculation(input);
  assert.strictEqual(res, null, `Expected query "${input}" to be rejected by on-device calculator`);
}
console.log('PASS: External currencies, code queries, and essays strictly rejected');

// Test 7: Optimization Decision Engine Plugin Integration
console.log('Test 7: Optimization Decision Engine Plugin Integration...');
const engine = new OptimizationDecisionEngine();
engine.registerRule(advancedCalculatorRule);

const event = createDetectedQueryEvent({
  rawPrompt: '15% of 850',
  triggerType: 'keyboard_enter'
});

const decision = engine.evaluate(event);
assert.strictEqual(decision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(decision.ruleId, RULE_ID);
assert.strictEqual(decision.metadata.category, AdvancedCalcCategory.PERCENTAGE);
assert.strictEqual(decision.metadata.result, '127.5');
console.log('PASS: Advanced calculator integrated into DecisionEngine seamlessly');

console.log('--- ALL ADVANCED CALCULATOR RULE TESTS PASSED ---');
