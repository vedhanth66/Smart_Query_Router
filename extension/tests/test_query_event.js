/**
 * Automated Unit Test Suite for Detected Query Event Data Structure
 * Tests structural separation of content and metadata, safe context extraction,
 * privacy classification, optimization placeholders, and safe logging summaries.
 */

const assert = require('assert');
const {
  createDetectedQueryEvent,
  classifyPrivacy,
  extractSafeContext,
  toSafeSummary,
  validateQueryEvent
} = require('../src/shared/query_event');

console.log('--- Running Query Event Data Structure Tests ---');

// Test 1: Structural separation of content and metadata
console.log('Test 1: Structural separation of content and metadata...');
const prompt = 'How do asynchronous streams work in JavaScript?';
const event = createDetectedQueryEvent({
  rawPrompt: prompt,
  triggerType: 'keyboard_enter',
  context: {
    origin: 'https://claude.ai',
    pathname: '/chat/abc-123-uuid',
    conversationId: 'abc-123-uuid',
    isNewChat: false,
    activeModelHint: 'Sonnet 3.5'
  }
});

// Verify 7 distinct top-level partitions exist
assert(event.metadata !== undefined, 'Metadata partition exists');
assert(event.context !== undefined, 'Context partition exists');
assert(event.content !== undefined, 'Content partition exists');
assert(event.privacy !== undefined, 'Privacy partition exists');
assert(event.features !== undefined, 'Features partition exists');
assert(event.contextDependency !== undefined, 'ContextDependency partition exists');
assert(event.optimization !== undefined, 'Optimization partition exists');

// Verify features partition extracted signals
assert.strictEqual(event.features.length.characterCount, prompt.length);
assert.strictEqual(event.features.questions.questionCount, 1);
assert.strictEqual(event.features.cues.hasReasoningCue, true);
assert(event.features.cues.detectedCues.includes('HOW_DOES_IT_WORK'));

// Verify contextDependency partition decision
assert.strictEqual(event.contextDependency.requiresContextAnalysis, false);
assert.strictEqual(event.contextDependency.category, 'STANDALONE');

// Verify metadata
assert(event.metadata.eventId.startsWith('evt_'), 'EventId prefixed with evt_');
assert.strictEqual(typeof event.metadata.timestamp, 'number');
assert.strictEqual(event.metadata.triggerType, 'keyboard_enter');
assert.strictEqual(event.metadata.schemaVersion, '1.0');

// Verify content partition isolates raw text and normalized form
assert.strictEqual(event.content.rawPrompt, prompt);
assert.strictEqual(event.content.normalizedPrompt, prompt);
assert.strictEqual(event.content.isNormalized, false);
assert.strictEqual(event.content.characterCount, prompt.length);
assert.strictEqual(event.content.normalizedCharacterCount, prompt.length);
assert.strictEqual(event.content.wordCount, 7);

// Test dual preservation with unnormalized input
const unnormalizedRaw = "  \r\n  Explain    neural networks   \r\n  ";
const unnormalizedEvent = createDetectedQueryEvent({
  rawPrompt: unnormalizedRaw,
  triggerType: 'keyboard_enter'
});
assert.strictEqual(unnormalizedEvent.content.rawPrompt, unnormalizedRaw, 'rawPrompt must be 100% bit-for-bit preserved');
assert.strictEqual(unnormalizedEvent.content.normalizedPrompt, 'Explain neural networks', 'normalizedPrompt must be cleanly normalized');
assert.strictEqual(unnormalizedEvent.content.isNormalized, true);
assert(unnormalizedEvent.content.charactersSaved > 0);

// Verify metadata contains zero raw or normalized prompt text
assert.strictEqual(JSON.stringify(event.metadata).includes(prompt), false);
assert.strictEqual(JSON.stringify(event.context).includes(prompt), false);
console.log('PASS: User content strictly separated from event metadata with dual form preservation');

// Test 2: Safe context extraction
console.log('Test 2: Safe context extraction from location...');
const mockLocationExistingChat = {
  origin: 'https://claude.ai',
  pathname: '/chat/f47ac10b-58cc-4372-a567-0e02b2c3d479'
};
const ctxExisting = extractSafeContext(mockLocationExistingChat, 'Claude 3.5 Sonnet');
assert.strictEqual(ctxExisting.origin, 'https://claude.ai');
assert.strictEqual(ctxExisting.conversationId, 'f47ac10b-58cc-4372-a567-0e02b2c3d479');
assert.strictEqual(ctxExisting.isNewChat, false);
assert.strictEqual(ctxExisting.activeModelHint, 'Claude 3.5 Sonnet');

const mockLocationNewChat = {
  origin: 'https://claude.ai',
  pathname: '/new'
};
const ctxNew = extractSafeContext(mockLocationNewChat, null);
assert.strictEqual(ctxNew.conversationId, null);
assert.strictEqual(ctxNew.isNewChat, true);
assert.strictEqual(ctxNew.activeModelHint, null);
console.log('PASS: Safe context extraction verified without tokens or cookies');

// Test 3: Privacy classification
console.log('Test 3: Privacy classification heuristics...');
const standardPrivacy = classifyPrivacy('Write a Python function to compute Fibonacci numbers');
assert.strictEqual(standardPrivacy.level, 'STANDARD');
assert.strictEqual(standardPrivacy.flags.length, 0);

const secretPrompt = 'Please use my key sk-ant-api03-abcdef1234567890abcdef1234567890 to authenticate';
const sensitivePrivacy = classifyPrivacy(secretPrompt);
assert.strictEqual(sensitivePrivacy.level, 'SENSITIVE');
assert(sensitivePrivacy.flags.includes('ANTHROPIC_KEY'));

const privateKeyPrompt = 'Here is the key: -----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA';
const rsaPrivacy = classifyPrivacy(privateKeyPrompt);
assert.strictEqual(rsaPrivacy.level, 'SENSITIVE');
assert(rsaPrivacy.flags.includes('PRIVATE_KEY'));
console.log('PASS: Privacy classification detects sensitive tokens and keys');

// Test 4: Optimization placeholders initial state
console.log('Test 4: Optimization decision placeholders...');
assert.strictEqual(event.optimization.status, 'PENDING');
assert.strictEqual(event.optimization.decision, null);
assert.strictEqual(event.optimization.recommendedModel, null);
assert.strictEqual(event.optimization.cacheStatus, 'UNCHECKED');
assert.strictEqual(event.optimization.optimizedPrompt, null);
assert.strictEqual(event.optimization.tokenSavingsEstimate, 0);
assert.strictEqual(event.optimization.applied, false);
console.log('PASS: Optimization placeholders cleanly initialized');

// Test 5: Safe summary for logging (Zero rawPrompt leakage)
console.log('Test 5: Safe summary generation...');
const summary = toSafeSummary(event);
assert.strictEqual(summary.eventId, event.metadata.eventId);
assert.strictEqual(summary.characterCount, prompt.length);
assert.strictEqual(summary.wordCount, 7);
assert.strictEqual(summary.privacyLevel, 'STANDARD');
assert.strictEqual(summary.rawPrompt, undefined, 'rawPrompt must NOT exist in safe summary');
assert.strictEqual(summary.normalizedPrompt, undefined, 'normalizedPrompt must NOT exist in safe summary');
assert.strictEqual(summary.conversationId, undefined, 'conversationId must NOT exist in safe summary to prevent session identifier leakage');
assert(summary.featuresSummary !== null, 'featuresSummary must exist in safe summary');
assert.strictEqual(summary.featuresSummary.questionCount, 1);
assert.strictEqual(summary.requiresContextAnalysis, false);
assert.strictEqual(summary.contextCategory, 'STANDALONE');
assert.strictEqual(JSON.stringify(summary).includes(prompt), false, 'Prompt text completely absent from summary');
console.log('PASS: Safe summary completely isolates user prompt from logging surfaces');

// Test 6: Validation utility
console.log('Test 6: Query event validation...');
assert.strictEqual(validateQueryEvent(event).valid, true);
assert.strictEqual(validateQueryEvent(null).valid, false);
assert.strictEqual(validateQueryEvent({}).valid, false);
assert.strictEqual(validateQueryEvent({ metadata: {} }).valid, false);
console.log('PASS: Schema validation correctly accepts valid and rejects malformed events');

console.log('--- ALL QUERY EVENT TESTS PASSED ---');
