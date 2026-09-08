/**
 * Smart Query Router - Unit Tests for Privacy Configuration & Retention
 * 
 * Verifies:
 * 1. Default privacy configuration values, structure, and immutability.
 * 2. Schema validation rejects malformed privacy settings.
 * 3. UserSettingsManager privacy accessors and partial updates.
 * 4. Decoupling Guarantee: Disabling telemetry does NOT disable the core optimizer.
 * 5. Bounded content retention: RecentTurnsTracker prunes turns older than TTL.
 * 6. Bounded diagnostics retention: DiagnosticLogger prunes entries older than TTL.
 * 7. Category-level switches for optimization and diagnostics.
 */

const assert = require('assert');
const {
  DEFAULT_PRIVACY_CONFIG,
  validatePrivacyConfig,
  createPrivacyConfig
} = require('../src/shared/privacy_config');

const {
  UserSettingsManager,
  createUserSettings
} = require('../src/shared/user_settings');

const { RecentTurnsTracker } = require('../src/shared/turn_tracker');
const { DiagnosticLogger, EventCategory } = require('../src/shared/logger');
const { executeDryRunPipeline } = require('../src/shared/optimizer_pipeline');

console.log('--- Running Privacy Configuration & Retention Tests ---');

// Test 1: Default privacy configuration values and immutability
console.log('Test 1: Default privacy configuration values and immutability...');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.version, '1.0.0');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.telemetry.enabled, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.telemetry.performanceMetrics, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.telemetry.errorMetrics, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.telemetry.featureMetrics, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.telemetry.allowRawConversationText, false, 'Raw conversation text must be false by default');

assert.strictEqual(DEFAULT_PRIVACY_CONFIG.diagnostics.enabled, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.diagnostics.consoleOutput, false, 'Console output must be quiet by default');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.diagnostics.retentionTtlMs, 300000, '5 minutes max log retention');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.diagnostics.maxEntries, 50);

assert.strictEqual(DEFAULT_PRIVACY_CONFIG.optimization.localRules, true, 'Core local rules must be enabled by default');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.optimization.promptNormalization, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.optimization.contextPruning, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.optimization.backendRouting, true);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.optimization.allowUiSubstitution, true);

assert.strictEqual(DEFAULT_PRIVACY_CONFIG.retention.maxTurnHistory, 4);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.retention.turnSnippetMaxChars, 300);
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.retention.turnRetentionTtlMs, 900000, '15 minutes max turn retention');
assert.strictEqual(DEFAULT_PRIVACY_CONFIG.retention.transientEventTtlMs, 60000, '60 seconds max transient prompt retention');

assert(Object.isFrozen(DEFAULT_PRIVACY_CONFIG), 'DEFAULT_PRIVACY_CONFIG must be frozen');
assert(Object.isFrozen(DEFAULT_PRIVACY_CONFIG.telemetry), 'telemetry section must be frozen');
assert(Object.isFrozen(DEFAULT_PRIVACY_CONFIG.diagnostics), 'diagnostics section must be frozen');
assert(Object.isFrozen(DEFAULT_PRIVACY_CONFIG.optimization), 'optimization section must be frozen');
assert(Object.isFrozen(DEFAULT_PRIVACY_CONFIG.retention), 'retention section must be frozen');
console.log('PASS: Default privacy configuration is privacy-preserving, usable, and frozen');

// Test 2: Validation of privacy configurations
console.log('Test 2: Schema validation of privacy configurations...');
assert.strictEqual(validatePrivacyConfig(DEFAULT_PRIVACY_CONFIG).valid, true);
assert.strictEqual(validatePrivacyConfig(null).valid, false);
assert.strictEqual(validatePrivacyConfig('invalid').valid, false);
assert.strictEqual(validatePrivacyConfig({ telemetry: 'not-an-object' }).valid, false);
assert.strictEqual(validatePrivacyConfig({ telemetry: { enabled: 'yes' } }).valid, false);
assert.strictEqual(validatePrivacyConfig({ diagnostics: { retentionTtlMs: -10 } }).valid, false);
assert.strictEqual(validatePrivacyConfig({ optimization: { localRules: 'true' } }).valid, false);
assert.strictEqual(validatePrivacyConfig({ retention: { maxTurnHistory: 0 } }).valid, false);
console.log('PASS: Schema validation correctly rejects malformed privacy objects');

// Test 3: createPrivacyConfig merging and immutability
console.log('Test 3: createPrivacyConfig merging and immutability...');
const customPrivacy = createPrivacyConfig({
  telemetry: { enabled: false },
  diagnostics: { consoleOutput: true }
});
assert.strictEqual(customPrivacy.telemetry.enabled, false);
assert.strictEqual(customPrivacy.telemetry.performanceMetrics, true, 'Unspecified fields preserve defaults');
assert.strictEqual(customPrivacy.diagnostics.consoleOutput, true);
assert.strictEqual(customPrivacy.optimization.localRules, true);
assert(Object.isFrozen(customPrivacy));
console.log('PASS: createPrivacyConfig merges overrides cleanly');

// Test 4: UserSettingsManager privacy accessors and partial updates
console.log('Test 4: UserSettingsManager privacy integration...');
const manager = new UserSettingsManager();
const initialPrivacy = manager.getPrivacyConfig();
assert.strictEqual(initialPrivacy.telemetry.enabled, true);
assert.strictEqual(manager.isTelemetryEnabled('performanceMetrics'), true);
assert.strictEqual(manager.isDiagnosticsEnabled('consoleOutput'), false);
assert.strictEqual(manager.isOptimizationCategoryEnabled('localRules'), true);

// Update telemetry category
manager.updatePrivacyConfig({
  telemetry: { enabled: false }
}).then((updatedSettings) => {
  assert.strictEqual(manager.isTelemetryEnabled('performanceMetrics'), false);
  assert.strictEqual(manager.isTelemetryEnabled(), false);
  // Optimization is NOT affected
  assert.strictEqual(manager.isOptimizationCategoryEnabled('localRules'), true);
  console.log('PASS: UserSettingsManager privacy accessors and updates verified');

  // Test 5: Decoupling Guarantee - Disabling telemetry does NOT disable core optimizer
  console.log('Test 5: Decoupling Guarantee - core optimizer executes with telemetry disabled...');
  const mockDecisionEngine = {
    evaluate: (queryEvent) => ({
      outcome: 'LOCAL_ANSWER',
      ruleId: 'rule_greeting',
      reason: 'Standard greeting'
    })
  };

  const mockRoutingPolicy = {
    classify: (event, opts) => ({
      route: 'local-eligible',
      modelTier: 'fast_cheap',
      ruleId: 'RULE_LOCAL_GREETING',
      reasonCode: 'LOCAL_RULE_MATCH',
      confidence: 1.0,
      suggestedModel: null
    })
  };

  let telemetryGenerated = false;
  const mockTelemetry = {
    createPerformanceRecord: () => {
      telemetryGenerated = true;
      return { correlation_id: 'corr_test' };
    }
  };

  executeDryRunPipeline({
    promptText: 'Hello there!',
    triggerSource: 'keyboard_enter',
    decisionEngine: mockDecisionEngine,
    routingPolicy: mockRoutingPolicy,
    userSettingsManager: manager, // manager has telemetry.enabled = false
    telemetry: mockTelemetry,
    forceDryRun: true
  }).then((pipelineResult) => {
    assert(pipelineResult, 'Pipeline must return a result');
    assert.strictEqual(pipelineResult.executed, true, 'Pipeline must execute successfully');
    assert(pipelineResult.proposedAction, 'Proposed action must be generated');
    assert.strictEqual(pipelineResult.proposedAction.routing.coarseRoute, 'local-eligible');
    assert.strictEqual(pipelineResult.proposedAction.routing.localDecision.outcome, 'LOCAL_ANSWER');
    assert.strictEqual(telemetryGenerated, false, 'Telemetry performance record must be bypassed when telemetry is disabled');
    console.log('PASS: Decoupling verified: optimizer fully functional with telemetry disabled');

    // Test 6: Bounded content retention in RecentTurnsTracker (TTL eviction)
    console.log('Test 6: Bounded content retention in RecentTurnsTracker (TTL eviction)...');
    const shortTtlTracker = new RecentTurnsTracker({
      maxTurns: 4,
      retentionTtlMs: 200 // 200ms retention
    });

    const t0 = Date.now();
    shortTtlTracker.recordTurn({
      role: 'user',
      text: 'First question that should expire',
      timestamp: t0
    });

    assert.strictEqual(shortTtlTracker.turns.length, 1, 'Turn recorded at t0');
    // Check with timestamp past TTL (t0 + 250ms)
    const prunedCount = shortTtlTracker.pruneExpiredTurns(t0 + 250);
    assert.strictEqual(prunedCount, 1, 'Expired turn must be pruned');
    assert.strictEqual(shortTtlTracker.turns.length, 0, 'Tracker must be empty after TTL expiration');

    // Normal recordTurn with timestamp past TTL also prunes prior turns
    const oldTurnTimestamp = Date.now() - 500;
    shortTtlTracker.turns.push({
      turnId: 'old_1',
      role: 'user',
      timestamp: oldTurnTimestamp,
      snippet: 'Ancient turn'
    });
    shortTtlTracker.recordTurn({ role: 'assistant', text: 'New fresh turn', timestamp: Date.now() });
    assert.strictEqual(shortTtlTracker.getTurnCount(), 1, 'Old turn pruned automatically on new recordTurn');
    assert.strictEqual(shortTtlTracker.getLastTurn().snippet, 'New fresh turn');
    console.log('PASS: RecentTurnsTracker automatically prunes turns older than TTL');

    // Test 7: Bounded diagnostics retention in DiagnosticLogger (TTL eviction)
    console.log('Test 7: Bounded diagnostics retention in DiagnosticLogger (TTL eviction)...');
    const shortTtlLogger = new DiagnosticLogger({
      maxBufferSize: 10,
      retentionTtlMs: 150 // 150ms retention
    });

    shortTtlLogger.info(EventCategory.STARTUP, 'Startup message at t0');
    assert.strictEqual(shortTtlLogger.getRecentLogs().length, 1);

    // Simulate entry age > 150ms
    shortTtlLogger.ringBuffer[0].timestamp = Date.now() - 300;
    const activeLogs = shortTtlLogger.getRecentLogs();
    assert.strictEqual(activeLogs.length, 0, 'Expired log entries must be pruned automatically');
    console.log('PASS: DiagnosticLogger automatically prunes log entries older than TTL');

    // Test 8: Optimization category toggle verification
    console.log('Test 8: Optimization category toggle verification...');
    manager.updatePrivacyConfig({
      telemetry: { enabled: true },
      optimization: { localRules: false }
    }).then((settingsAfterLocalRulesOff) => {
      assert.strictEqual(manager.isOptimizationCategoryEnabled('localRules'), false);
      assert.strictEqual(manager.isOptimizationCategoryEnabled('promptNormalization'), true);

      // Re-run pipeline with localRules disabled
      executeDryRunPipeline({
        promptText: 'Hello there!',
        triggerSource: 'keyboard_enter',
        decisionEngine: mockDecisionEngine,
        routingPolicy: mockRoutingPolicy,
        userSettingsManager: manager,
        forceDryRun: true
      }).then((resultLocalRulesOff) => {
        assert.strictEqual(resultLocalRulesOff.proposedAction.routing.localDecision, null, 'Local decision must be null when localRules is disabled');
        console.log('PASS: Optimization category toggle cleanly disables specific optimization sub-features');

        console.log('--- ALL PRIVACY CONFIGURATION TESTS PASSED ---');
      });
    });
  });
});
