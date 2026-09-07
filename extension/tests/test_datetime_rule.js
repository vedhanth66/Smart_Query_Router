/**
 * Automated Unit Test Suite for Deterministic Date/Time Rule
 * Verifies classification of simple local date/time utility requests,
 * strict rejection of external facts (weather, news, stocks, remote locations),
 * and integration with the OptimizationDecisionEngine.
 */

const assert = require('assert');
const {
  classifyDateTimeQuery,
  getBrowserExposedTimezone,
  dateTimeRule,
  RULE_ID,
  DateTimeCategory
} = require('../src/rules/datetime_rule');

const {
  DecisionOutcome,
  OptimizationDecisionEngine
} = require('../src/shared/decision_engine');

const { createDetectedQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Deterministic Date/Time Rule Tests ---');

// Test 1: Eligible simple local date/time requests
console.log('Test 1: Classification of eligible local date/time requests...');
const eligibleCases = [
  { input: 'what time is it?', expectedCat: DateTimeCategory.CURRENT_TIME },
  { input: "what's the time right now?", expectedCat: DateTimeCategory.CURRENT_TIME },
  { input: 'current time', expectedCat: DateTimeCategory.CURRENT_TIME },
  { input: 'what is the local time?', expectedCat: DateTimeCategory.CURRENT_TIME },
  { input: "what is today's date?", expectedCat: DateTimeCategory.CURRENT_DATE },
  { input: 'today date', expectedCat: DateTimeCategory.CURRENT_DATE },
  { input: 'what day is it today?', expectedCat: DateTimeCategory.CURRENT_DAY_OF_WEEK },
  { input: 'what year is it?', expectedCat: DateTimeCategory.CURRENT_YEAR },
  { input: 'what is my timezone?', expectedCat: DateTimeCategory.BROWSER_TIMEZONE },
  { input: 'current timezone', expectedCat: DateTimeCategory.BROWSER_TIMEZONE }
];

for (const { input, expectedCat } of eligibleCases) {
  const res = classifyDateTimeQuery(input);
  assert.strictEqual(res.eligible, true, `Expected "${input}" to be classified as eligible`);
  assert.strictEqual(res.category, expectedCat, `Expected category ${expectedCat}, got ${res.category}`);
  assert(res.reason.includes('Eligible'), 'Reason notes eligibility');
}
console.log('PASS: All standard local date/time queries classified as eligible');

// Test 2: Rejection of queries requiring external current facts (weather, news, stocks)
console.log('Test 2: Rejection of external dynamic facts (weather, news, stocks)...');
const externalFactCases = [
  'what is the weather today?',
  'what is the weather forecast for tomorrow?',
  'current temperature outside',
  'what is the latest news headlines?',
  'what is the stock price of Apple?',
  'how much is bitcoin worth today?',
  'what is the score of the game?'
];

for (const input of externalFactCases) {
  const res = classifyDateTimeQuery(input);
  assert.strictEqual(res.eligible, false, `Expected external fact query "${input}" to be ineligible`);
  assert(res.reason.includes('external dynamic facts'), 'Reason cites external dynamic facts');
}
console.log('PASS: External current facts (weather/news/stocks) strictly rejected');

// Test 3: Rejection of remote geographic locations (Tokyo, London, etc.)
console.log('Test 3: Rejection of remote geographic time requests...');
const remoteLocationCases = [
  'what time is it in Tokyo?',
  'current time in London',
  'what is the time for Paris?',
  'what time is it in New York?'
];

for (const input of remoteLocationCases) {
  const res = classifyDateTimeQuery(input);
  assert.strictEqual(res.eligible, false, `Expected remote city query "${input}" to be ineligible`);
  assert(res.reason.includes('remote geographic location'), 'Reason cites remote location');
}
console.log('PASS: Remote geographic location queries strictly rejected');

// Test 4: Rejection of general reasoning and conversational queries
console.log('Test 4: Rejection of general conversation and temporal reasoning...');
const generalCases = [
  'Can you explain how atomic clocks measure time?',
  'What was the date of the Battle of Waterloo?',
  'What will the time be in 5 hours and 30 minutes?',
  'Tell me a poem about the passage of time'
];

for (const input of generalCases) {
  const res = classifyDateTimeQuery(input);
  assert.strictEqual(res.eligible, false, `Expected general query "${input}" to be ineligible`);
}
console.log('PASS: General conversation and relative time calculations rejected');

// Test 5: Browser timezone reporting without external lookup
console.log('Test 5: Browser timezone inspection...');
const tz = getBrowserExposedTimezone();
assert(typeof tz === 'string' && tz.length > 0, 'Browser timezone must be a non-empty string');
console.log(`PASS: Browser timezone obtained deterministically: ${tz}`);

// Test 6: OptimizationDecisionEngine integration
console.log('Test 6: OptimizationDecisionEngine integration with dateTimeRule...');
const engine = new OptimizationDecisionEngine();
engine.registerRule(dateTimeRule);

// Case A: Eligible local time query
const timeEvent = createDetectedQueryEvent({
  rawPrompt: 'what time is it?',
  triggerType: 'keyboard_enter'
});
const timeDecision = engine.evaluate(timeEvent);
assert.strictEqual(timeDecision.outcome, DecisionOutcome.LOCAL_ANSWER_CANDIDATE);
assert.strictEqual(timeDecision.ruleId, RULE_ID);
assert.strictEqual(timeDecision.metadata.category, DateTimeCategory.CURRENT_TIME);
assert.strictEqual(timeDecision.metadata.browserTimezone, tz);
assert(timeDecision.metadata.eligibilityReason.length > 10);
console.log('PASS: Local time query marked as LOCAL_ANSWER_CANDIDATE with eligibility metadata');

// Case B: Ineligible query falls back to baseline
const weatherEvent = createDetectedQueryEvent({
  rawPrompt: 'what is the weather right now?',
  triggerType: 'keyboard_enter'
});
const weatherDecision = engine.evaluate(weatherEvent);
assert.strictEqual(weatherDecision.outcome, DecisionOutcome.NO_OPTIMIZATION);
console.log('PASS: Ineligible query falls through to baseline NO_OPTIMIZATION');

console.log('--- ALL DETERMINISTIC DATE/TIME TESTS PASSED ---');
