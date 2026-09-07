/**
 * Smart Query Router - Unit Tests for Experimental Local Context Summarizer & Metrics
 * 
 * Verifies:
 * 1. Disabled by default configuration and strict production lock
 * 2. Inability to execute in production environment even if enabled: true
 * 3. Permitted execution in explicit 'test' or 'experimental' environments
 * 4. Deterministic extractive summarization without an on-device model
 * 5. Comparative metrics calculation (character reduction, token savings, retention rates)
 * 6. Telemetry privacy audit (strictly numeric/categorical, zero raw text leakage)
 * 7. Graceful handling of empty or edge-case turn packages
 */

const assert = require('assert');
const summarizer = require('../src/shared/experimental_summarizer');

console.log('--- Running Experimental Context Summarizer Tests ---');

// Test 1: Disabled by default configuration
console.log('Test 1: Disabled by default configuration...');
assert.strictEqual(summarizer.DEFAULT_CONFIG.enabled, false, 'Default config must have enabled: false');
assert.strictEqual(summarizer.DEFAULT_CONFIG.environment, 'production', 'Default config environment must be production');
assert.strictEqual(
  summarizer.isExperimentalSummarizationAllowed(summarizer.DEFAULT_CONFIG),
  false,
  'Default config must not allow experimental summarization'
);

const defaultExec = summarizer.evaluateExperimentalPath({
  directCandidatePackage: { includedTurns: [{ content: 'test turn' }] },
  config: summarizer.DEFAULT_CONFIG
});
assert.strictEqual(defaultExec, null, 'Default execution must return null without running');
console.log('PASS: Disabled by default verified');

// Test 2: Production safety lock
console.log('Test 2: Production safety lock...');
const accidentalProdConfig = {
  enabled: true,
  environment: 'production'
};
assert.strictEqual(
  summarizer.isExperimentalSummarizationAllowed(accidentalProdConfig),
  false,
  'Strict production lock must reject execution when environment is production even if enabled: true'
);

const prodExec = summarizer.evaluateExperimentalPath({
  directCandidatePackage: { includedTurns: [{ content: 'test turn' }] },
  config: accidentalProdConfig
});
assert.strictEqual(prodExec, null, 'Production execution must return null even if enabled: true');
console.log('PASS: Production safety lock verified');

// Test 3: Permitted execution in explicit test/experimental mode
console.log('Test 3: Permitted execution in experimental mode...');
const validExperimentalConfig = {
  enabled: true,
  environment: 'experimental'
};
assert.strictEqual(
  summarizer.isExperimentalSummarizationAllowed(validExperimentalConfig),
  true,
  'Explicit experimental mode must allow execution when enabled: true'
);

const validTestConfig = {
  enabled: true,
  environment: 'test'
};
assert.strictEqual(
  summarizer.isExperimentalSummarizationAllowed(validTestConfig),
  true,
  'Explicit test mode must allow execution when enabled: true'
);
console.log('PASS: Permitted execution verified');

// Test 4: Deterministic Extractive Summarization (Zero On-Device Model)
console.log('Test 4: Deterministic extractive summarization...');
const mockTurns = [
  {
    role: 'user',
    originalIndex: 0,
    content: 'We are designing a microservice with PostgreSQL and Redis. You must format as JSON arrays only.',
    preservationFlags: {
      codeBlockCount: 0,
      citationCount: 0,
      constraintCount: 1,
      namedEntityCount: 2
    }
  },
  {
    role: 'assistant',
    originalIndex: 1,
    content: 'Here is how to set up the connection pool using Python with ThreadedConnectionPool.\n```python\ndef setup_connection_pool(host, port, min_conn=5, max_conn=20):\n    pool = ThreadedConnectionPool(min_conn, max_conn, host=host, port=port)\n    return pool\n```\nThis handles connection lifecycle, automatic pooling, reconnection logic, and thread-safety for PostgreSQL cleanly without leaking connections under heavy concurrency.',
    preservationFlags: {
      codeBlockCount: 1,
      citationCount: 0,
      constraintCount: 0,
      namedEntityCount: 3
    }
  },
  {
    role: 'user',
    originalIndex: 2,
    content: 'Ensure all connections use SSL and keep timeout under 5000ms.',
    preservationFlags: {
      codeBlockCount: 0,
      citationCount: 0,
      constraintCount: 1,
      namedEntityCount: 1
    }
  }
];

const summaryPackage = summarizer.generateExtractiveSummary(mockTurns);
assert.strictEqual(summaryPackage.summaryType, 'NON_GENERATIVE_EXTRACTIVE_SUMMARY');
assert(summaryPackage.topicEntities.includes('PostgreSQL'), 'Must extract PostgreSQL entity');
assert(summaryPackage.topicEntities.includes('Redis'), 'Must extract Redis entity');
assert(summaryPackage.activeConstraints.length >= 1, 'Must extract active user constraints');
assert(summaryPackage.activeConstraints[0].includes('must format as JSON'), 'Extracted constraint must match');
assert(summaryPackage.codeSignatures.some((sig) => sig.includes('setup_connection_pool')), 'Must extract code signature');
assert(summaryPackage.characterCount > 0, 'Must record character count');
assert(summaryPackage.estimatedTokens > 0, 'Must record estimated tokens');
console.log('PASS: Extractive summarization verified');

// Test 5: Comparative Metrics Calculation
console.log('Test 5: Comparative metrics calculation...');
const mockDirectCandidatePackage = {
  strategy: 'FILTERED_RELEVANT',
  includedTurns: mockTurns
};

const comparison = summarizer.compareContextStrategies(mockDirectCandidatePackage, summaryPackage, 2.5);

assert(comparison.directTotalCharacters > comparison.summaryTotalCharacters, 'Direct characters should exceed summarized characters');
assert(comparison.characterReductionRatio > 0.3, 'Should achieve significant character reduction');
assert(comparison.estimatedTokenSavings > 0, 'Should yield positive estimated token savings');
assert(comparison.entityRetentionRate > 0.5, 'Should retain majority of named entities');
assert(comparison.constraintRetentionRate >= 0.5, 'Should retain active user constraints');
assert(comparison.codeRetentionRate >= 0.5, 'Should retain code signatures');
assert.strictEqual(comparison.latencyMs, 2.5, 'Should record latency correctly');
console.log('PASS: Comparative metrics calculation verified');

// Test 6: Telemetry Privacy Audit (Zero Raw Text Exposure)
console.log('Test 6: Telemetry privacy audit...');
const telemetry = summarizer.toSafeComparisonTelemetry(comparison);

assert.strictEqual(telemetry.metric_type, 'context_compression_comparison');
assert.strictEqual(telemetry.valid, true);
assert.strictEqual(typeof telemetry.strategy_direct_chars, 'number');
assert.strictEqual(typeof telemetry.strategy_summary_chars, 'number');
assert.strictEqual(typeof telemetry.reduction_ratio, 'number');
assert.strictEqual(typeof telemetry.token_savings_est, 'number');
assert.strictEqual(typeof telemetry.entity_retention_rate, 'number');
assert.strictEqual(typeof telemetry.constraint_retention_rate, 'number');
assert.strictEqual(typeof telemetry.code_retention_rate, 'number');
assert.strictEqual(typeof telemetry.latency_ms, 'number');

// Strict privacy verification: Ensure NO telemetry key or value contains raw prompt text or entity values
const sensitiveKeys = ['prompt', 'query', 'text', 'content', 'snippet', 'entity', 'topic', 'postgresql', 'redis', 'user', 'assistant'];
for (const [key, value] of Object.entries(telemetry)) {
  const lowerKey = key.toLowerCase();
  for (const sKey of sensitiveKeys) {
    assert(!lowerKey.includes(sKey) || key === 'entity_retention_rate', `Telemetry key "${key}" must not contain sensitive name "${sKey}"`);
  }
  if (typeof value === 'string') {
    assert(value === 'context_compression_comparison', `String value in telemetry must only be known metric_type, got: ${value}`);
  }
}
console.log('PASS: Telemetry privacy audit verified (zero raw conversation leakage)');

// Test 7: Full Experimental Path Execution End-to-End in Test Mode
console.log('Test 7: Full experimental path end-to-end...');
const fullEval = summarizer.evaluateExperimentalPath({
  directCandidatePackage: mockDirectCandidatePackage,
  config: { enabled: true, environment: 'test' }
});

assert(fullEval !== null, 'Full evaluation must succeed in test environment');
assert(fullEval.summaryPackage !== null, 'summaryPackage must be produced');
assert(fullEval.comparison !== null, 'comparison metrics must be produced');
assert(fullEval.telemetry !== null, 'telemetry must be produced');
assert.strictEqual(fullEval.telemetry.valid, true);
console.log('PASS: Full experimental path end-to-end verified');

// Test 8: Empty / Edge Cases
console.log('Test 8: Edge cases handling...');
const emptyEval = summarizer.evaluateExperimentalPath({
  directCandidatePackage: { includedTurns: [] },
  config: { enabled: true, environment: 'test' }
});
assert(emptyEval !== null);
assert.strictEqual(emptyEval.summaryPackage.characterCount, 0);
assert.strictEqual(emptyEval.comparison.directTotalCharacters, 0);

console.log('--- ALL EXPERIMENTAL SUMMARIZER TESTS PASSED ---');
