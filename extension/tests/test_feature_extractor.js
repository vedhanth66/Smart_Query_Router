/**
 * Automated Unit Test Suite for Non-Generative Local Feature Extractor
 * 
 * Verifies deterministic, inexpensive feature extraction without LLM inference:
 * - Approximate length & token estimates
 * - Code-like formatting (fences, inline backticks, indentation, syntax keywords)
 * - Lists (ordered, unordered, mixed)
 * - Number of questions
 * - Mathematical symbols & LaTeX notation
 * - URLs
 * - Reasoning & comparison cues
 * - Non-prescriptive signal representation (signals only, no routing side-effects)
 */

const assert = require('assert');
const {
  extractQueryFeatures,
  estimateTokens
} = require('../src/shared/feature_extractor');

console.log('--- Running Non-Generative Local Feature Extractor Tests ---');

// Test 1: Length and token estimation
console.log('Test 1: Length and token estimation features...');
const emptyFeatures = extractQueryFeatures('');
assert.strictEqual(emptyFeatures.length.characterCount, 0);
assert.strictEqual(emptyFeatures.length.wordCount, 0);
assert.strictEqual(emptyFeatures.length.lineCount, 0);
assert.strictEqual(emptyFeatures.length.estimatedTokens, 0);

const proseSample = 'This is a test prompt containing eight words.';
const proseFeatures = extractQueryFeatures(proseSample);
assert.strictEqual(proseFeatures.length.characterCount, proseSample.length);
assert.strictEqual(proseFeatures.length.wordCount, 8);
assert.strictEqual(proseFeatures.length.lineCount, 1);
assert.strictEqual(proseFeatures.length.estimatedTokens, Math.ceil(proseSample.length / 4));
console.log('PASS: Length and token estimation features accurately computed');

// Test 2: Code formatting detection
console.log('Test 2: Code-like formatting feature extraction...');
const fencedPrompt = 'Can you review this code?\n```python\ndef add(a, b):\n    return a + b\n```';
const fencedFeatures = extractQueryFeatures(fencedPrompt);
assert.strictEqual(fencedFeatures.code.hasCodeFence, true);
assert.strictEqual(fencedFeatures.code.hasCodeSyntax, true);

const inlinePrompt = 'What does `git status --porcelain` do?';
const inlineFeatures = extractQueryFeatures(inlinePrompt);
assert.strictEqual(inlineFeatures.code.hasInlineCode, true);
assert.strictEqual(inlineFeatures.code.hasCodeSyntax, true);

const indentedPrompt = 'Here is the function:\n    const sum = (a, b) => a + b;';
const indentedFeatures = extractQueryFeatures(indentedPrompt);
assert.strictEqual(indentedFeatures.code.hasIndentedCode, true);
assert.strictEqual(indentedFeatures.code.hasCodeSyntax, true);

const ordinaryProse = 'What is the capital of France?';
const plainFeatures = extractQueryFeatures(ordinaryProse);
assert.strictEqual(plainFeatures.code.hasCodeFence, false);
assert.strictEqual(plainFeatures.code.hasInlineCode, false);
assert.strictEqual(plainFeatures.code.hasIndentedCode, false);
assert.strictEqual(plainFeatures.code.hasCodeSyntax, false);
console.log('PASS: Code-like formatting detected reliably across fences, backticks, and indentation');

// Test 3: List structure detection
console.log('Test 3: List feature extraction (ordered, unordered, mixed)...');
const unorderedPrompt = 'Here are my requirements:\n- Fast execution\n- Low memory\n- Minimal permissions';
const unordFeatures = extractQueryFeatures(unorderedPrompt);
assert.strictEqual(unordFeatures.lists.hasList, true);
assert.strictEqual(unordFeatures.lists.listType, 'UNORDERED');
assert.strictEqual(unordFeatures.lists.unorderedCount, 3);
assert.strictEqual(unordFeatures.lists.orderedCount, 0);

const orderedPrompt = 'Steps to reproduce:\n1. Open browser\n2. Click button\n3. Observe crash';
const ordFeatures = extractQueryFeatures(orderedPrompt);
assert.strictEqual(ordFeatures.lists.hasList, true);
assert.strictEqual(ordFeatures.lists.listType, 'ORDERED');
assert.strictEqual(ordFeatures.lists.orderedCount, 3);
assert.strictEqual(ordFeatures.lists.unorderedCount, 0);

const mixedPrompt = 'Summary:\n1. First item\n- Bullet item';
const mixedFeatures = extractQueryFeatures(mixedPrompt);
assert.strictEqual(mixedFeatures.lists.hasList, true);
assert.strictEqual(mixedFeatures.lists.listType, 'MIXED');
assert.strictEqual(mixedFeatures.lists.totalCount, 2);
console.log('PASS: List structures and item counts correctly identified');

// Test 4: Question count detection
console.log('Test 4: Question detection and counting...');
const singleQ = 'What is the difference between synchronous and asynchronous code?';
const singleQFeatures = extractQueryFeatures(singleQ);
assert.strictEqual(singleQFeatures.questions.questionCount, 1);
assert.strictEqual(singleQFeatures.questions.hasMultipleQuestions, false);

const multiQ = 'How does this work? Why is it faster? Can you show an example?';
const multiQFeatures = extractQueryFeatures(multiQ);
assert.strictEqual(multiQFeatures.questions.questionCount, 3);
assert.strictEqual(multiQFeatures.questions.hasMultipleQuestions, true);

const noQ = 'Tell me a story about space exploration.';
const noQFeatures = extractQueryFeatures(noQ);
assert.strictEqual(noQFeatures.questions.questionCount, 0);
assert.strictEqual(noQFeatures.questions.hasMultipleQuestions, false);
console.log('PASS: Question counts and multi-question flags verified');

// Test 5: Mathematical symbols & LaTeX detection
console.log('Test 5: Mathematical symbol and LaTeX feature extraction...');
const arithmeticText = 'Calculate 10 * (2 + 3) / 5 ^ 2';
const arithFeatures = extractQueryFeatures(arithmeticText);
assert.strictEqual(arithFeatures.math.hasMathSymbols, true);
assert(arithFeatures.math.symbolCount >= 4);

const latexText = 'Evaluate $$\\int_0^\\infty e^{-x} dx$$ and \\sum_{i=1}^n x_i';
const latexFeatures = extractQueryFeatures(latexText);
assert.strictEqual(latexFeatures.math.hasMathSymbols, true);
assert.strictEqual(latexFeatures.math.hasLatexMath, true);

const nonMathText = 'Tell me about the history of Rome';
const nonMathFeatures = extractQueryFeatures(nonMathText);
assert.strictEqual(nonMathFeatures.math.hasMathSymbols, false);
assert.strictEqual(nonMathFeatures.math.hasLatexMath, false);
assert.strictEqual(nonMathFeatures.math.symbolCount, 0);
console.log('PASS: Mathematical symbols and LaTeX notations reliably extracted');

// Test 6: URL detection
console.log('Test 6: URL feature extraction...');
const urlText = 'Summarize the documentation at https://nodejs.org/api/fs.html and https://developer.mozilla.org';
const urlFeatures = extractQueryFeatures(urlText);
assert.strictEqual(urlFeatures.urls.hasUrl, true);
assert.strictEqual(urlFeatures.urls.urlCount, 2);

const noUrlText = 'Show me how to make an HTTP GET request in Node';
const noUrlFeatures = extractQueryFeatures(noUrlText);
assert.strictEqual(noUrlFeatures.urls.hasUrl, false);
assert.strictEqual(noUrlFeatures.urls.urlCount, 0);
console.log('PASS: URL presence and link counts verified');

// Test 7: Reasoning & comparison cues
console.log('Test 7: Reasoning and comparison cue detection...');
const comparisonText = 'Compare React vs Vue: what is the difference between them and what are the tradeoffs?';
const compFeatures = extractQueryFeatures(comparisonText);
assert.strictEqual(compFeatures.cues.hasComparisonCue, true);
assert(compFeatures.cues.detectedCues.includes('COMPARE'));
assert(compFeatures.cues.detectedCues.includes('VERSUS'));
assert(compFeatures.cues.detectedCues.includes('DIFFERENCE'));
assert(compFeatures.cues.detectedCues.includes('TRADEOFFS'));

const reasoningText = 'Explain why garbage collection causes pause times and derive the step-by-step root cause';
const reasonFeatures = extractQueryFeatures(reasoningText);
assert.strictEqual(reasonFeatures.cues.hasReasoningCue, true);
assert(reasonFeatures.cues.detectedCues.includes('WHY'));
assert(reasonFeatures.cues.detectedCues.includes('EXPLAIN_WHY'));
assert(reasonFeatures.cues.detectedCues.includes('STEP_BY_STEP'));
assert(reasonFeatures.cues.detectedCues.includes('ROOT_CAUSE'));

const neutralText = 'Hi Claude, what time is it?';
const neutralFeatures = extractQueryFeatures(neutralText);
assert.strictEqual(neutralFeatures.cues.hasComparisonCue, false);
assert.strictEqual(neutralFeatures.cues.hasReasoningCue, false);
assert.strictEqual(neutralFeatures.cues.detectedCues.length, 0);
console.log('PASS: Reasoning and comparison cues detected accurately');

// Test 8: Non-prescriptive signal representation
console.log('Test 8: Proving features are non-prescriptive heuristic signals...');
const multiSignalText = 'Compare the two approaches in https://example.com/spec? Which is faster: 2 * x or x + x?';
const multiSignal = extractQueryFeatures(multiSignalText);
assert(typeof multiSignal === 'object', 'Features object produced');
assert.strictEqual(multiSignal.urls.hasUrl, true);
assert.strictEqual(multiSignal.cues.hasComparisonCue, true);
assert.strictEqual(multiSignal.math.hasMathSymbols, true);
assert.strictEqual(multiSignal.questions.questionCount, 2);
// Ensure features contains NO decision outcomes, models, or routing actions
assert.strictEqual(multiSignal.decision, undefined, 'Features must not contain decision');
assert.strictEqual(multiSignal.model, undefined, 'Features must not contain model');
assert.strictEqual(multiSignal.route, undefined, 'Features must not contain route');
console.log('PASS: Features operate purely as signals without enforcing routing decisions');

console.log('--- ALL FEATURE EXTRACTOR TESTS PASSED ---');
