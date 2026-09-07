/**
 * Smart Query Router - Unit Tests for Initial Complexity Scorer
 * 
 * Verifies:
 * 1. Configuration decoupling, validation, and override mechanics.
 * 2. Dimension sub-scores: length, code, lists, cues, context, task type.
 * 3. Categorical complexity levels: VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH.
 * 4. Signal-only nature: isSignalOnly: true, structured breakdown, human explanation.
 * 5. Non-aggressive threshold scaling without brittle cliff-effects.
 * 6. Integration with QueryEvent, toSafeSummary, and schema validation.
 */

const assert = require('assert');
const {
  DEFAULT_COMPLEXITY_SCORER_CONFIG,
  validateComplexityScorerConfig,
  createComplexityScorerConfig
} = require('../src/shared/complexity_scorer_config');

const {
  ComplexityLevel,
  ComplexityScorer,
  defaultComplexityScorer
} = require('../src/shared/complexity_scorer');

const { extractQueryFeatures } = require('../src/shared/feature_extractor');
const { detectContextDependency } = require('../src/shared/context_detector');
const { defaultTaskClassifier } = require('../src/shared/task_classifier');
const { createDetectedQueryEvent, toSafeSummary, validateQueryEvent } = require('../src/shared/query_event');

console.log('--- Running Initial Complexity Scorer Tests ---');

// Helper to evaluate a prompt through the full feature and scorer pipeline
function scorePrompt(promptText, customScorer = defaultComplexityScorer) {
  const text = promptText.trim();
  const features = extractQueryFeatures(text);
  const contextDep = detectContextDependency(text, features);
  const task = defaultTaskClassifier.classifyTask(text, features, contextDep);

  return customScorer.computeScore({
    promptText: text,
    localFeatures: features,
    contextDependency: contextDep,
    taskClassification: task
  });
}

// 1. Configuration Validation & Decoupling
console.log('Test 1: Configuration validation and decoupling...');
assert.strictEqual(validateComplexityScorerConfig(DEFAULT_COMPLEXITY_SCORER_CONFIG).valid, true);

// Invalid configurations
assert.strictEqual(validateComplexityScorerConfig(null).valid, false);
assert.strictEqual(validateComplexityScorerConfig({}).valid, false);
assert.strictEqual(validateComplexityScorerConfig({ version: '1.0', enabled: true, weights: { length: 0.5 } }).valid, false);

// Weights not summing to 1.0
assert.strictEqual(validateComplexityScorerConfig({
  version: '1.0',
  enabled: true,
  weights: { length: 0.1, code: 0.1, listStructure: 0.1, cues: 0.1, contextDependency: 0.1, taskType: 0.1 },
  thresholds: { veryLow: 0.15, low: 0.35, medium: 0.60, high: 0.80 }
}).valid, false);

// Descending thresholds
assert.strictEqual(validateComplexityScorerConfig({
  version: '1.0',
  enabled: true,
  weights: DEFAULT_COMPLEXITY_SCORER_CONFIG.weights,
  thresholds: { veryLow: 0.50, low: 0.30, medium: 0.60, high: 0.80 }
}).valid, false);

// Overrides via createComplexityScorerConfig
const customConfig = createComplexityScorerConfig({
  thresholds: { veryLow: 0.10, low: 0.30, medium: 0.55, high: 0.75 }
});
assert.strictEqual(customConfig.thresholds.veryLow, 0.10);
assert.strictEqual(customConfig.weights.code, 0.25);
console.log('PASS: Configuration validation and overrides verified');

// 2. Signal-Only Structure & Explanation Guarantee
console.log('Test 2: Signal-only structure and explanation guarantee...');
const sampleResult = scorePrompt('Write a python script to parse CSV files and compute average salaries');
assert.strictEqual(sampleResult.isSignalOnly, true, 'Score must be explicitly marked isSignalOnly: true');
assert.ok(typeof sampleResult.score === 'number' && sampleResult.score >= 0.0 && sampleResult.score <= 1.0);
assert.ok(typeof sampleResult.explanation === 'string' && sampleResult.explanation.length > 10);
assert.ok(sampleResult.breakdown && typeof sampleResult.breakdown === 'object');
assert.ok(sampleResult.breakdown.length);
assert.ok(sampleResult.breakdown.code);
assert.ok(sampleResult.breakdown.listStructure);
assert.ok(sampleResult.breakdown.cues);
assert.ok(sampleResult.breakdown.contextDependency);
assert.ok(sampleResult.breakdown.taskType);
assert.ok(Array.isArray(sampleResult.matchedFactors));
console.log('PASS: Signal-only structure and explanation verified');

// 3. Categorical Complexity Levels
console.log('Test 3: Categorical complexity levels across distinct prompt types...');

// 3a. VERY_LOW: Trivial greeting / single-word pleasantry
const resVeryLow = scorePrompt('Hello');
assert.strictEqual(resVeryLow.level, ComplexityLevel.VERY_LOW, `Expected VERY_LOW for "Hello", got ${resVeryLow.level} (${resVeryLow.score})`);
assert.ok(resVeryLow.score < DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.veryLow);

// 3b. LOW: Brief factual definition query without code or cues
const resLow = scorePrompt('What is photosynthesis?');
assert.strictEqual(resLow.level, ComplexityLevel.LOW, `Expected LOW for "What is photosynthesis?", got ${resLow.level} (${resLow.score})`);
assert.ok(resLow.score >= DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.veryLow && resLow.score < DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.low);

// 3c. MEDIUM: Multi-sentence query with structured list and moderate length
const resMedium = scorePrompt(
  'Summarize the key architectural advantages and disadvantages of using PostgreSQL for web applications with the following list:\n' +
  '1. ACID compliance and strong transactions\n' +
  '2. JSON document support\n' +
  '3. Concurrency with MVCC\n' +
  '4. Rich extension ecosystem'
);
assert.strictEqual(resMedium.level, ComplexityLevel.MEDIUM, `Expected MEDIUM for structured list inquiry, got ${resMedium.level} (${resMedium.score})`);
assert.ok(resMedium.score >= DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.low && resMedium.score < DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.medium);

// 3d. HIGH: Coding task with cognitive cues, code snippet, and substantial length
const resHigh = scorePrompt(
  'Write a Python class implementing a rate limiter using token bucket algorithm with thread safety:\n' +
  '```python\n' +
  'class TokenBucket:\n' +
  '    def __init__(self, capacity: int, fill_rate: float):\n' +
  '        self.capacity = capacity\n' +
  '```\n' +
  'Explain step by step why this approach is better than fixed window.'
);
assert.strictEqual(resHigh.level, ComplexityLevel.HIGH, `Expected HIGH for coding + reasoning cue, got ${resHigh.level} (${resHigh.score})`);
assert.ok(resHigh.score >= DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.medium && resHigh.score < DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.high);

// 3e. VERY_HIGH: Multi-line fenced code block + debugging + stack trace + anaphoric context + list
const resVeryHigh = scorePrompt(
  'In your previous response, the code threw an uncaught TypeError on line 42. Debug and fix it based on the following requirements:\n' +
  '1. Ensure null checks before dereferencing\n' +
  '2. Add proper error handling\n' +
  '3. Return fallback value on failure\n' +
  '```typescript\n' +
  'function processUser(user: User | null): string {\n' +
  '    return user.profile.name;\n' +
  '}\n' +
  '```\n' +
  'Explain why this failed and compare with the alternative implementation.'
);
assert.strictEqual(resVeryHigh.level, ComplexityLevel.VERY_HIGH, `Expected VERY_HIGH for code + debugging + stack trace + context, got ${resVeryHigh.level} (${resVeryHigh.score})`);
assert.ok(resVeryHigh.score >= DEFAULT_COMPLEXITY_SCORER_CONFIG.thresholds.high);
console.log('PASS: Categorical complexity levels (VERY_LOW to VERY_HIGH) verified');

// 4. Individual Feature Dimension Impacts
console.log('Test 4: Individual feature dimension impact isolation...');

// Code Presence impact
const basePrompt = 'Can you help me with a problem today?';
const codePrompt = 'Can you help me with a problem today?\n```javascript\nconst x = 1;\n```';
const resBase = scorePrompt(basePrompt);
const resWithCode = scorePrompt(codePrompt);
assert.ok(resWithCode.score > resBase.score, 'Adding fenced code must increase complexity score');
assert.strictEqual(resWithCode.breakdown.code.factor, 1.0);
assert.strictEqual(resBase.breakdown.code.factor, 0.0);

// Context Dependency impact
const standalonePrompt = 'Explain the difference between TCP and UDP.';
const anaphoricPrompt = 'Explain why it failed in the previous step.';
const resStandalone = scorePrompt(standalonePrompt);
const resAnaphoric = scorePrompt(anaphoricPrompt);
assert.strictEqual(resStandalone.breakdown.contextDependency.factor, 0.0);
assert.strictEqual(resAnaphoric.breakdown.contextDependency.factor, 0.9);

// Cognitive Cues impact
const promptWithoutCue = 'Tell me about solar energy generation.';
const promptWithCue = 'Explain step by step why solar energy is better than fossil fuels.';
const resNoCue = scorePrompt(promptWithoutCue);
const resWithCue = scorePrompt(promptWithCue);
assert.ok(resWithCue.breakdown.cues.contribution > resNoCue.breakdown.cues.contribution);

console.log('PASS: Dimension impacts verified');

// 5. Non-Aggressive Threshold & Smooth Scaling
console.log('Test 5: Non-aggressive threshold and smooth scaling...');
// A prompt with just 1 or 2 conversational words must not jump to HIGH
const shortConv = scorePrompt('Thanks a lot Claude!');
assert.ok(shortConv.score <= 0.15, 'Polite short greeting must stay very low');

// A prompt of 40 words with simple language must scale smoothly into LOW/MEDIUM, not spike to VERY_HIGH
const mediumPlain = scorePrompt(
  'I am planning a trip to Japan next spring and would like to know the best time ' +
  'to view cherry blossoms in Kyoto and Tokyo. Could you provide some general advice ' +
  'on weather and clothing recommendations for that time of year?'
);
assert.ok(mediumPlain.score >= 0.08 && mediumPlain.score <= 0.35, `Expected smooth moderate score, got ${mediumPlain.score}`);
console.log('PASS: Non-aggressive smooth scaling verified');

// 6. Disabled Scorer Behavior
console.log('Test 6: Disabled scorer behavior...');
const disabledScorer = new ComplexityScorer({ enabled: false });
const resDisabled = scorePrompt('Complex query with code', disabledScorer);
assert.strictEqual(resDisabled.score, 0.0);
assert.strictEqual(resDisabled.level, ComplexityLevel.VERY_LOW);
assert.ok(resDisabled.matchedFactors.includes('SCORER_DISABLED'));
console.log('PASS: Disabled scorer behavior verified');

// 7. Integration with QueryEvent & toSafeSummary
console.log('Test 7: Integration with QueryEvent and toSafeSummary...');
const queryEvent = createDetectedQueryEvent({
  rawPrompt: 'Write a python script to parse CSV files and calculate totals',
  triggerType: 'keyboard_enter'
});

assert.ok(queryEvent.complexity, 'queryEvent must attach complexity partition');
assert.ok(typeof queryEvent.complexity.score === 'number');
assert.ok(queryEvent.complexity.level);
assert.strictEqual(queryEvent.complexity.isSignalOnly, true);

// Schema validation
const validation = validateQueryEvent(queryEvent);
assert.strictEqual(validation.valid, true, `validateQueryEvent failed: ${validation.error}`);

// toSafeSummary
const safeSummary = toSafeSummary(queryEvent);
assert.strictEqual(safeSummary.complexityScore, queryEvent.complexity.score);
assert.strictEqual(safeSummary.complexityLevel, queryEvent.complexity.level);
assert.strictEqual(safeSummary.rawPrompt, undefined, 'Must not leak rawPrompt in safeSummary');
console.log('PASS: QueryEvent integration and safe summary verified');

console.log('--- ALL INITIAL COMPLEXITY SCORER TESTS PASSED ---');
