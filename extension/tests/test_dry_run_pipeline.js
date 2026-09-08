/**
 * Smart Query Router - Complete Optimizer Pipeline Tests (Dry-Run Mode)
 * 
 * Verifies:
 * 1. Off-by-default behavior: dry-run mode is disabled unless explicitly enabled.
 * 2. Complete pipeline execution when enabled:
 *    - Detection & feature extraction
 *    - Context selection & relevance ranking
 *    - Deterministic routing policy
 *    - Backend call & cache observation (exact match & semantic cache outcomes)
 *    - Assembled ProposedActionRecord with strict non-interference safety guarantees
 * 3. Behavior when backend is disabled (backendEnabled: false).
 * 4. Graceful fail-open resilience when backend throws.
 * 5. Bounded ring buffer storage (DryRunActionHistory).
 * 6. Background service worker message passing (DRY_RUN_RECORD) and diagnostics exposure.
 * 7. Strict privacy guarantees: zero raw prompt text in proposed action logs or diagnostics.
 */

'use strict';

const assert = require('assert');

// Import modules
const {
  OptimizerActionType,
  DryRunActionHistory,
  defaultDryRunHistory,
  executeDryRunPipeline
} = require('../src/shared/optimizer_pipeline');

const {
  MessageTypes,
  createDryRunRecordMessage,
  createDiagnosticsRequestMessage,
  validateMessage
} = require('../src/shared/messages');

const {
  UserSettingsManager,
  DEFAULT_USER_SETTINGS
} = require('../src/shared/user_settings');

const {
  RecentTurnsTracker
} = require('../src/shared/turn_tracker');

const {
  DiagnosticLogger,
  LogLevel
} = require('../src/shared/logger');

const {
  HealthTracker
} = require('../src/background/health_tracker');

console.log('--- Running Complete Optimizer Pipeline (Dry-Run Mode) Tests ---');

(async function runTests() {
  const logger = new DiagnosticLogger({ level: LogLevel.DEBUG, enableConsole: false });

  // Test 1: Disabled by default
  console.log('Test 1: Verifying dry-run mode is disabled by default...');
  const defaultSettingsManager = new UserSettingsManager();
  assert.strictEqual(defaultSettingsManager.isDryRunMode(), false, 'dryRunMode must be false by default');

  const disabledResult = await executeDryRunPipeline({
    promptText: 'Explain quantum computing',
    userSettingsManager: defaultSettingsManager,
    logger
  });

  assert.strictEqual(disabledResult.executed, false);
  assert.strictEqual(disabledResult.dryRunMode, false);
  assert.strictEqual(disabledResult.reason, 'DRY_RUN_MODE_DISABLED');
  assert.strictEqual(disabledResult.proposedAction, null);
  console.log('PASS: Dry-run mode correctly does not run when toggle is false');

  // Test 2: Full pipeline execution when dry-run toggle is enabled
  console.log('Test 2: Full dry-run pipeline execution with detection, context, routing, caching, and telemetry...');
  const activeSettingsManager = new UserSettingsManager();
  activeSettingsManager.setDryRunMode(true);
  assert.strictEqual(activeSettingsManager.isDryRunMode(), true);

  // Set up conversation turn history
  const turnTracker = new RecentTurnsTracker({ maxTurns: 5 });
  turnTracker.recordTurn({ role: 'user', text: 'What is Python asyncio?' });
  turnTracker.recordTurn({ role: 'assistant', text: 'Python asyncio is a library to write concurrent code.' });

  // Mock backend dispatcher returning cached model routing decision
  let mockBackendCalled = false;
  let receivedPackage = null;
  const mockBackendDispatcher = async (pkg) => {
    mockBackendCalled = true;
    receivedPackage = pkg;
    return {
      request_id: pkg.request_id,
      correlation_id: pkg.correlation_id,
      decision_type: 'MODEL_ROUTED',
      coarse_route: 'reasoning-candidate',
      model_route: 'claude-3-5-sonnet',
      cache_outcome: 'EXACT_CACHE_HIT',
      execution_metadata: {
        cache_key: 'exact_cache_key_hash_abc123',
        model_version: 'claude-3-5-sonnet-20241022',
        latency_ms: 12,
        semantic_cache_outcome: 'SKIPPED_EXACT_HIT',
        escalation_occurred: false
      }
    };
  };

  const pipelineResult = await executeDryRunPipeline({
    promptText: 'Can you show me an example of asyncio.gather()?',
    triggerSource: 'keyboard_enter',
    safeContext: { hostname: 'claude.ai', conversationId: 'chat_conv_test_123' },
    turnTracker,
    userSettingsManager: activeSettingsManager,
    backendDispatcher: mockBackendDispatcher,
    logger
  });

  assert.strictEqual(pipelineResult.executed, true);
  assert.strictEqual(pipelineResult.dryRunMode, true);
  assert.ok(pipelineResult.proposedAction, 'ProposedActionRecord must exist');
  assert.ok(mockBackendCalled, 'Backend dispatcher must have been called');

  const action = pipelineResult.proposedAction;

  // Verify Detection Phase
  assert.strictEqual(action.mode, 'DRY_RUN');
  assert.strictEqual(action.detection.trigger, 'keyboard_enter');
  assert.ok(action.detection.characterCount > 0);
  assert.ok(action.detection.wordCount > 0);
  assert.strictEqual(action.detection.dedupAccepted, true);

  // Verify Context Selection Phase
  assert.strictEqual(action.contextSelection.totalTurnsTracked, 2);
  assert.ok(action.contextSelection.hasContext);
  assert.ok(action.contextSelection.candidateTurnsCount >= 1);

  // Verify Routing Phase
  assert.ok(action.routing.coarseRoute !== null);

  // Verify Caching Observation
  assert.strictEqual(action.caching.cacheOutcome, 'EXACT_CACHE_HIT');
  assert.strictEqual(action.caching.semanticCacheOutcome, 'SKIPPED_EXACT_HIT');
  assert.strictEqual(action.caching.cacheKey, 'exact_cache_key_hash_abc123');

  // Verify Backend Observation
  assert.strictEqual(action.backend.enabled, true);
  assert.strictEqual(action.backend.called, true);
  assert.strictEqual(action.backend.status, 'SUCCESS');
  assert.strictEqual(action.backend.decisionType, 'MODEL_ROUTED');
  assert.strictEqual(action.backend.modelRoute, 'claude-3-5-sonnet');
  assert.strictEqual(action.backend.failOpen, false);

  // Verify Proposed Optimization Action
  assert.strictEqual(action.proposedOptimization.actionType, OptimizerActionType.MODEL_ROUTING);
  assert.strictEqual(action.proposedOptimization.targetModel, 'claude-3-5-sonnet');

  // CRITICAL INVARIANT: Strict non-interference safety guarantees
  assert.strictEqual(action.safetyGuarantees.appliedToUserRequest, false);
  assert.strictEqual(action.safetyGuarantees.actualClaudeRequestAltered, false);
  assert.strictEqual(action.safetyGuarantees.actualClaudeResponseAltered, false);
  assert.strictEqual(action.safetyGuarantees.userVisibleBehaviorAltered, false);

  // Privacy invariant: raw prompt text is not stored in the action record
  assert.strictEqual(action.rawPrompt, undefined);
  assert.strictEqual(action.queryText, undefined);
  assert.ok(action.querySummary.characterCount > 0);

  console.log('PASS: Complete pipeline executes and assembles proposed action with strict non-interference guarantees');

  // Test 3: Pipeline behavior when backend is disabled (backendEnabled: false)
  console.log('Test 3: Dry-run pipeline with backend disabled (local analysis only)...');
  const localSettingsManager = new UserSettingsManager();
  localSettingsManager.setDryRunMode(true);
  localSettingsManager.setBackendEnabled(false);
  assert.strictEqual(localSettingsManager.isBackendEnabled(), false);

  let disabledBackendCalled = false;
  const localResult = await executeDryRunPipeline({
    promptText: '2 + 2',
    userSettingsManager: localSettingsManager,
    backendDispatcher: async () => {
      disabledBackendCalled = true;
      return null;
    },
    logger
  });

  assert.strictEqual(localResult.executed, true);
  assert.strictEqual(disabledBackendCalled, false, 'Backend must NOT be called when backendEnabled is false');
  assert.strictEqual(localResult.proposedAction.backend.enabled, false);
  assert.strictEqual(localResult.proposedAction.backend.called, false);
  assert.strictEqual(localResult.proposedAction.backend.status, 'SKIPPED');
  assert.strictEqual(localResult.proposedAction.caching.cacheOutcome, 'NOT_CHECKED');
  assert.strictEqual(localResult.proposedAction.safetyGuarantees.userVisibleBehaviorAltered, false);
  console.log('PASS: Local-only pipeline analysis verified when backend is disabled');

  // Test 4: Graceful fail-open resilience when backend call fails
  console.log('Test 4: Graceful fail-open resilience on backend exception...');
  const failingResult = await executeDryRunPipeline({
    promptText: 'Explain deep learning architecture',
    userSettingsManager: activeSettingsManager,
    backendDispatcher: async () => {
      throw new Error('Connection refused by backend gateway');
    },
    logger
  });

  assert.strictEqual(failingResult.executed, true);
  assert.strictEqual(failingResult.proposedAction.backend.called, true);
  assert.strictEqual(failingResult.proposedAction.backend.status, 'FAIL_OPEN');
  assert.strictEqual(failingResult.proposedAction.backend.failOpen, true);
  assert.strictEqual(failingResult.proposedAction.backend.errorReason, 'Connection refused by backend gateway');
  assert.strictEqual(failingResult.proposedAction.safetyGuarantees.userVisibleBehaviorAltered, false);
  console.log('PASS: Graceful fail-open handling verified');

  // Test 5: Bounded ring buffer storage (DryRunActionHistory)
  console.log('Test 5: Bounded ring buffer history...');
  const history = new DryRunActionHistory(3);
  assert.strictEqual(history.count(), 0);

  history.add({ actionId: 'act_1' });
  history.add({ actionId: 'act_2' });
  history.add({ actionId: 'act_3' });
  assert.strictEqual(history.count(), 3);
  assert.strictEqual(history.getLatest().actionId, 'act_3');

  // Overflow drops oldest
  history.add({ actionId: 'act_4' });
  assert.strictEqual(history.count(), 3);
  assert.strictEqual(history.getAll()[0].actionId, 'act_2');
  assert.strictEqual(history.getLatest().actionId, 'act_4');
  console.log('PASS: Ring buffer correctly bounds history and discards oldest');

  // Test 6: Message protocol DRY_RUN_RECORD validation and HealthTracker recording
  console.log('Test 6: DRY_RUN_RECORD message validation and HealthTracker integration...');
  const sampleProposedAction = pipelineResult.proposedAction;
  const dryRunMsg = createDryRunRecordMessage(sampleProposedAction);
  assert.strictEqual(dryRunMsg.type, MessageTypes.DRY_RUN_RECORD);
  const msgValidation = validateMessage(dryRunMsg);
  assert.strictEqual(msgValidation.valid, true);

  const healthTracker = new HealthTracker();
  assert.strictEqual(healthTracker.getRecentDryRunActions().length, 0);

  healthTracker.recordDryRunAction(sampleProposedAction);
  assert.strictEqual(healthTracker.getRecentDryRunActions().length, 1);
  assert.strictEqual(healthTracker.getHealthSummary().dryRunActionsCount, 1);
  assert.strictEqual(healthTracker.getRecentDryRunActions()[0].actionId, sampleProposedAction.actionId);
  console.log('PASS: DRY_RUN_RECORD protocol message and HealthTracker integration verified');

  // Test 7: Diagnostics summary includes dry-run actions
  console.log('Test 7: Diagnostics summary includes recent dry-run actions...');
  const diagSummary = healthTracker.getHealthSummary();
  assert.strictEqual(diagSummary.dryRunActionsCount, 1);
  assert.strictEqual(diagSummary.status, 'STANDBY');
  console.log('PASS: Diagnostics summary safely reflects dry run actions count');

  console.log('--- ALL DRY-RUN PIPELINE TESTS PASSED ---');
})();
