/**
 * Automated Unit Test Suite for Conservative Context-Dependency Detector
 * 
 * Verifies local, deterministic detection of context-dependent queries vs
 * standalone questions without LLM inference or history transmission:
 * - Direct continuation commands and elliptical follow-ups
 * - Explicit backward references to prior conversation turns
 * - Anaphoric action verbs targeting pronouns
 * - Strict suppression of false positives on standalone questions:
 *   - Relative pronoun "that" ("a function that sorts an array")
 *   - Expletive / dummy "it" ("is it possible to...", "it is important to...")
 *   - Temporal "this" ("this weekend", "this week")
 *   - Self-contained referents embedded in prompt ("Explain this code: ```...```")
 * - Output restriction: decision-only payload with zero history transmission
 */

const assert = require('assert');
const {
  detectContextDependency,
  isReferentInlineResolved,
  isExpletiveIt
} = require('../src/shared/context_detector');

console.log('--- Running Context-Dependency Detector Tests ---');

// Test 1: Direct continuation commands
console.log('Test 1: Direct continuation commands detection...');
const continuationPrompts = [
  'continue',
  'continue.',
  'go on',
  'tell me more',
  'keep going',
  'proceed',
  'next step',
  'what next?'
];

for (const p of continuationPrompts) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    true,
    `Expected "${p}" to require context analysis`
  );
  assert.strictEqual(res.category, 'CONTINUATION');
  assert.strictEqual(res.confidence, 'HIGH');
}
console.log('PASS: Direct continuation commands recognized');

// Test 2: Elliptical follow-ups and bare interrogatives
console.log('Test 2: Elliptical follow-ups and bare interrogatives...');
const ellipticalPrompts = [
  'what about python?',
  'and for mac?',
  'how about docker?',
  'why?',
  'how so?'
];

for (const p of ellipticalPrompts) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    true,
    `Expected elliptical "${p}" to require context analysis`
  );
  assert.strictEqual(res.category, 'CONTINUATION');
}
console.log('PASS: Elliptical follow-ups and bare interrogatives recognized');

// Test 3: Explicit backward references to preceding turns
console.log('Test 3: Explicit conversation history references...');
const historyReferences = [
  { prompt: 'Explain the difference between the above two approaches.', expectedSignal: 'THE_ABOVE' },
  { prompt: 'In your previous response you mentioned caching.', expectedSignal: 'PREVIOUS_OUTPUT' },
  { prompt: 'You said that PostgreSQL was better for this use case.', expectedSignal: 'SPEAKER_REFERENCE' },
  { prompt: 'Can we do the same thing with Python?', expectedSignal: 'SAME_THING' },
  { prompt: 'Instead of that, can we use an in-memory database?', expectedSignal: 'INSTEAD_OF_THAT' },
  { prompt: 'What did you mean by that?', expectedSignal: 'WHAT_DID_YOU_MEAN' }
];

for (const { prompt, expectedSignal } of historyReferences) {
  const res = detectContextDependency(prompt);
  assert.strictEqual(
    res.requiresContextAnalysis,
    true,
    `Expected "${prompt}" to require context analysis`
  );
  assert.strictEqual(res.category, 'ANAPHORIC_REFERENCE');
  assert(
    res.matchedSignals.includes(expectedSignal),
    `Expected signal "${expectedSignal}" in ${JSON.stringify(res.matchedSignals)}`
  );
}
console.log('PASS: Explicit backward references reliably detected');

// Test 4: Anaphoric action verbs targeting pronouns
console.log('Test 4: Anaphoric action verbs targeting pronouns...');
const anaphoricPrompts = [
  'Fix it so it handles edge cases',
  'Rewrite that in Go',
  'Why did it fail?',
  'Make it faster',
  'Explain that'
];

for (const p of anaphoricPrompts) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    true,
    `Expected anaphoric query "${p}" to require context analysis`
  );
  assert.strictEqual(res.category, 'ANAPHORIC_REFERENCE');
}
console.log('PASS: Anaphoric verb-pronoun patterns reliably detected');

// Test 5: Conservative avoidance of false positives on standalone queries
console.log('Test 5: Conservative avoidance of false positives on standalone queries...');

// 5a. Relative pronoun "that" (e.g. "a function that sorts an array")
const standaloneWithThat = [
  'Write a function that sorts an array in place.',
  'Is there a library that supports WebSockets in C++?',
  'Show me an example that demonstrates recursion.'
];

for (const p of standaloneWithThat) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    false,
    `Standalone query with relative pronoun "${p}" must NOT trigger context analysis`
  );
  assert.strictEqual(res.category, 'STANDALONE');
}

// 5b. Expletive / Dummy "it" (e.g. "is it possible to...", "it is recommended to...")
const standaloneWithIt = [
  'Is it possible to run Docker inside an LXC container?',
  'It is recommended to use HTTPS for all production traffic.',
  'Can it be beneficial to use Redis for session storage?'
];

for (const p of standaloneWithIt) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    false,
    `Standalone query with expletive "it" "${p}" must NOT trigger context analysis`
  );
  assert.strictEqual(res.category, 'STANDALONE');
}

// 5c. Temporal "this" (e.g. "this week", "this morning")
const standaloneWithThis = [
  'What events happened in tech this week?',
  'Summarize the weather in Seattle this weekend.',
  'What is this concept called dependency inversion?'
];

for (const p of standaloneWithThis) {
  const res = detectContextDependency(p);
  assert.strictEqual(
    res.requiresContextAnalysis,
    false,
    `Standalone query with temporal/introductory "this" "${p}" must NOT trigger context analysis`
  );
  assert.strictEqual(res.category, 'STANDALONE');
}
console.log('PASS: Standalone queries with incidental pronouns safely passed through without false triggers');

// Test 6: Self-contained inline referents (code blocks and quotes embedded in prompt)
console.log('Test 6: Self-contained inline referents...');
const inlineReferentPrompt = [
  'Explain this code:',
  '```python',
  'def factorial(n):',
  '    return 1 if n <= 1 else n * factorial(n - 1)',
  '```'
].join('\n');

const inlineFeatures = {
  code: { hasCodeFence: true, hasIndentedCode: false }
};

const inlineRes = detectContextDependency(inlineReferentPrompt, inlineFeatures);
assert.strictEqual(
  inlineRes.requiresContextAnalysis,
  false,
  'Query with embedded code fence must be recognized as self-contained inline referent'
);
assert.strictEqual(inlineRes.category, 'INLINE_RESOLVED');

const calcThisPrompt = 'Calculate this: 15 * 4 + 100 / 2';
const calcThisRes = detectContextDependency(calcThisPrompt);
assert.strictEqual(
  calcThisRes.requiresContextAnalysis,
  false,
  'Query with inline specification "this: ..." must not trigger context dependency'
);
assert.strictEqual(calcThisRes.category, 'INLINE_RESOLVED');
console.log('PASS: Self-contained embedded referents correctly resolved inline');

// Test 7: Standard standalone domain queries
console.log('Test 7: Standard standalone domain queries...');
const domainQueries = [
  'What is the difference between TCP and UDP?',
  'How do asynchronous streams work in JavaScript?',
  '2 + 2',
  'Hello Claude'
];

for (const q of domainQueries) {
  const res = detectContextDependency(q);
  assert.strictEqual(
    res.requiresContextAnalysis,
    false,
    `Expected standard query "${q}" to be standalone`
  );
  assert.strictEqual(res.category, 'STANDALONE');
}
console.log('PASS: Standard standalone queries confirmed independent of context');

// Test 8: Output restriction (decision-only payload, zero history transmission)
console.log('Test 8: Output restriction verification...');
const sampleDecision = detectContextDependency('Fix it please');
assert(typeof sampleDecision.requiresContextAnalysis === 'boolean');
assert(typeof sampleDecision.confidence === 'string');
assert(typeof sampleDecision.category === 'string');
assert(typeof sampleDecision.reason === 'string');
assert(Array.isArray(sampleDecision.matchedSignals));
// Strictly assert zero conversation history or message text is present
assert.strictEqual(sampleDecision.history, undefined);
assert.strictEqual(sampleDecision.context, undefined);
assert.strictEqual(sampleDecision.messages, undefined);
assert.strictEqual(sampleDecision.summary, undefined);
console.log('PASS: Detector payload is strictly decision-only with zero conversation history');

console.log('--- ALL CONTEXT-DEPENDENCY DETECTOR TESTS PASSED ---');
