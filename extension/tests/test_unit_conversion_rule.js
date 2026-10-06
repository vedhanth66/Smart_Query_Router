/**
 * Automated Unit Test Suite for Deterministic Unit & Temperature Conversion Rule
 * Verifies exact conversions across physical & digital dimensions,
 * conservative rejection of currencies, cryptocurrencies, and code queries,
 * and integration with OptimizationDecisionEngine.
 */

const assert = require('assert');
const {
  evaluateUnitConversion,
  unitConversionRule,
  RULE_ID,
  UnitCategory
} = require('../src/rules/unit_conversion_rule');

const {
  DecisionOutcome,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Deterministic Unit Conversion Rule Tests ---');

// Test 1: Temperature Conversions (F, C, K)
console.log('Test 1: Temperature conversions...');
const tempCases = [
  { input: '100 F to C', expectedFrom: '100 °F', expectedTo: '37.778 °C' },
  { input: '32 F in C', expectedFrom: '32 °F', expectedTo: '0 °C' },
  { input: '0 C to F', expectedFrom: '0 °C', expectedTo: '32 °F' },
  { input: '100 C to F', expectedFrom: '100 °C', expectedTo: '212 °F' },
  { input: '300 K to C', expectedFrom: '300 K', expectedTo: '26.85 °C' },
  { input: 'convert 68 fahrenheit to celsius', expectedFrom: '68 °F', expectedTo: '20 °C' }
];

for (const { input, expectedFrom, expectedTo } of tempCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.category, UnitCategory.TEMPERATURE);
  assert.strictEqual(res.expression, expectedFrom, `Expression mismatch for "${input}"`);
  assert.strictEqual(res.result, expectedTo, `Result mismatch for "${input}"`);
}
console.log('PASS: All temperature conversions evaluated accurately');

// Test 2: Length and Distance Conversions (km, mi, ft, m, in, cm)
console.log('Test 2: Length and distance conversions...');
const lengthCases = [
  { input: '15 km to miles', expectedFrom: '15 km', expectedTo: '9.321 miles' },
  { input: '15km in mi', expectedFrom: '15 km', expectedTo: '9.321 miles' },
  { input: '6 feet in cm', expectedFrom: '6 ft', expectedTo: '182.88 cm' },
  { input: '10 meters to feet', expectedFrom: '10 m', expectedTo: '32.808 ft' },
  { input: '1 mile to km', expectedFrom: '1 miles', expectedTo: '1.609 km' },
  { input: '100 cm to m', expectedFrom: '100 cm', expectedTo: '1 m' }
];

for (const { input, expectedFrom, expectedTo } of lengthCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.category, UnitCategory.LENGTH);
  assert.strictEqual(res.expression, expectedFrom);
  assert.strictEqual(res.result, expectedTo);
}
console.log('PASS: All length conversions evaluated accurately');

// Test 3: Mass and Weight Conversions (kg, lbs, oz, g)
console.log('Test 3: Mass and weight conversions...');
const massCases = [
  { input: '70 kg to lbs', expectedFrom: '70 kg', expectedTo: '154.32 lbs' },
  { input: '16 oz to grams', expectedFrom: '16 oz', expectedTo: '453.59 g' },
  { input: '1 kg to grams', expectedFrom: '1 kg', expectedTo: '1000 g' },
  { input: '1000 mg to g', expectedFrom: '1000 mg', expectedTo: '1 g' }
];

for (const { input, expectedFrom, expectedTo } of massCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.category, UnitCategory.MASS);
  assert.strictEqual(res.expression, expectedFrom);
  assert.strictEqual(res.result, expectedTo);
}
console.log('PASS: All mass conversions evaluated accurately');

// Test 4: Speed, Digital Storage, Volume, Time
console.log('Test 4: Speed, storage, volume, time...');
const miscCases = [
  { input: '60 mph to kmh', cat: UnitCategory.SPEED, expectedFrom: '60 mph', expectedTo: '96.561 km/h' },
  { input: '500 GB to TB', cat: UnitCategory.DIGITAL_STORAGE, expectedFrom: '500 GB', expectedTo: '0.4883 TB' },
  { input: '16 MB in KB', cat: UnitCategory.DIGITAL_STORAGE, expectedFrom: '16 MB', expectedTo: '16384 KB' },
  { input: '1 gallon to liters', cat: UnitCategory.VOLUME, expectedFrom: '1 gallons', expectedTo: '3.785 L' },
  { input: '2 hours to minutes', cat: UnitCategory.TIME, expectedFrom: '2 hours', expectedTo: '120 minutes' },
  { input: '3 days to hours', cat: UnitCategory.TIME, expectedFrom: '3 days', expectedTo: '72 hours' }
];

for (const { input, cat, expectedFrom, expectedTo } of miscCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.category, cat);
  assert.strictEqual(res.expression, expectedFrom);
  assert.strictEqual(res.result, expectedTo);
}
console.log('PASS: All speed, storage, volume, and time conversions evaluated accurately');

// Test 5: Inverted Natural Query Formats ("how many X in Y")
console.log('Test 5: Inverted query phrasing...');
const invertedCases = [
  { input: 'how many miles in 15 km?', expectedFrom: '15 km', expectedTo: '9.321 miles' },
  { input: 'how many cm is 6 feet?', expectedFrom: '6 ft', expectedTo: '182.88 cm' },
  { input: 'how many grams are in 5 lbs?', expectedFrom: '5 lbs', expectedTo: '2267.96 g' },
  { input: 'how many minutes in 2 hours', expectedFrom: '2 hours', expectedTo: '120 minutes' }
];

for (const { input, expectedFrom, expectedTo } of invertedCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected "${input}" to evaluate successfully`);
  assert.strictEqual(res.expression, expectedFrom);
  assert.strictEqual(res.result, expectedTo);
}
console.log('PASS: Inverted query phrasing evaluated accurately');

// Test 6: Zero-width space & Unicode sanitization
console.log('Test 6: Zero-width characters & Unicode sanitization...');
const zwCases = [
  { input: '\u200B100 F to C\u200B', expectedFrom: '100 °F', expectedTo: '37.778 °C' },
  { input: '\uFEFF15 km to miles\u200C', expectedFrom: '15 km', expectedTo: '9.321 miles' },
  { input: '\u200Bhow many miles in 15 km?\u200B', expectedFrom: '15 km', expectedTo: '9.321 miles' }
];

for (const { input, expectedFrom, expectedTo } of zwCases) {
  const res = evaluateUnitConversion(input);
  assert(res !== null, `Expected zero-width input "${input}" to evaluate successfully`);
  assert.strictEqual(res.expression, expectedFrom);
  assert.strictEqual(res.result, expectedTo);
}
console.log('PASS: Zero-width spaces sanitized cleanly');

// Test 7: Strict rejection of currencies, cryptos, and general reasoning
console.log('Test 7: Rejection of currencies, cryptos, and code...');
const rejectedCases = [
  '$50 to EUR',
  '100 USD in GBP',
  '5000 yen to dollars',
  'how much is 100 rupees in usd',
  '1 btc to usd',
  'how much is 5 ethereum in usd',
  'how to convert miles to km in python',
  'what is the history of the metric system',
  'explain temperature conversion formula'
];

for (const input of rejectedCases) {
  const res = evaluateUnitConversion(input);
  assert.strictEqual(res, null, `Expected rejected query "${input}" to return null`);
}
console.log('PASS: Currencies, cryptos, and general questions strictly rejected');

// Test 8: Decision Engine Integration
console.log('Test 8: OptimizationDecisionEngine integration...');
const engine = new OptimizationDecisionEngine();
engine.registerRule(unitConversionRule);

const convEvent = createDetectedQueryEvent({
  rawPrompt: '100 F to C',
  triggerType: 'keyboard_enter'
});
const decision = engine.evaluate(convEvent);
assert.strictEqual(decision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(decision.ruleId, RULE_ID);
assert.strictEqual(decision.metadata.category, UnitCategory.TEMPERATURE);
assert.strictEqual(decision.metadata.expression, '100 °F');
assert.strictEqual(decision.metadata.result, '37.778 °C');

const ineligibleEvent = createDetectedQueryEvent({
  rawPrompt: '$100 to EUR',
  triggerType: 'keyboard_enter'
});
const inelDecision = engine.evaluate(ineligibleEvent);
assert.strictEqual(inelDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
console.log('PASS: OptimizationDecisionEngine integrated cleanly with unitConversionRule');

console.log('--- ALL UNIT CONVERSION RULE TESTS PASSED ---');
