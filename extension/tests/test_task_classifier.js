/**
 * Smart Query Router - Unit Tests for Conservative Task Classifier
 * 
 * Verifies:
 * 1. Exact 13 stable task categories defined on TaskCategory enum.
 * 2. Accurate classification across representative fixtures for each category:
 *    - greeting
 *    - arithmetic
 *    - factual question
 *    - summarization
 *    - rewriting
 *    - translation
 *    - creative writing
 *    - coding
 *    - debugging
 *    - comparison
 *    - analysis
 *    - reasoning
 *    - unknown
 * 3. Strict Non-Prescriptive Boundary:
 *    - Task classifier NEVER returns a model, route, or model recommendation.
 *    - Task classifier ONLY returns category, confidence, reason, and matchedSignals.
 * 4. Integration with QueryEvent and DeterministicRoutingPolicy:
 *    - QueryEvent automatically attaches taskClassification signal.
 *    - toSafeSummary includes taskCategory without leaking prompt text.
 *    - DeterministicRoutingPolicy ingests taskCategory to guide coarse routing.
 */

const assert = require('assert');
const {
  TaskCategory,
  TaskClassifier,
  defaultTaskClassifier
} = require('../src/shared/task_classifier');

const { extractQueryFeatures } = require('../src/shared/feature_extractor');
const { detectContextDependency } = require('../src/shared/context_detector');
const { createDetectedQueryEvent, toSafeSummary } = require('../src/shared/query_event');
const { defaultRoutingPolicy, CoarseRoute } = require('../src/shared/routing_policy');

console.log('--- Running Conservative Task Classifier Tests ---');

// 1. Validate the 13 stable categories
console.log('Test 1: Verifying 13 stable task categories...');
const EXPECTED_CATEGORIES = [
  'greeting',
  'arithmetic',
  'factual question',
  'summarization',
  'rewriting',
  'translation',
  'creative writing',
  'coding',
  'debugging',
  'comparison',
  'analysis',
  'reasoning',
  'unknown'
];

const actualCategories = Object.values(TaskCategory);
assert.strictEqual(actualCategories.length, 13, 'Must have exactly 13 task categories');
for (const cat of EXPECTED_CATEGORIES) {
  assert.ok(actualCategories.includes(cat), `Category '${cat}' must be in TaskCategory`);
}
console.log('PASS: Exactly 13 stable categories exist');

// Helper to classify with features
function classify(text) {
  const feats = extractQueryFeatures(text);
  const contextDep = detectContextDependency(text, feats);
  return defaultTaskClassifier.classifyTask(text, feats, contextDep);
}

// 2. Test Greeting
console.log('Test 2: Greeting classification...');
const greetingCases = [
  'Hello Claude!',
  'Good morning',
  'Hi there',
  'Thanks so much',
  'bye'
];
for (const prompt of greetingCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.GREETING, `Expected greeting for: "${prompt}"`);
  assert.ok(res.confidence >= 0.8, 'Expected high confidence for greeting');
}
console.log('PASS: Greeting classification verified');

// 3. Test Arithmetic
console.log('Test 3: Arithmetic classification...');
const arithmeticCases = [
  'calculate 25 * 4 + 100',
  '120 / 4 = ?',
  'what is 42 * 99?',
  'compute 500 - 125'
];
for (const prompt of arithmeticCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.ARITHMETIC, `Expected arithmetic for: "${prompt}"`);
  assert.ok(res.confidence >= 0.8, 'Expected high confidence for arithmetic');
}
console.log('PASS: Arithmetic classification verified');

// 4. Test Debugging
console.log('Test 4: Debugging classification...');
const debuggingCases = [
  'Why is my code throwing TypeError: cannot read property of undefined?',
  'Fix this bug in my react component',
  'Debug this nullpointerexception in my service',
  'Here is the stack trace: Uncaught ReferenceError: foo is not defined'
];
for (const prompt of debuggingCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.DEBUGGING, `Expected debugging for: "${prompt}"`);
}
console.log('PASS: Debugging classification verified');

// 5. Test Coding
console.log('Test 5: Coding classification...');
const codingCases = [
  'Write a python function to compute fibonacci numbers',
  'Implement a binary search tree in TypeScript',
  '```python\ndef parse_log(line):\n    return line.split()\n```',
  'Write a SQL query to find top 5 customers by sales'
];
for (const prompt of codingCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.CODING, `Expected coding for: "${prompt}"`);
}
console.log('PASS: Coding classification verified');

// 6. Test Comparison
console.log('Test 6: Comparison classification...');
const comparisonCases = [
  'Compare PostgreSQL vs MySQL for high write throughput',
  'What are the pros and cons of microservices versus monorepo?',
  'Which database is better between Redis and Memcached?'
];
for (const prompt of comparisonCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.COMPARISON, `Expected comparison for: "${prompt}"`);
}
console.log('PASS: Comparison classification verified');

// 7. Test Summarization
console.log('Test 7: Summarization classification...');
const summarizationCases = [
  'Summarize the following meeting notes and action items',
  'Give me a brief tl;dr of this whitepaper',
  'Executive summary of the Q3 earnings release'
];
for (const prompt of summarizationCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.SUMMARIZATION, `Expected summarization for: "${prompt}"`);
}
console.log('PASS: Summarization classification verified');

// 8. Test Rewriting
console.log('Test 8: Rewriting classification...');
const rewritingCases = [
  'Rewrite this email to make it sound more professional',
  'Paraphrase the following paragraph with better vocabulary',
  'Proofread and edit this abstract for academic clarity'
];
for (const prompt of rewritingCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.REWRITING, `Expected rewriting for: "${prompt}"`);
}
console.log('PASS: Rewriting classification verified');

// 9. Test Translation
console.log('Test 9: Translation classification...');
const translationCases = [
  'Translate this document into Spanish',
  'How do you say where is the train station in German?',
  'Translate the following paragraph to French'
];
for (const prompt of translationCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.TRANSLATION, `Expected translation for: "${prompt}"`);
}
console.log('PASS: Translation classification verified');

// 10. Test Creative Writing
console.log('Test 10: Creative writing classification...');
const creativeWritingCases = [
  'Write a short sci-fi story about a rogue satellite',
  'Compose a poem about autumn leaves',
  'Write a fictional scene between two detectives'
];
for (const prompt of creativeWritingCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.CREATIVE_WRITING, `Expected creative writing for: "${prompt}"`);
}
console.log('PASS: Creative writing classification verified');

// 11. Test Reasoning
console.log('Test 11: Reasoning classification...');
const reasoningCases = [
  'Why do aircraft wings generate lift at subsonic speeds?',
  'Explain the cause and effect of hyperinflation step by step',
  'Derive the formula for kinetic energy from first principles'
];
for (const prompt of reasoningCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.REASONING, `Expected reasoning for: "${prompt}"`);
}
console.log('PASS: Reasoning classification verified');

// 12. Test Analysis
console.log('Test 12: Analysis classification...');
const analysisCases = [
  'Analyze the performance bottlenecks of this cloud infrastructure',
  'Conduct a critical analysis of the company quarterly report',
  'Dissect the market breakdown of EV manufacturers in 2025'
];
for (const prompt of analysisCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.ANALYSIS, `Expected analysis for: "${prompt}"`);
}
console.log('PASS: Analysis classification verified');

// 13. Test Factual Question
console.log('Test 13: Factual question classification...');
const factualCases = [
  'What is the capital of Canada?',
  'Who was the third president of the United States?',
  'When was the telescope invented?',
  'Define photosynthesis'
];
for (const prompt of factualCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.FACTUAL_QUESTION, `Expected factual question for: "${prompt}"`);
}
console.log('PASS: Factual question classification verified');

// 14. Test Unknown / Fallback
console.log('Test 14: Unknown fallback classification...');
const unknownCases = [
  '',
  '   ',
  'banana spaceship purple melody toaster'
];
for (const prompt of unknownCases) {
  const res = classify(prompt);
  assert.strictEqual(res.category, TaskCategory.UNKNOWN, `Expected unknown for: "${prompt}"`);
}
console.log('PASS: Unknown fallback classification verified');

// 15. Guardrail: STRICT NON-MODEL-SELECTION GUARANTEE
console.log('Test 15: Guardrail - Strict non-model-selection guarantee...');
const testPrompts = [
  'Write a complex Python compiler',
  'Hello',
  'What is 2 + 2?',
  'Analyze the architectural tradeoffs of Kafka'
];
for (const prompt of testPrompts) {
  const res = classify(prompt);
  assert.strictEqual(res.model, undefined, 'Task classifier MUST NOT return a model property');
  assert.strictEqual(res.route, undefined, 'Task classifier MUST NOT return a route property');
  assert.strictEqual(res.recommendedModel, undefined, 'Task classifier MUST NOT return a recommendedModel property');
  assert.strictEqual(res.suggestedModel, undefined, 'Task classifier MUST NOT return a suggestedModel property');
  assert.ok(typeof res.category === 'string', 'Must provide category');
  assert.ok(typeof res.confidence === 'number', 'Must provide confidence');
  assert.ok(typeof res.reason === 'string', 'Must provide reason');
  assert.ok(Array.isArray(res.matchedSignals), 'Must provide matchedSignals array');
}
console.log('PASS: Task classifier strictly adheres to non-prescriptive boundary');

// 16. Integration with QueryEvent & toSafeSummary
console.log('Test 16: Integration with QueryEvent & toSafeSummary...');
const queryEvent = createDetectedQueryEvent({
  rawPrompt: 'Write a quick python script to parse CSV files',
  triggerType: 'keyboard_enter'
});
assert.ok(queryEvent.taskClassification, 'queryEvent must attach taskClassification');
assert.strictEqual(queryEvent.taskClassification.category, TaskCategory.CODING);

const safeSummary = toSafeSummary(queryEvent);
assert.strictEqual(safeSummary.taskCategory, 'coding', 'toSafeSummary must include taskCategory');
assert.strictEqual(safeSummary.rawPrompt, undefined, 'toSafeSummary must NEVER leak rawPrompt');
assert.strictEqual(safeSummary.normalizedPrompt, undefined, 'toSafeSummary must NEVER leak normalizedPrompt');
console.log('PASS: QueryEvent attaches taskClassification and exposes safe summary');

// 17. Routing Policy Integration: Task signals guide coarse routes
console.log('Test 17: DeterministicRoutingPolicy integration with task categories...');
// Coding task -> complex-model candidate
const codingEvent = createDetectedQueryEvent({
  rawPrompt: 'Write a python parser for JSON',
  triggerType: 'keyboard_enter'
});
const codingRoute = defaultRoutingPolicy.classify(codingEvent);
assert.strictEqual(codingRoute.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(codingRoute.taskCategory, 'coding');

// Analysis task -> complex-model candidate
const analysisEvent = createDetectedQueryEvent({
  rawPrompt: 'Analyze the system memory bottlenecks under heavy load',
  triggerType: 'keyboard_enter'
});
const analysisRoute = defaultRoutingPolicy.classify(analysisEvent);
assert.strictEqual(analysisRoute.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE);
assert.strictEqual(analysisRoute.taskCategory, 'analysis');

// Factual question -> simple-model candidate
const factualEvent = createDetectedQueryEvent({
  rawPrompt: 'What is the capital of Australia?',
  triggerType: 'keyboard_enter'
});
const factualRoute = defaultRoutingPolicy.classify(factualEvent);
assert.strictEqual(factualRoute.route, CoarseRoute.SIMPLE_MODEL_CANDIDATE);
assert.strictEqual(factualRoute.taskCategory, 'factual question');

console.log('PASS: Routing policy successfully consumes task classifier signals');

console.log('--- ALL CONSERVATIVE TASK CLASSIFIER TESTS PASSED ---');
