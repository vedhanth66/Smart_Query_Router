/**
 * Automated Unit Test Suite for Semantics-Preserving Query Normalizer
 * 
 * Verifies:
 * - Line ending normalization (CRLF / CR -> LF)
 * - Leading/trailing whitespace trimming
 * - Excessive blank line collapsing (3+ newlines -> 2)
 * - Safe repeated horizontal whitespace collapse in prose
 * - STRICT preservation of code syntax and indentation
 * - STRICT preservation of mathematical content and symbols
 * - STRICT preservation of punctuation, emojis, quotes, and list structures
 * - STRICT preservation of filler/conversational words (zero lossy token reduction)
 * - Dual form retention and comparison metrics
 */

const assert = require('assert');
const {
  normalizeQueryText,
  normalizeQuery,
  comparePrompts,
  verifySemanticsPreserved
} = require('../src/shared/normalizer');

console.log('--- Running Semantics-Preserving Normalizer Tests ---');

// Test 1: Line ending normalization
console.log('Test 1: Normalizing line endings (CRLF / CR -> LF)...');
const crlfText = 'First line\r\nSecond line\rThird line\nFourth line';
const normalizedCrlf = normalizeQueryText(crlfText);
assert.strictEqual(normalizedCrlf, 'First line\nSecond line\nThird line\nFourth line');
assert.strictEqual(normalizedCrlf.includes('\r'), false, 'All CR/CRLF converted to LF');
console.log('PASS: Line endings safely normalized to standard LF');

// Test 2: Boundary whitespace trimming and excessive blank lines
console.log('Test 2: Trimming boundary whitespace and collapsing excessive blank lines...');
const messyWhitespace = '\n\n   \t  Paragraph 1\n\n\n\n\nParagraph 2   \t \n\n';
const cleanWhitespace = normalizeQueryText(messyWhitespace);
assert.strictEqual(cleanWhitespace, 'Paragraph 1\n\nParagraph 2');
console.log('PASS: Boundary whitespace trimmed and excessive blank lines collapsed to paragraph breaks');

// Test 3: Code syntax and indentation strictly unchanged
console.log('Test 3: Proving code syntax and indentation remain unchanged...');
const pythonCodeBlock = [
  '```python',
  'def fibonacci(n):',
  '    # 4-space indentation must be preserved!',
  '    if n <= 1:',
  '        return n',
  '    a, b = 0, 1',
  '    for _ in range(n):',
  '        a, b = b, a + b',
  '    return a',
  '```'
].join('\n');

const promptWithCode = `Here is my Python function:\n\n${pythonCodeBlock}\n\nHow can I optimize it?`;
const normPromptWithCode = normalizeQueryText(promptWithCode);

// The code block within ``` must be character-for-character identical
assert(normPromptWithCode.includes(pythonCodeBlock), 'Python code block preserved verbatim');
assert(normPromptWithCode.includes('    # 4-space indentation must be preserved!'), 'Indentation preserved');
assert(normPromptWithCode.includes('    for _ in range(n):'), 'Loop syntax preserved');
assert(normPromptWithCode.includes('        a, b = b, a + b'), 'Tuple unpacking and math symbols preserved');

// Test indented code (outside backticks, 4 spaces leading)
const indentedCode = '    const result = compute(x, y); // 4 spaces indent';
const normIndented = normalizeQueryText(indentedCode);
assert.strictEqual(normIndented, indentedCode, 'Indented code preserved exactly');

// Test inline code backticks with intentional spacing
const inlineCodePrompt = 'Check if `const   x   =   1;` is valid syntax.';
const normInline = normalizeQueryText(inlineCodePrompt);
assert.strictEqual(normInline, 'Check if `const   x   =   1;` is valid syntax.', 'Inline code spacing preserved');
console.log('PASS: Code syntax, indentation, and code blocks strictly unchanged');

// Test 4: Mathematical content and symbols strictly unchanged
console.log('Test 4: Proving mathematical content and symbols remain unchanged...');
const arithmeticQuery = 'Calculate 2 * (3 + 4) / 5 - 1.5 ^ 2';
const normArithmetic = normalizeQueryText(arithmeticQuery);
assert.strictEqual(normArithmetic, arithmeticQuery, 'Arithmetic symbols preserved');

// Complex LaTeX equation with calculus and summations
const mathQuery = 'Solve $$\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$ and \\sum_{i=1}^n x_i \\le \\mu';
const normMath = normalizeQueryText(mathQuery);
assert.strictEqual(normMath, mathQuery, 'LaTeX calculus, summations, and symbols preserved');

// Verify all mathematical symbols remain
const allMathSymbols = 'a + b - c * d / e = f ^ g % h × i ÷ j < k > l ≤ m ≥ n ∑ o ∫ p π q';
const normMathSymbols = normalizeQueryText(allMathSymbols);
assert.strictEqual(normMathSymbols, allMathSymbols, 'All mathematical operators and symbols preserved');
console.log('PASS: Mathematical formulas, symbols, and operators strictly unchanged');

// Test 5: Punctuation and emojis strictly unchanged
console.log('Test 5: Proving punctuation and emojis remain unchanged...');
const punctuatedEmojiPrompt = 'Hello, Claude! How are you doing today? (Is everything okay...?) 🚀 🐍 💡';
const normPunctuated = normalizeQueryText(punctuatedEmojiPrompt);
assert.strictEqual(normPunctuated, punctuatedEmojiPrompt, 'All punctuation and emojis preserved');

// Punctuation characters completeness
const allPunctuation = 'Quotes: "double" and \'single\'; brackets: (round), [square], {curly}; misc: ! ? . , : ; - _';
const normPunctuation = normalizeQueryText(allPunctuation);
assert.strictEqual(normPunctuation, allPunctuation, 'All punctuation forms preserved');
console.log('PASS: Punctuation and emojis strictly unchanged');

// Test 6: Markdown list structure preserved
console.log('Test 6: Proving list structures remain intact...');
const listPrompt = [
  'Here is the checklist:',
  '1. Item one with detail',
  '2. Item two with detail',
  '   - Nested bullet with 3 spaces indentation',
  '   - Another nested bullet',
  '* Unordered item A',
  '* Unordered item B'
].join('\n');

const normList = normalizeQueryText(listPrompt);
assert(normList.includes('1. Item one with detail'), 'Ordered item preserved');
assert(normList.includes('   - Nested bullet with 3 spaces indentation'), 'Nested list indentation preserved');
assert(normList.includes('* Unordered item A'), 'Unordered item preserved');
console.log('PASS: List structures and hierarchical indentations strictly preserved');

// Test 7: Filler and conversational words NEVER removed (zero lossy compression)
console.log('Test 7: Proving conversational filler words are never removed...');
const politeQuery = 'Please, could you kindly take a moment to explain how async/await works? I would really appreciate it.';
const normPolite = normalizeQueryText(politeQuery);
assert.strictEqual(normPolite, politeQuery, 'No words removed to save tokens');

const wordsInPolite = ['Please', 'could', 'you', 'kindly', 'take', 'a', 'moment', 'to', 'explain', 'I', 'would', 'really'];
for (const word of wordsInPolite) {
  assert(normPolite.includes(word), `Word "${word}" must not be stripped`);
}
console.log('PASS: No filler words removed; token count is not reduced at expense of prompt content');

// Test 8: Quoted text with internal spacing preserved
console.log('Test 8: Proving quoted text content is preserved...');
const quotedPrompt = 'Search for the exact phrase "artificial   intelligence   agents" in the document.';
const normQuoted = normalizeQueryText(quotedPrompt);
assert.strictEqual(normQuoted, quotedPrompt, 'Quoted string internal spaces preserved');
console.log('PASS: Quoted text preserved');

// Test 9: Dual form retention and comparison metrics
console.log('Test 9: Dual form retention and comparison helpers...');
const rawSample = '  \r\n  Explain    gradient   descent  \r\n  ';
const result = normalizeQuery(rawSample);

assert.strictEqual(result.rawPrompt, rawSample, 'Original raw prompt preserved bit-for-bit');
assert.strictEqual(result.normalizedPrompt, 'Explain gradient descent');
assert.strictEqual(result.isChanged, true);
assert.strictEqual(result.savings.characters, rawSample.length - 'Explain gradient descent'.length);
assert(result.savings.percentage > 0);

// Verify comparison helper
const comparison = comparePrompts(result.rawPrompt, result.normalizedPrompt);
assert.strictEqual(comparison.isIdentical, false);
assert.strictEqual(comparison.hasLeadingWhitespace, true);
assert.strictEqual(comparison.hasTrailingWhitespace, true);
assert.strictEqual(comparison.hasCrlf, true);
assert.strictEqual(comparison.charactersSaved, result.savings.characters);

// Verify semantics preservation validator
const semCheck = verifySemanticsPreserved(result.rawPrompt, result.normalizedPrompt);
assert.strictEqual(semCheck.preserved, true, 'Zero semantic violations detected');
assert.strictEqual(semCheck.violations.length, 0);
console.log('PASS: Dual form retention and comparison metrics verified');

console.log('--- ALL NORMALIZER TESTS PASSED ---');
