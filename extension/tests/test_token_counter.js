/**
 * Test Suite: Accurate Token Counter & Dynamic Savings / Consumption
 * 
 * Verifies:
 * 1. Subword token counting handles empty, plain words, numbers, contractions, code, and Unicode.
 * 2. Conversation history token counting includes per-turn message framing overhead.
 * 3. estimateLocalRuleSavings accurately computes prompt + context + response tokens (not hardcoded 50).
 * 4. estimateConsumedTokens accurately computes input + output tokens for native Claude execution.
 * 5. estimateContextBudget accurately estimates headroom against Claude's 200k context limit.
 */

const assert = require('assert');
const tokenCounterModule = require('../src/shared/token_counter');

const {
  countTokens,
  countConversationTokens,
  estimatePromptInputTokens,
  estimateResponseTokens,
  estimateConsumedTokens,
  estimateLocalRuleSavings,
  estimateContextBudget,
  CLAUDE_MAX_CONTEXT_WINDOW_TOKENS
} = tokenCounterModule;

console.log('--- Running Token Counter Test Suite ---');

// 1. Basic token counting
assert.strictEqual(countTokens(''), 0, 'Empty string should return 0 tokens');
assert.strictEqual(countTokens(null), 0, 'Null should return 0 tokens');
assert.strictEqual(countTokens(undefined), 0, 'Undefined should return 0 tokens');
assert.strictEqual(countTokens('a'), 1, 'Single character should return 1 token');

const helloWorldTokens = countTokens('Hello world');
assert(helloWorldTokens >= 2 && helloWorldTokens <= 3, `Expected 2-3 tokens for 'Hello world', got ${helloWorldTokens}`);

const mathTokens = countTokens('5 + 2');
assert(mathTokens >= 3 && mathTokens <= 4, `Expected 3-4 tokens for '5 + 2', got ${mathTokens}`);

const calcTokens = countTokens('calculate compound interest on $10,000 at 5% for 3 years');
assert(calcTokens >= 10 && calcTokens <= 20, `Expected 10-20 tokens, got ${calcTokens}`);

// 2. Contractions and numbers
const contractionTokens = countTokens("You're right, it's done");
assert(contractionTokens >= 6 && contractionTokens <= 9, `Expected 6-9 tokens for contractions, got ${contractionTokens}`);

const largeNumberTokens = countTokens('1000000');
assert(largeNumberTokens >= 2, `Expected >= 2 tokens for large number, got ${largeNumberTokens}`);

// 3. Emojis and Unicode
const emojiTokens = countTokens('Hello 👋 world 🌍');
assert(emojiTokens >= 4, `Expected >= 4 tokens for text with emojis, got ${emojiTokens}`);

// 4. Code and newlines
const codeTokens = countTokens('```python\ndef add(a, b):\n    return a + b\n```');
assert(codeTokens >= 10, `Expected >= 10 tokens for code block, got ${codeTokens}`);

// 5. Conversation history token counting
const mockTurns = [
  { role: 'user', content: 'What is 10 + 20?', characterCount: 16 },
  { role: 'assistant', content: '10 + 20 equals 30.', characterCount: 18 }
];

const contextTokens = countConversationTokens(mockTurns);
assert(contextTokens >= 12, `Expected >= 12 tokens including framing for 2 turns, got ${contextTokens}`);
assert.strictEqual(countConversationTokens([]), 0, 'Empty turns should return 0 tokens');

// 6. Dynamic Local Rule Savings (VERIFIES NOT HARDCODED TO 50!)
const mockTracker = {
  getRecentTurns: () => mockTurns
};

const emptyTracker = {
  getRecentTurns: () => []
};

// Savings without prior history
const savingsNoHistory = estimateLocalRuleSavings('5+2', '7', emptyTracker);
assert(savingsNoHistory > 0, 'Savings must be positive');
assert(savingsNoHistory !== 50, `Savings for '5+2' should be accurate, not hardcoded 50! Got ${savingsNoHistory}`);
assert(savingsNoHistory >= 8 && savingsNoHistory <= 18, `Expected realistic 8-18 tokens saved for '5+2' -> '7', got ${savingsNoHistory}`);

// Savings WITH prior history: should include the history that was saved from re-transmission!
const savingsWithHistory = estimateLocalRuleSavings('5+2', '7', mockTracker);
assert(savingsWithHistory > savingsNoHistory, 'Savings with history should be strictly greater than without history');
assert.strictEqual(
  savingsWithHistory,
  savingsNoHistory + contextTokens,
  'Savings with history should equal base savings + context tokens'
);

// Complex query savings
const complexSavings = estimateLocalRuleSavings(
  'calculate compound interest on $10,000 at 5% for 3 years',
  'Total after 3 years: $11,576.25 (Interest: $1,576.25)',
  emptyTracker
);
assert(complexSavings > savingsNoHistory, 'Complex math savings should be greater than simple math savings');
assert(complexSavings !== 50, `Complex savings should not be 50! Got ${complexSavings}`);

// 7. Consumed Tokens Estimation for Native Requests
const consumed = estimateConsumedTokens(
  'Explain how quantum computing works in simple terms',
  'Quantum computing utilizes qubits which can exist in superpositions of 0 and 1 simultaneously.',
  emptyTracker
);

assert(consumed.inputTokens > 0, 'Input tokens must be > 0');
assert(consumed.outputTokens > 0, 'Output tokens must be > 0');
assert.strictEqual(
  consumed.totalTokens,
  consumed.inputTokens + consumed.outputTokens,
  'Total consumed must equal input + output tokens'
);

const consumedWithHistory = estimateConsumedTokens(
  'Can you clarify that further?',
  'Certainly! In classical computing, bits are binary...',
  mockTracker
);
assert(consumedWithHistory.inputTokens > consumed.inputTokens, 'Input tokens with history should be greater');

// 8. Context Budget (200k context window)
const budget = estimateContextBudget(mockTracker);
assert.strictEqual(budget.maxTokens, CLAUDE_MAX_CONTEXT_WINDOW_TOKENS, 'Max tokens should match 200,000');
assert.strictEqual(budget.usedTokens, contextTokens, 'Used tokens should match context tokens');
assert.strictEqual(budget.remainingTokens, CLAUDE_MAX_CONTEXT_WINDOW_TOKENS - contextTokens, 'Remaining tokens should match');
assert(budget.pctUsed.endsWith('%'), 'Percentage used should be formatted as % string');

console.log('PASS: All Token Counter and Savings / Consumption tests passed successfully.');
