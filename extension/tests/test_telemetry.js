/**
 * Smart Query Router - Unit Tests for Performance Telemetry & Correlation Tracker
 * 
 * Verifies:
 * 1. Correlation identifier format, uniqueness, and structure.
 * 2. Privacy-preserving character count bucketing.
 * 3. Safe non-generative feature summary extraction (zero prompt text).
 * 4. Production vs development debug mode toggle behavior.
 * 5. Structured PerformanceTelemetryRecord creation with zero query text in production.
 * 6. Opt-in development debugging mechanism.
 */

const assert = require('assert');
const {
  CacheOutcome,
  ErrorCategory,
  generateCorrelationId,
  bucketCharacterCount,
  extractSafeFeatureSummary,
  isDebugModeEnabled,
  sanitizeDebugMetadata,
  createPerformanceRecord
} = require('../src/shared/telemetry');

console.log('--- Running Telemetry & Correlation ID Tests ---');

// Test 1: Correlation ID generation format and uniqueness
console.log('Test 1: Correlation ID generation...');
const id1 = generateCorrelationId();
const id2 = generateCorrelationId();
assert(typeof id1 === 'string', 'Correlation ID must be a string');
assert(id1.startsWith('corr_'), `Correlation ID must start with 'corr_', got: ${id1}`);
assert(id1 !== id2, 'Successive correlation IDs must be unique');
const parts = id1.split('_');
assert.strictEqual(parts.length, 3, 'Correlation ID must follow corr_<timestamp>_<random>');
assert(!isNaN(Number(parts[1])), 'Middle segment must be numeric timestamp');
console.log('PASS: Correlation ID format verified');

// Test 2: Character count bucketing
console.log('Test 2: Character count bucketing...');
assert.strictEqual(bucketCharacterCount(0), '0');
assert.strictEqual(bucketCharacterCount(-5), '0');
assert.strictEqual(bucketCharacterCount(null), '0');
assert.strictEqual(bucketCharacterCount(25), '<50');
assert.strictEqual(bucketCharacterCount(49), '<50');
assert.strictEqual(bucketCharacterCount(50), '50-200');
assert.strictEqual(bucketCharacterCount(199), '50-200');
assert.strictEqual(bucketCharacterCount(200), '200-1000');
assert.strictEqual(bucketCharacterCount(999), '200-1000');
assert.strictEqual(bucketCharacterCount(1000), '1000-5000');
assert.strictEqual(bucketCharacterCount(4999), '1000-5000');
assert.strictEqual(bucketCharacterCount(5000), '>5000');
assert.strictEqual(bucketCharacterCount(25000), '>5000');
console.log('PASS: Character count bucketing verified');

// Test 3: Safe feature summary extraction
console.log('Test 3: Safe feature summary extraction...');
const rawFeatures = {
  character_count: 150,
  word_count: 22,
  has_code: true,
  has_math: false,
  has_questions: true,
  has_urls: false,
  is_normalized: true,
  detected_cues: ['how to', 'compare'],
  raw_query_injected: 'Sensitive user text that must never be extracted'
};
const summary = extractSafeFeatureSummary(rawFeatures, 3);
assert.strictEqual(summary.char_bucket, '50-200');
assert.strictEqual(summary.has_code, true);
assert.strictEqual(summary.has_math, false);
assert.strictEqual(summary.has_questions, true);
assert.strictEqual(summary.has_urls, false);
assert.strictEqual(summary.is_normalized, true);
assert.strictEqual(summary.cue_count, 2);
assert.strictEqual(summary.candidate_count, 3);
assert.strictEqual(summary.raw_query_injected, undefined);
const summaryJson = JSON.stringify(summary);
assert(!summaryJson.includes('Sensitive'), 'Feature summary must not contain raw text');
console.log('PASS: Safe feature summary extraction verified');

// Test 4: Debug mode evaluation
console.log('Test 4: Debug mode toggle evaluation...');
assert.strictEqual(isDebugModeEnabled(), false, 'Default must be false');
assert.strictEqual(isDebugModeEnabled({}), false, 'Empty options must be false');
assert.strictEqual(isDebugModeEnabled({ environment: 'production' }), false, 'Production must be false');
assert.strictEqual(isDebugModeEnabled({ debugMode: false }), false);
assert.strictEqual(isDebugModeEnabled({ debugMode: true }), true, 'Explicit debugMode: true must enable');
assert.strictEqual(isDebugModeEnabled({ environment: 'development' }), true, 'development env must enable');
console.log('PASS: Debug mode toggle verified');

// Test 5: Production performance telemetry record (Zero raw query text)
console.log('Test 5: Production performance telemetry record creation...');
const clientStart = Date.now() - 120;
const prodRecord = createPerformanceRecord({
  correlationId: id1,
  clientTimestamp: clientStart,
  backendTimestamp: clientStart + 115,
  decisionType: 'NO_OPTIMIZATION',
  modelRoute: null,
  cacheOutcome: CacheOutcome.NOT_CHECKED,
  latencyMs: 120.456,
  errorCategory: ErrorCategory.NONE,
  localFeatures: rawFeatures,
  candidateCount: 2,
  versionIdentifiers: { extension: '0.1.0' },
  options: { environment: 'production' } // Production mode
});

assert.strictEqual(prodRecord.correlation_id, id1);
assert.strictEqual(prodRecord.client_timestamp, clientStart);
assert.strictEqual(prodRecord.backend_timestamp, clientStart + 115);
assert.strictEqual(prodRecord.decision_type, 'NO_OPTIMIZATION');
assert.strictEqual(prodRecord.model_route, null);
assert.strictEqual(prodRecord.cache_outcome, CacheOutcome.NOT_CHECKED);
assert.strictEqual(prodRecord.latency_ms, 120.46);
assert.strictEqual(prodRecord.error_category, ErrorCategory.NONE);
assert.strictEqual(prodRecord.debug_metadata, null, 'debug_metadata must be strictly null in production');

// Privacy guarantee check
const prodRecordStr = JSON.stringify(prodRecord);
const forbiddenTerms = ['prompt', 'query_text', 'Sensitive', 'how to', 'compare'];
for (const term of forbiddenTerms) {
  assert(!prodRecordStr.includes(term), `Production record must not contain: ${term}`);
}
console.log('PASS: Production record privacy guarantee verified');

// Test 6: Development debug telemetry record (Opt-in)
console.log('Test 6: Development debug telemetry record creation...');
const devTrace = {
  dispatch_queue_size: 1,
  mock_diagnostic_step: 'service_worker_ipc_ok'
};
const devRecord = createPerformanceRecord({
  correlationId: id2,
  clientTimestamp: clientStart,
  latencyMs: 45.0,
  options: { debugMode: true }, // Explicit opt-in
  debugTrace: devTrace
});

assert.strictEqual(devRecord.correlation_id, id2);
assert.deepStrictEqual(devRecord.debug_metadata, devTrace, 'debug_metadata should be populated in dev mode');
console.log('PASS: Development debug telemetry verified');

// Test 6b: Debug metadata sanitization (forbidden cookies, tokens, session IDs, and raw text)
console.log('Test 6b: Debug metadata sanitization...');
const dirtyTrace = {
  stage: 'candidate_evaluation',
  cookie: 'session=secret_cookie_val',
  authToken: 'Bearer secret_token_123',
  sessionId: 'sess_abc456',
  url: 'https://claude.ai/chat/conv-uuid-1',
  query_text: 'Secret user prompt in debug trace',
  prompt: 'Another raw prompt',
  safeMetric: 42,
  nested: {
    apiKey: 'sk-12345',
    token: 'jwt.token.here',
    turn_content: 'Nested prompt turn',
    valid_flag: true
  }
};

// Without allowRawConversationText: cookies, tokens, sessions, AND raw text must be stripped
const cleanedDefault = sanitizeDebugMetadata(dirtyTrace, { debugMode: true });
assert.strictEqual(cleanedDefault.stage, 'candidate_evaluation');
assert.strictEqual(cleanedDefault.safeMetric, 42);
assert.strictEqual(cleanedDefault.cookie, undefined);
assert.strictEqual(cleanedDefault.authToken, undefined);
assert.strictEqual(cleanedDefault.sessionId, undefined);
assert.strictEqual(cleanedDefault.url, undefined);
assert.strictEqual(cleanedDefault.query_text, undefined);
assert.strictEqual(cleanedDefault.prompt, undefined);
assert.strictEqual(cleanedDefault.nested.apiKey, undefined);
assert.strictEqual(cleanedDefault.nested.token, undefined);
assert.strictEqual(cleanedDefault.nested.turn_content, undefined);
assert.strictEqual(cleanedDefault.nested.valid_flag, true);
console.log('PASS: Debug metadata sanitization strips cookies, tokens, session IDs, and raw text by default');

// Test 6c: User-initiated debugging path with allowRawConversationText: true
console.log('Test 6c: User-initiated debugging path with allowRawConversationText: true...');
const cleanedWithRawText = sanitizeDebugMetadata(dirtyTrace, { debugMode: true, allowRawConversationText: true });
assert.strictEqual(cleanedWithRawText.query_text, 'Secret user prompt in debug trace');
assert.strictEqual(cleanedWithRawText.prompt, 'Another raw prompt');
assert.strictEqual(cleanedWithRawText.nested.turn_content, 'Nested prompt turn');
// Crucial: Cookies, tokens, and session IDs are STILL purged!
assert.strictEqual(cleanedWithRawText.cookie, undefined);
assert.strictEqual(cleanedWithRawText.authToken, undefined);
assert.strictEqual(cleanedWithRawText.sessionId, undefined);
assert.strictEqual(cleanedWithRawText.nested.apiKey, undefined);
console.log('PASS: User-initiated debugging preserves text but strictly purges cookies, tokens, and session IDs');

// Test 7: CacheOutcome and ErrorCategory enums
console.log('Test 7: CacheOutcome and ErrorCategory enums...');
assert.strictEqual(CacheOutcome.HIT, 'HIT');
assert.strictEqual(CacheOutcome.MISS, 'MISS');
assert.strictEqual(CacheOutcome.BYPASS, 'BYPASS');
assert.strictEqual(CacheOutcome.NOT_CHECKED, 'NOT_CHECKED');

assert.strictEqual(ErrorCategory.NONE, 'NONE');
assert.strictEqual(ErrorCategory.TIMEOUT, 'TIMEOUT');
assert.strictEqual(ErrorCategory.NETWORK_ERROR, 'NETWORK_ERROR');
assert.strictEqual(ErrorCategory.SCHEMA_ERROR, 'SCHEMA_ERROR');
assert.strictEqual(ErrorCategory.SERVER_ERROR, 'SERVER_ERROR');
assert.strictEqual(ErrorCategory.CLIENT_ERROR, 'CLIENT_ERROR');
console.log('PASS: Enums verified');

// Test 8: Route, model version, latency, and failure category metadata
console.log('Test 8: Execution metadata in performance telemetry...');
const execMetaRecord = createPerformanceRecord({
  correlationId: id1,
  clientTimestamp: clientStart,
  decisionType: 'NO_OPTIMIZATION',
  coarseRoute: 'complex-model candidate',
  modelVersion: 'gpt-4o-2024-08-06',
  failureCategory: 'NONE',
  latencyMs: 145.2,
  executionLatencyMs: 130.55,
  localFeatures: rawFeatures,
  candidateCount: 1,
  options: { environment: 'production' }
});

assert.strictEqual(execMetaRecord.coarse_route, 'complex-model candidate');
assert.strictEqual(execMetaRecord.model_version, 'gpt-4o-2024-08-06');
assert.strictEqual(execMetaRecord.failure_category, 'NONE');
assert.strictEqual(execMetaRecord.execution_latency_ms, 130.55);
assert.strictEqual(execMetaRecord.latency_ms, 145.2);

const fallbackRecord = createPerformanceRecord({
  correlationId: id2,
  clientTimestamp: clientStart,
  decisionType: 'NO_OPTIMIZATION',
  coarseRoute: 'simple-model candidate',
  modelVersion: null,
  failureCategory: 'TIMEOUT',
  latencyMs: 10005.0,
  executionLatencyMs: 10001.2,
  localFeatures: rawFeatures,
  candidateCount: 0,
  options: { environment: 'production' }
});

assert.strictEqual(fallbackRecord.coarse_route, 'simple-model candidate');
assert.strictEqual(fallbackRecord.model_version, null);
assert.strictEqual(fallbackRecord.failure_category, 'TIMEOUT');
assert.strictEqual(fallbackRecord.execution_latency_ms, 10001.2);
assert.strictEqual(fallbackRecord.escalation_occurred, false);
assert.strictEqual(fallbackRecord.escalation_reason, null);
console.log('PASS: Execution metadata in performance telemetry verified');

// Test 9: Escalation metadata tracking
console.log('Test 9: Escalation metadata tracking...');
const escalatedRecord = createPerformanceRecord({
  correlationId: id1,
  clientTimestamp: clientStart,
  decisionType: 'NO_OPTIMIZATION',
  coarseRoute: 'simple-model candidate',
  modelVersion: 'gpt-4o-2024-08-06',
  failureCategory: 'NONE',
  latencyMs: 350.5,
  executionLatencyMs: 310.2,
  escalationOccurred: true,
  escalationReason: 'Evaluator recommended escalation: unclosed code block',
  localFeatures: rawFeatures,
  candidateCount: 1,
  options: { environment: 'production' }
});

assert.strictEqual(escalatedRecord.escalation_occurred, true);
assert.strictEqual(escalatedRecord.escalation_reason, 'Evaluator recommended escalation: unclosed code block');
assert.strictEqual(escalatedRecord.model_version, 'gpt-4o-2024-08-06');
console.log('PASS: Escalation metadata tracking verified');

console.log('--- ALL TELEMETRY & CORRELATION ID TESTS PASSED ---');


