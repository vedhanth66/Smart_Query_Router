/**
 * Test Suite: Optimizer Metrics & Activity Tracker
 * 
 * Verifies:
 * 1. Correct aggregation of enabled state and user routing mode.
 * 2. Cache hit rate calculation across zero, single, and mixed outcomes.
 * 3. Small vs Strong model route distribution percentages and counts.
 * 4. Estimated token savings accumulation.
 * 5. Bounded FIFO ring buffer for recent activity (max 15 items).
 * 6. STRICT PRIVACY INVARIANT: Rejects and strips raw user prompts or conversational text.
 * 7. Persistence to simulated chrome.storage.local and reload recovery.
 */

const assert = require('assert');
const metricsModule = require('../src/shared/optimizer_metrics');
const userSettingsModule = require('../src/shared/user_settings');

const {
  OptimizerMetricsTracker,
  sanitizeActivityRecord,
  MAX_RECENT_ACTIVITY
} = metricsModule;

const {
  UserSettingsManager
} = userSettingsModule;

console.log('--- Running Optimizer Metrics Tests ---');

// Mock storage factory
function createMockStorage() {
  const store = {};
  return {
    get: (keys, callback) => {
      const result = {};
      for (const k of keys) {
        if (store[k] !== undefined) {
          result[k] = JSON.parse(JSON.stringify(store[k]));
        }
      }
      callback(result);
    },
    set: (items, callback) => {
      for (const [k, v] of Object.entries(items)) {
        store[k] = JSON.parse(JSON.stringify(v));
      }
      if (callback) callback();
    },
    _getRaw: () => store
  };
}

// Test 1: Initialization & Empty State
console.log('Test 1: Tracker initialization and zero state...');
const mockStorage1 = createMockStorage();
const settingsManager1 = new UserSettingsManager();
const tracker1 = new OptimizerMetricsTracker({
  storage: mockStorage1,
  userSettingsManager: settingsManager1
});

const initialSummary = tracker1.getMetricsSummary();
assert.strictEqual(initialSummary.totalQueries, 0);
assert.strictEqual(initialSummary.cacheHitRate.percentage, '0.0%');
assert.strictEqual(initialSummary.cacheHitRate.hits, 0);
assert.strictEqual(initialSummary.cacheHitRate.totalEvaluated, 0);
assert.strictEqual(initialSummary.routeDistribution.smallCount, 0);
assert.strictEqual(initialSummary.routeDistribution.strongCount, 0);
assert.strictEqual(initialSummary.estimatedTokenSavings.totalTokensSaved, 0);
assert.strictEqual(initialSummary.recentActivity.length, 0);
assert.strictEqual(initialSummary.enabledState.optimizationEnabled, true);
assert.strictEqual(initialSummary.enabledState.routingOverride, 'automatic');
console.log('PASS: Initialization and zero state verified');

// Test 2: Recording Activities & Route Distribution Calculation
console.log('Test 2: Recording activities and route distribution...');
// Record 3 simple model routes
tracker1.recordActivity({
  route: 'simple-model candidate',
  modelTier: 'simple',
  cacheOutcome: 'MISS',
  tokensSaved: 25,
  latencyMs: 120,
  status: 'COMPLETED'
});

tracker1.recordActivity({
  route: 'simple-model candidate',
  modelTier: 'simple',
  cacheOutcome: 'HIT',
  tokensSaved: 80,
  latencyMs: 45,
  status: 'COMPLETED'
});

tracker1.recordActivity({
  route: 'simple-model candidate',
  modelTier: 'simple',
  cacheOutcome: 'HIT',
  tokensSaved: 50,
  latencyMs: 50,
  status: 'COMPLETED'
});

// Record 1 strong model route
tracker1.recordActivity({
  route: 'strong-model candidate',
  modelTier: 'strong',
  cacheOutcome: 'MISS',
  tokensSaved: 0,
  latencyMs: 1800,
  status: 'COMPLETED'
});

const summary2 = tracker1.getMetricsSummary();
assert.strictEqual(summary2.totalQueries, 4);
assert.strictEqual(summary2.routeDistribution.smallCount, 3);
assert.strictEqual(summary2.routeDistribution.strongCount, 1);
assert.strictEqual(summary2.routeDistribution.totalRouted, 4);
assert.strictEqual(summary2.routeDistribution.smallPercentage, '75.0%');
assert.strictEqual(summary2.routeDistribution.strongPercentage, '25.0%');
console.log('PASS: Route distribution calculations (75% small, 25% strong) verified');

// Test 3: Cache Hit Rate Calculation
console.log('Test 3: Cache hit rate calculation...');
// Out of 4 recorded queries: 2 hits, 2 misses -> 50% hit rate
assert.strictEqual(summary2.cacheHitRate.hits, 2);
assert.strictEqual(summary2.cacheHitRate.misses, 2);
assert.strictEqual(summary2.cacheHitRate.totalEvaluated, 4);
assert.strictEqual(summary2.cacheHitRate.percentage, '50.0%');
console.log('PASS: Cache hit rate calculation verified (50.0%)');

// Test 4: Estimated Token Savings Accumulation
console.log('Test 4: Estimated token savings accumulation...');
// 25 + 80 + 50 + 0 = 155 tokens saved
assert.strictEqual(summary2.estimatedTokenSavings.totalTokensSaved, 155);
assert.strictEqual(summary2.estimatedTokenSavings.formatted, '155');
console.log('PASS: Estimated token savings accumulation verified');

// Test 5: STRICT PRIVACY INVARIANT - No raw prompts or conversation content
console.log('Test 5: Privacy scrub guard (rejects prompt and conversation content)...');
const dirtyRecord = {
  route: 'Prompt Normalization',
  modelTier: 'local',
  cacheOutcome: 'NOT_CHECKED',
  tokensSaved: 10,
  prompt: 'Sensitive medical query from user',
  raw_prompt: 'Confidential corporate code',
  query_text: 'Secret API key sk-9999',
  content: 'Private user text',
  cookie: 'session=12345',
  session_token: 'jwt.token.here'
};

const sanitized = sanitizeActivityRecord(dirtyRecord);
assert.strictEqual(sanitized.route, 'Prompt Normalization');
assert.strictEqual(sanitized.tokensSaved, 10);
assert.strictEqual(sanitized.prompt, undefined);
assert.strictEqual(sanitized.raw_prompt, undefined);
assert.strictEqual(sanitized.query_text, undefined);
assert.strictEqual(sanitized.content, undefined);
assert.strictEqual(sanitized.cookie, undefined);
assert.strictEqual(sanitized.session_token, undefined);

// Ensure tracker strips it too
tracker1.recordActivity(dirtyRecord);
const latestInHistory = tracker1.recentActivity[0];
assert.strictEqual(latestInHistory.prompt, undefined);
assert.strictEqual(latestInHistory.raw_prompt, undefined);
assert.strictEqual(latestInHistory.query_text, undefined);
console.log('PASS: Privacy invariant verified (zero raw prompt/conversation text stored)');

// Test 6: Bounded Ring Buffer Capacity
console.log('Test 6: Bounded FIFO ring buffer capacity...');
for (let i = 0; i < 25; i++) {
  tracker1.recordActivity({
    route: `Activity ${i}`,
    modelTier: 'simple',
    cacheOutcome: 'MISS',
    tokensSaved: 5
  });
}
assert.strictEqual(tracker1.recentActivity.length, MAX_RECENT_ACTIVITY);
assert.strictEqual(tracker1.recentActivity.length, 15);
// The latest item recorded (i=24) must be at index 0
assert.strictEqual(tracker1.recentActivity[0].route, 'Activity 24');
console.log('PASS: Bounded ring buffer capped at 15 items verified');

// Test 7: Persistence and Async Recovery
console.log('Test 7: Persistence to storage and async recovery...');
(async () => {
  await tracker1.persist();

  // Create a second tracker instance pointing to same storage
  const tracker2 = new OptimizerMetricsTracker({
    storage: mockStorage1,
    userSettingsManager: settingsManager1
  });
  await tracker2.load();

  const summaryReloaded = tracker2.getMetricsSummary();
  assert.strictEqual(summaryReloaded.totalQueries, tracker1.totalQueries);
  assert.strictEqual(summaryReloaded.estimatedTokenSavings.totalTokensSaved, tracker1.totalTokensSaved);
  assert.strictEqual(summaryReloaded.recentActivity.length, 15);
  assert.strictEqual(summaryReloaded.recentActivity[0].route, 'Activity 24');
  console.log('PASS: Storage persistence and recovery verified');

  // Test 8: Reset functionality
  console.log('Test 8: Reset functionality...');
  await tracker2.reset();
  const resetSummary = tracker2.getMetricsSummary();
  assert.strictEqual(resetSummary.totalQueries, 0);
  assert.strictEqual(resetSummary.estimatedTokenSavings.totalTokensSaved, 0);
  assert.strictEqual(resetSummary.recentActivity.length, 0);
  console.log('PASS: Reset clears metrics back to zero');

  // Test 9: RoutingReasonCode enum validation
  console.log('Test 9: Verifying RoutingReasonCode enum values...');
  const { RoutingReasonCode } = metricsModule;
  assert.ok(RoutingReasonCode);
  assert.strictEqual(RoutingReasonCode.SIMPLE_TASK_SIGNAL, 'SIMPLE_TASK_SIGNAL');
  assert.strictEqual(RoutingReasonCode.CONTEXT_DEPENDENCY, 'CONTEXT_DEPENDENCY');
  assert.strictEqual(RoutingReasonCode.CACHE_HIT, 'CACHE_HIT');
  assert.strictEqual(RoutingReasonCode.ESCALATION, 'ESCALATION');
  assert.strictEqual(RoutingReasonCode.LOCAL_RULE_MATCH, 'LOCAL_RULE_MATCH');
  assert.strictEqual(RoutingReasonCode.USER_OVERRIDE, 'USER_OVERRIDE');
  assert.strictEqual(RoutingReasonCode.RICH_CONTENT_PRESERVATION, 'RICH_CONTENT_PRESERVATION');
  assert.strictEqual(RoutingReasonCode.COMPLEX_TASK_SIGNAL, 'COMPLEX_TASK_SIGNAL');
  console.log('PASS: RoutingReasonCode enum constants verified');

  // Test 10: Developer diagnostics sanitization and internal signals labeling
  console.log('Test 10: Developer diagnostics sanitization and labeling...');
  const activityWithDiagnostics = tracker2.recordActivity({
    route: 'simple-model candidate',
    modelTier: 'simple',
    cacheOutcome: 'MISS',
    tokensSaved: 15,
    latencyMs: 80,
    status: 'COMPLETED',
    diagnostics: {
      reasonCode: RoutingReasonCode.SIMPLE_TASK_SIGNAL,
      reasonExplanation: 'Single-intent inquiry with no code or reasoning markers.',
      taskCategory: 'factual question',
      signals: ['STANDALONE_QUERY', 'NO_CODE_SYNTAX'],
      internalSignals: {
        score: 0.18,
        level: 'LOW',
        confidence: 0.85,
        factorBreakdown: { length: 0.05, cues: 0.0 }
      },
      // Attempt forbidden fields inside diagnostics
      raw_prompt: 'SHOULD BE STRIPPED',
      query_text: 'SHOULD BE STRIPPED'
    }
  });

  assert.ok(activityWithDiagnostics);
  assert.ok(activityWithDiagnostics.diagnostics);
  assert.strictEqual(activityWithDiagnostics.diagnostics.reasonCode, 'SIMPLE_TASK_SIGNAL');
  assert.strictEqual(activityWithDiagnostics.diagnostics.reasonExplanation, 'Single-intent inquiry with no code or reasoning markers.');
  assert.strictEqual(activityWithDiagnostics.diagnostics.taskCategory, 'factual question');
  assert.deepStrictEqual(activityWithDiagnostics.diagnostics.signals, ['STANDALONE_QUERY', 'NO_CODE_SYNTAX']);
  
  // Verify internalSignals labeling
  const isig = activityWithDiagnostics.diagnostics.internalSignals;
  assert.ok(isig);
  assert.strictEqual(isig.score, 0.18);
  assert.strictEqual(isig.level, 'LOW');
  assert.strictEqual(isig.confidence, 0.85);
  assert.strictEqual(isig.isSignalOnly, true);
  assert.strictEqual(isig.label, 'Internal Heuristic Signal');
  assert.ok(isig.disclaimer.includes('Indicative heuristic signal only'));

  // Verify privacy scrubbing of forbidden keys
  assert.strictEqual(activityWithDiagnostics.diagnostics.raw_prompt, undefined);
  assert.strictEqual(activityWithDiagnostics.diagnostics.query_text, undefined);

  // Test escalation diagnostics record
  const activityEscalated = tracker2.recordActivity({
    route: 'strong-model candidate',
    modelTier: 'strong',
    cacheOutcome: 'MISS',
    tokensSaved: 0,
    latencyMs: 1400,
    status: 'COMPLETED',
    diagnostics: {
      reasonCode: RoutingReasonCode.ESCALATION,
      reasonExplanation: 'Small model output incomplete, escalated to strong model.',
      escalationDetails: {
        evaluatorId: 'completeness_evaluator',
        completeness: 0.42,
        detectedIssues: ['TRUNCATED_RESPONSE', 'LOW_CONFIDENCE']
      }
    }
  });

  assert.ok(activityEscalated.diagnostics);
  assert.strictEqual(activityEscalated.diagnostics.reasonCode, 'ESCALATION');
  assert.strictEqual(activityEscalated.diagnostics.escalationDetails.evaluatorId, 'completeness_evaluator');
  assert.strictEqual(activityEscalated.diagnostics.escalationDetails.completeness, 0.42);
  assert.deepStrictEqual(activityEscalated.diagnostics.escalationDetails.detectedIssues, ['TRUNCATED_RESPONSE', 'LOW_CONFIDENCE']);
  console.log('PASS: Developer diagnostics recorded and sanitized with internal signal disclaimer');

  console.log('--- ALL OPTIMIZER METRICS TESTS PASSED ---');
})();
