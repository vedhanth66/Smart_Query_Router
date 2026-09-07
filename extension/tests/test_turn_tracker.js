/**
 * Automated Unit Test Suite for Bounded Local Turn Tracker
 * 
 * Verifies:
 * - Small, configurable turn window and FIFO eviction
 * - Strict snippet length bounding and truncation flag
 * - Minimal information storage (zero sensitive tokens, cookies, or unbounded history)
 * - Immediate cleanup on conversation switch
 * - Immediate cleanup on navigation / reset
 * - Compact relevance context generation
 * - Deduplication of rapid duplicate observations
 */

const assert = require('assert');
const {
  RecentTurnsTracker,
  createBoundedSnippet,
  DEFAULT_MAX_TURNS,
  DEFAULT_MAX_SNIPPET_CHARS
} = require('../src/shared/turn_tracker');

console.log('--- Running Bounded Local Turn Tracker Tests ---');

// Test 1: Bounded window and FIFO eviction
console.log('Test 1: Bounded window and FIFO eviction...');
const tracker = new RecentTurnsTracker({ maxTurns: 3, maxSnippetChars: 200 });
assert.strictEqual(tracker.maxTurns, 3);
assert.strictEqual(tracker.getTurnCount(), 0);

const turn1 = tracker.recordTurn({ role: 'user', text: 'First user question', timestamp: 1000 });
const turn2 = tracker.recordTurn({ role: 'assistant', text: 'First assistant response', timestamp: 2000 });
const turn3 = tracker.recordTurn({ role: 'user', text: 'Second user question', timestamp: 3000 });
assert.strictEqual(tracker.getTurnCount(), 3);

// Record 4th turn: must evict turn1 (FIFO)
const turn4 = tracker.recordTurn({ role: 'assistant', text: 'Second assistant response', timestamp: 4000 });
assert.strictEqual(tracker.getTurnCount(), 3, 'Window size must not exceed maxTurns (3)');

const recentTurns = tracker.getRecentTurns();
assert.strictEqual(recentTurns[0].snippet, 'First assistant response');
assert.strictEqual(recentTurns[1].snippet, 'Second user question');
assert.strictEqual(recentTurns[2].snippet, 'Second assistant response');
assert.strictEqual(tracker.getLastTurn().snippet, 'Second assistant response');
console.log('PASS: Bounded turn window and FIFO eviction verified');

// Test 2: Snippet bounding and truncation
console.log('Test 2: Snippet bounding and truncation...');
const longTracker = new RecentTurnsTracker({ maxTurns: 4, maxSnippetChars: 50 });
const longText = 'This is a very long prompt text designed to exceed the fifty character limit for testing truncation behavior.';
const longTurn = longTracker.recordTurn({ role: 'user', text: longText });

assert(longTurn.snippet.length <= 50, 'Snippet length must be <= 50 chars');
assert.strictEqual(longTurn.truncated, true, 'Turn must be marked as truncated');
assert.strictEqual(longTurn.characterCount, longText.length, 'Original character count recorded');

const shortText = 'Short prompt';
const shortTurn = longTracker.recordTurn({ role: 'assistant', text: shortText });
assert.strictEqual(shortTurn.snippet, shortText);
assert.strictEqual(shortTurn.truncated, false, 'Short turn must not be truncated');
console.log('PASS: Snippet length bounding and truncation flag verified');

// Test 3: Minimal information storage (Zero sensitive data)
console.log('Test 3: Minimal information storage...');
const turn = tracker.getLastTurn();
assert(turn.turnId.startsWith('turn_'));
assert.strictEqual(typeof turn.role, 'string');
assert.strictEqual(typeof turn.timestamp, 'number');
assert.strictEqual(typeof turn.snippet, 'string');
assert.strictEqual(typeof turn.characterCount, 'number');
assert.strictEqual(typeof turn.truncated, 'boolean');
assert(typeof turn.features === 'object');

// Strictly assert no sensitive keys or unbounded data
assert.strictEqual(turn.cookie, undefined);
assert.strictEqual(turn.token, undefined);
assert.strictEqual(turn.session, undefined);
assert.strictEqual(turn.fullHistory, undefined);
assert.strictEqual(turn.domElement, undefined);
console.log('PASS: Only minimum information stored for relevance analysis');

// Test 4: Cleanup on conversation switch
console.log('Test 4: Cleanup on conversation switch...');
const convTracker = new RecentTurnsTracker({ maxTurns: 4 });
convTracker.recordTurn({ role: 'user', text: 'Question in Conv A', conversationId: 'conv_aaa_111' });
convTracker.recordTurn({ role: 'assistant', text: 'Answer in Conv A', conversationId: 'conv_aaa_111' });
assert.strictEqual(convTracker.getTurnCount(), 2);
assert.strictEqual(convTracker.currentConversationId, 'conv_aaa_111');

// User switches to Conv B
convTracker.recordTurn({ role: 'user', text: 'New question in Conv B', conversationId: 'conv_bbb_222' });
assert.strictEqual(convTracker.currentConversationId, 'conv_bbb_222');
assert.strictEqual(convTracker.getTurnCount(), 1, 'Previous conversation turns must be cleared on switch');
assert.strictEqual(convTracker.getRecentTurns()[0].snippet, 'New question in Conv B');

// Explicit switchConversation method
convTracker.switchConversation('conv_ccc_333');
assert.strictEqual(convTracker.getTurnCount(), 0, 'Explicit switch must clear turns');
assert.strictEqual(convTracker.currentConversationId, 'conv_ccc_333');
console.log('PASS: Conversation switch resets and clears turn buffer');

// Test 5: Cleanup on navigation / unmount
console.log('Test 5: Cleanup on navigation / clear...');
const navTracker = new RecentTurnsTracker();
navTracker.recordTurn({ role: 'user', text: 'Hello' });
navTracker.recordTurn({ role: 'assistant', text: 'Hi' });
assert.strictEqual(navTracker.getTurnCount(), 2);

navTracker.clear();
assert.strictEqual(navTracker.getTurnCount(), 0);
assert.strictEqual(navTracker.getRecentTurns().length, 0);
assert.strictEqual(navTracker.getLastTurn(), null);
console.log('PASS: Clear immediately discards in-memory turn buffer');

// Test 6: Compact relevance context formatting
console.log('Test 6: Relevance context formatting...');
const relTracker = new RecentTurnsTracker({ maxTurns: 4, conversationId: 'chat_uuid_123' });
relTracker.recordTurn({ role: 'user', text: 'What is WebSockets?' });
relTracker.recordTurn({ role: 'assistant', text: 'WebSockets provide full-duplex communication.' });

const relContext = relTracker.getRelevanceContext();
assert.strictEqual(relContext.turnCount, 2);
assert.strictEqual(relContext.conversationId, 'chat_uuid_123');
assert.strictEqual(relContext.hasAssistantHistory, true);
assert.strictEqual(relContext.turns.length, 2);
assert.strictEqual(relContext.turns[0].role, 'user');
assert.strictEqual(relContext.turns[1].role, 'assistant');

const lastAssistant = relTracker.getLastAssistantTurn();
assert.strictEqual(lastAssistant.role, 'assistant');
assert(lastAssistant.snippet.includes('WebSockets'));
console.log('PASS: Relevance context cleanly formatted');

// Test 7: Rapid duplicate observation suppression
console.log('Test 7: Rapid duplicate observation suppression...');
const dedupTracker = new RecentTurnsTracker({ maxTurns: 4 });
const now = Date.now();
const t1 = dedupTracker.recordTurn({ role: 'user', text: 'Duplicate check prompt', timestamp: now });
assert(t1 !== null);
const t2 = dedupTracker.recordTurn({ role: 'user', text: 'Duplicate check prompt', timestamp: now + 500 });
assert.strictEqual(t2, null, 'Rapid identical observation within 1500ms must be dropped');
assert.strictEqual(dedupTracker.getTurnCount(), 1);
console.log('PASS: Rapid duplicate turn observations safely suppressed');

console.log('--- ALL TURN TRACKER TESTS PASSED ---');
