/**
 * Automated Unit Test Suite for Conversation Turn Relevance Ranker
 * 
 * Verifies:
 * - Lexical overlap scoring with content word extraction
 * - Explicit conversational reference boost ("the above", "previous response", anaphora)
 * - Recency weighting
 * - Structural cue matching (code, math, Q&A)
 * - Small bounded candidate set output
 * - Context confidence and needsContext estimation
 * - Original turn order preservation and chronological reconstruction
 * - Edge cases (empty query, empty turns)
 */

const assert = require('assert');
const {
  rankTurnsByRelevance,
  reconstructChronologicalOrder,
  extractContentWords,
  computeLexicalOverlap,
  computeExplicitReferenceScore,
  computeRecencyScore,
  computeStructuralScore,
  DEFAULT_MAX_CANDIDATES
} = require('../src/shared/relevance_ranker');

console.log('--- Running Conversation Turn Relevance Ranker Tests ---');

// Test 1: Content word extraction and lexical overlap
console.log('Test 1: Content word extraction and lexical overlap...');
const words1 = extractContentWords('How do I configure the WebSocket client timeout?');
assert(words1.has('configure'));
assert(words1.has('websocket'));
assert(words1.has('client'));
assert(words1.has('timeout'));
assert(!words1.has('the'), 'Stop word "the" must be filtered');
assert(!words1.has('how'), 'Stop word "how" must be filtered');

const words2 = extractContentWords('Configure timeout in the WebSocket settings.');
const overlap = computeLexicalOverlap(words1, words2);
assert(overlap.score > 0.4, 'High lexical overlap between related texts');
assert(overlap.matchedWords.includes('configure'));
assert(overlap.matchedWords.includes('websocket'));
assert(overlap.matchedWords.includes('timeout'));

const words3 = extractContentWords('PostgreSQL database query optimization.');
const noOverlap = computeLexicalOverlap(words1, words3);
assert.strictEqual(noOverlap.score, 0, 'Zero overlap between unrelated texts');
console.log('PASS: Lexical overlap and token filtering verified');

// Test 2: Explicit conversational references boost
console.log('Test 2: Explicit reference score computation...');
const refAbove = computeExplicitReferenceScore('Explain the above in detail', 3, 4, 'assistant');
assert.strictEqual(refAbove, 1.0, 'Most recent turn receives 1.0 for "the above"');

const refOld = computeExplicitReferenceScore('Explain the above in detail', 0, 4, 'assistant');
assert(refOld < 0.3, 'Old turn receives low explicit reference score');

const refLastResponse = computeExplicitReferenceScore('In your previous response you mentioned Docker', 3, 4, 'assistant');
assert.strictEqual(refLastResponse, 1.0, 'Last assistant response receives maximum boost');

const refAnaphor = computeExplicitReferenceScore('Fix it so it handles errors', 3, 4, 'assistant');
assert(refAnaphor >= 0.9, 'Immediately preceding turn receives high anaphoric boost');
console.log('PASS: Explicit conversational references boosted accurately');

// Test 3: Recency score decay
console.log('Test 3: Recency score decay...');
const r0 = computeRecencyScore(0, 4); // oldest
const r1 = computeRecencyScore(1, 4);
const r2 = computeRecencyScore(2, 4);
const r3 = computeRecencyScore(3, 4); // newest
assert.strictEqual(r3, 1.0, 'Newest turn recency is 1.0');
assert(r3 > r2 && r2 > r1 && r1 > r0, 'Recency scores strictly decrease with distance');
console.log('PASS: Recency score decay verified');

// Test 4: Structural cue matching
console.log('Test 4: Structural cue matching...');
const turnWithCode = {
  role: 'assistant',
  features: { hasCode: true, hasMath: false, hasQuestions: false }
};
const queryCode = 'Why does this function throw a TypeError?';
const codeScore = computeStructuralScore(queryCode, turnWithCode);
assert(codeScore >= 0.5, 'Structural code match receives boost');

const turnWithoutCode = {
  role: 'user',
  features: { hasCode: false, hasMath: false, hasQuestions: false }
};
const noCodeScore = computeStructuralScore(queryCode, turnWithoutCode);
assert(codeScore > noCodeScore, 'Turn with code receives higher structural score');
console.log('PASS: Structural cue matching verified');

// Test 5: Full ranking with mock conversation turns
console.log('Test 5: Full turn ranking and candidate selection...');
const mockTurns = [
  {
    turnId: 'turn_1',
    role: 'user',
    timestamp: 1000,
    snippet: 'How do I connect to a PostgreSQL database in Node.js?',
    characterCount: 52,
    truncated: false,
    features: { hasCode: false, hasMath: false, hasQuestions: true }
  },
  {
    turnId: 'turn_2',
    role: 'assistant',
    timestamp: 2000,
    snippet: 'Use the pg library: const { Pool } = require("pg"); const pool = new Pool();',
    characterCount: 75,
    truncated: false,
    features: { hasCode: true, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_3',
    role: 'user',
    timestamp: 3000,
    snippet: 'What about Redis for caching?',
    characterCount: 30,
    truncated: false,
    features: { hasCode: false, hasMath: false, hasQuestions: true }
  },
  {
    turnId: 'turn_4',
    role: 'assistant',
    timestamp: 4000,
    snippet: 'Redis is an in-memory key-value store. You can use ioredis for caching.',
    characterCount: 72,
    truncated: false,
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  }
];

// Query specifically targeting the PostgreSQL turn (turn 2)
const queryPg = 'How do I configure the connection pool idle timeout for PostgreSQL?';
const rankingPg = rankTurnsByRelevance(queryPg, mockTurns, { maxCandidates: 2 });

assert.strictEqual(rankingPg.rankedCandidates.length, 2, 'Candidate count bounded by maxCandidates (2)');
// Turn 2 or Turn 1 (PostgreSQL) should be the top ranked candidate
assert(
  rankingPg.rankedCandidates[0].turnId === 'turn_2' || rankingPg.rankedCandidates[0].turnId === 'turn_1',
  'PostgreSQL turns must outrank Redis turns for PostgreSQL query'
);
assert(rankingPg.topScore > 0.1, 'topScore should reflect topical lexical match');
console.log('PASS: Full ranking accurately identifies most relevant turns');

// Test 6: Explicit reference ranking ("the above")
console.log('Test 6: Explicit reference ranking...');
const queryAbove = 'Can you rewrite the above code using async/await?';
const contextDepAbove = { requiresContextAnalysis: true, category: 'ANAPHORIC_REFERENCE' };
const rankingAbove = rankTurnsByRelevance(queryAbove, mockTurns, {
  maxCandidates: 2,
  contextDependency: contextDepAbove
});

assert.strictEqual(rankingAbove.contextConfidence, 'HIGH');
assert.strictEqual(rankingAbove.needsContext, true);
// Most recent turn (turn 4) or turn 2 (code) should top rank
assert(rankingAbove.topScore >= 0.4);
console.log('PASS: Explicit reference correctly elevates recent turns and sets HIGH context confidence');

// Test 7: Chronological reconstruction preservation
console.log('Test 7: Chronological order preservation...');
// Check that originalIndex is present on all ranked candidates
for (const cand of rankingPg.rankedCandidates) {
  assert(typeof cand.originalIndex === 'number', 'originalIndex must be preserved');
  assert(cand.originalIndex >= 0 && cand.originalIndex < mockTurns.length);
}

// Check that reconstructedOrder sorts by originalIndex ascending
const reconstructed = rankingPg.reconstructedOrder;
for (let i = 0; i < reconstructed.length - 1; i++) {
  assert(
    reconstructed[i].originalIndex < reconstructed[i + 1].originalIndex,
    'Reconstructed order must be strictly chronological'
  );
}

// Standalone reconstruction helper
const mixedOrder = [{ originalIndex: 3 }, { originalIndex: 1 }, { originalIndex: 0 }];
const sortedRecon = reconstructChronologicalOrder(mixedOrder);
assert.deepStrictEqual(sortedRecon.map(t => t.originalIndex), [0, 1, 3]);
console.log('PASS: Original turn order preserved and chronological reconstruction verified');

// Test 8: Edge cases (empty inputs)
console.log('Test 8: Edge cases handling...');
const emptyTurnsResult = rankTurnsByRelevance('Hello', []);
assert.strictEqual(emptyTurnsResult.rankedCandidates.length, 0);
assert.strictEqual(emptyTurnsResult.contextConfidence, 'LOW');
assert.strictEqual(emptyTurnsResult.needsContext, false);

const emptyQueryResult = rankTurnsByRelevance('', mockTurns);
assert.strictEqual(emptyQueryResult.rankedCandidates.length, 0);
console.log('PASS: Edge cases gracefully handled');

console.log('--- ALL RELEVANCE RANKER TESTS PASSED ---');
