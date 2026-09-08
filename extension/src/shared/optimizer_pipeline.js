/**
 * Smart Query Router - Complete Optimizer Pipeline (Dry-Run Mode)
 * 
 * CORE RESPONSIBILITIES:
 * 1. Connects the end-to-end optimization pipeline:
 *    - Detection & feature extraction (normalization, cues, task category, complexity, deduplication).
 *    - Context selection (conversation turn tracking, relevance ranking, candidate packaging).
 *    - Deterministic routing (local rules engine, routing policy, user override).
 *    - Backend call & caching observation (if backend is enabled).
 *    - Performance telemetry creation (strictly sanitized, zero raw user queries).
 * 2. Assembles and records a structured ProposedActionRecord proving detection,
 *    context selection, routing, caching, and telemetry work together.
 * 3. STRICT NON-INTERFERENCE INVARIANT:
 *    - Dry-run mode strictly observes and records proposed actions in memory.
 *    - Never modifies or replaces prompt text in the contenteditable editor.
 *    - Never calls event.preventDefault() or event.stopPropagation().
 *    - Never blocks, intercepts, or alters Claude's actual query request or streaming response.
 *    - Guarantees userVisibleBehaviorAltered === false across all paths.
 * 4. Controlled by an explicit toggle that is OFF by default (userSettings.dryRunMode).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let normalizer = null;
    let featureExtractor = null;
    let taskClassifier = null;
    let complexityScorer = null;
    let relevanceRanker = null;
    let contextPackager = null;
    let telemetry = null;
    let routingPolicy = null;
    try { normalizer = require('./normalizer'); } catch (_) {}
    try { featureExtractor = require('./feature_extractor'); } catch (_) {}
    try { taskClassifier = require('./task_classifier'); } catch (_) {}
    try { complexityScorer = require('./complexity_scorer'); } catch (_) {}
    try { relevanceRanker = require('./relevance_ranker'); } catch (_) {}
    try { contextPackager = require('./context_packager'); } catch (_) {}
    try { telemetry = require('./telemetry'); } catch (_) {}
    try { routingPolicy = require('./routing_policy'); } catch (_) {}
    module.exports = factory(normalizer, featureExtractor, taskClassifier, complexityScorer, relevanceRanker, contextPackager, telemetry, routingPolicy);
  } else {
    root.SmartQueryRouterOptimizerPipeline = factory(
      root.SmartQueryRouterNormalizer,
      root.SmartQueryRouterFeatureExtractor,
      root.SmartQueryRouterTaskClassifier,
      root.SmartQueryRouterComplexityScorer,
      root.SmartQueryRouterRelevanceRanker,
      root.SmartQueryRouterContextPackager,
      root.SmartQueryRouterTelemetry,
      root.SmartQueryRouterRoutingPolicy
    );
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (
  normalizerDep,
  featureExtractorDep,
  taskClassifierDep,
  complexityScorerDep,
  relevanceRankerDep,
  contextPackagerDep,
  telemetryDep,
  routingPolicyDep
) {
  'use strict';

  /**
   * Action types proposed by the optimizer
   */
  const OptimizerActionType = Object.freeze({
    NO_OPTIMIZATION: 'NO_OPTIMIZATION',
    LOCAL_COMPLETION: 'LOCAL_COMPLETION',
    MODEL_ROUTING: 'MODEL_ROUTING',
    CONTEXT_PRUNING: 'CONTEXT_PRUNING',
    DUAL_EVALUATION: 'DUAL_EVALUATION'
  });

  /**
   * Safe ID generator for dry-run tracking
   */
  function generateId(prefix = 'dry') {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  /**
   * Bounded in-memory ring buffer for recent dry-run actions
   */
  class DryRunActionHistory {
    constructor(maxEntries = 20) {
      this.maxEntries = maxEntries;
      this.entries = [];
    }

    add(action) {
      if (!action || typeof action !== 'object') return;
      this.entries.push(action);
      if (this.entries.length > this.maxEntries) {
        this.entries.shift();
      }
    }

    getLatest() {
      return this.entries.length > 0 ? this.entries[this.entries.length - 1] : null;
    }

    getAll() {
      return [...this.entries];
    }

    clear() {
      this.entries = [];
    }

    count() {
      return this.entries.length;
    }
  }

  const defaultDryRunHistory = new DryRunActionHistory(20);

  /**
   * Executes the complete optimizer pipeline in dry-run mode.
   * 
   * @param {object} options
   * @param {string} options.promptText - Raw prompt text from editor
   * @param {string} [options.triggerSource] - 'keyboard_enter' | 'button_click' | etc.
   * @param {object} [options.safeContext] - Safe URL/conversation metadata
   * @param {object} [options.turnTracker] - RecentTurnsTracker instance
   * @param {object} [options.userSettingsManager] - UserSettingsManager instance
   * @param {object} [options.deduplicator] - QueryDeduplicator instance
   * @param {object} [options.decisionEngine] - DecisionEngine instance
   * @param {object} [options.routingPolicy] - DeterministicRoutingPolicy instance
   * @param {object} [options.backendClient] - BackendClient instance
   * @param {Function} [options.backendDispatcher] - Optional async dispatch function for service worker messaging
   * @param {object} [options.logger] - DiagnosticLogger instance
   * @param {object} [options.telemetry] - SmartQueryRouterTelemetry module
   * @param {boolean} [options.forceDryRun] - Force dry-run execution (for testing)
   * @returns {Promise<object>} Pipeline execution outcome with proposedAction
   */
  async function executeDryRunPipeline(options = {}) {
    const {
      promptText = '',
      triggerSource = 'keyboard_enter',
      safeContext = {},
      turnTracker = null,
      userSettingsManager = null,
      deduplicator = null,
      decisionEngine = null,
      routingPolicy = null,
      backendClient = null,
      backendDispatcher = null,
      logger = null,
      telemetry = null,
      forceDryRun = false
    } = options;

    const now = Date.now();

    // 0. Verify Dry-Run Mode Toggle (Strictly OFF by default)
    const isDryRunActive = forceDryRun || (userSettingsManager && typeof userSettingsManager.isDryRunMode === 'function'
      ? userSettingsManager.isDryRunMode()
      : false);

    if (!isDryRunActive) {
      return {
        executed: false,
        dryRunMode: false,
        reason: 'DRY_RUN_MODE_DISABLED',
        proposedAction: null
      };
    }

    // Resolve modules: options -> injected dependencies -> globalThis
    const normalizerModule = options.normalizer || normalizerDep || globalThis.SmartQueryRouterNormalizer || null;
    const featureExtractorModule = options.featureExtractor || featureExtractorDep || globalThis.SmartQueryRouterFeatureExtractor || null;
    const taskClassifierModule = options.taskClassifier || taskClassifierDep || globalThis.SmartQueryRouterTaskClassifier || null;
    const complexityScorerModule = options.complexityScorer || complexityScorerDep || globalThis.SmartQueryRouterComplexityScorer || null;
    const relevanceRankerModule = options.relevanceRanker || relevanceRankerDep || globalThis.SmartQueryRouterRelevanceRanker || null;
    const contextPackagerModule = options.contextPackager || contextPackagerDep || globalThis.SmartQueryRouterContextPackager || null;
    const telemetryModule = telemetry || telemetryDep || globalThis.SmartQueryRouterTelemetry || null;
    const activeRoutingPolicy = routingPolicy || (routingPolicyDep && routingPolicyDep.defaultRoutingPolicy) || (globalThis.SmartQueryRouterRoutingPolicy && globalThis.SmartQueryRouterRoutingPolicy.defaultRoutingPolicy) || null;

    // 1. Detection: Normalization & Local Feature Extraction
    let queryText = promptText.trim();
    let isNormalized = false;
    if (normalizerModule) {
      if (typeof normalizerModule.normalizeQuery === 'function') {
        const normRes = normalizerModule.normalizeQuery(promptText);
        queryText = normRes.normalizedPrompt || queryText;
        isNormalized = Boolean(normRes.isChanged);
      } else if (typeof normalizerModule.normalizeQueryText === 'function') {
        queryText = normalizerModule.normalizeQueryText(promptText);
        isNormalized = queryText !== promptText;
      }
    }

    let features = null;
    if (featureExtractorModule && typeof featureExtractorModule.extractQueryFeatures === 'function') {
      features = featureExtractorModule.extractQueryFeatures(queryText);
    } else {
      features = {
        length: {
          characterCount: queryText.length,
          wordCount: queryText.split(/\s+/).filter(Boolean).length,
          estimatedTokens: Math.ceil(queryText.length / 4)
        },
        code: { hasCodeSyntax: false },
        math: { hasMathSymbols: false },
        questions: { questionCount: 0 },
        urls: { hasUrl: false },
        cues: { hasComparisonCue: false, hasReasoningCue: false, detectedCues: [] }
      };
    }

    const charCount = (features && features.length && typeof features.length.characterCount === 'number')
      ? features.length.characterCount
      : queryText.length;
    const wordCount = (features && features.length && typeof features.length.wordCount === 'number')
      ? features.length.wordCount
      : queryText.split(/\s+/).filter(Boolean).length;

    // Task Classification
    let taskClassification = { category: 'unknown', confidence: 0.0, matchedSignals: [] };
    if (taskClassifierModule) {
      if (typeof taskClassifierModule.classifyTask === 'function') {
        taskClassification = taskClassifierModule.classifyTask(queryText, features);
      } else if (taskClassifierModule.defaultTaskClassifier && typeof taskClassifierModule.defaultTaskClassifier.classifyTask === 'function') {
        taskClassification = taskClassifierModule.defaultTaskClassifier.classifyTask(queryText, features);
      }
    }

    // Complexity Scoring
    let complexity = { score: 0.5, level: 'MEDIUM', matchedFactors: [] };
    if (complexityScorerModule) {
      if (typeof complexityScorerModule.scoreComplexity === 'function') {
        complexity = complexityScorerModule.scoreComplexity(queryText, features, null, taskClassification);
      } else if (complexityScorerModule.defaultComplexityScorer && typeof complexityScorerModule.defaultComplexityScorer.scoreComplexity === 'function') {
        complexity = complexityScorerModule.defaultComplexityScorer.scoreComplexity(queryText, features, null, taskClassification);
      }
    }

    // Bounded Deduplication Check
    let dedupAccepted = true;
    let dedupReason = 'ACCEPTED';
    if (deduplicator && typeof deduplicator.recordSubmission === 'function') {
      const dedupRes = deduplicator.recordSubmission(queryText, safeContext, now);
      dedupAccepted = dedupRes.accepted;
      dedupReason = dedupRes.reason;
    }

    // Generate Telemetry Correlation ID
    const correlationId = telemetryModule && typeof telemetryModule.generateCorrelationId === 'function'
      ? telemetryModule.generateCorrelationId()
      : generateId('corr');
    const requestId = generateId('req');

    // 2. Context Selection: Conversation Turn Tracking & Relevance Ranking
    let contextRelevance = null;
    let candidateContextPackage = null;

    if (turnTracker && typeof turnTracker.getTurnCount === 'function' && turnTracker.getTurnCount() > 0) {
      const recentTurns = turnTracker.getRecentTurns();
      if (relevanceRankerModule && typeof relevanceRankerModule.rankTurnsByRelevance === 'function') {
        contextRelevance = relevanceRankerModule.rankTurnsByRelevance(queryText, recentTurns);
      }

      if (contextPackagerModule && typeof contextPackagerModule.buildCandidateContextPackage === 'function') {
        candidateContextPackage = contextPackagerModule.buildCandidateContextPackage({
          queryText,
          recentTurns,
          contextRelevance
        });
      }
    }

    // 3. Routing: Local Rules & Deterministic Routing Policy
    const activeOverride = userSettingsManager && typeof userSettingsManager.getRoutingOverride === 'function'
      ? userSettingsManager.getRoutingOverride()
      : 'automatic';

    let localDecision = null;
    const isLocalRulesActive = userSettingsManager && typeof userSettingsManager.isOptimizationCategoryEnabled === 'function'
      ? userSettingsManager.isOptimizationCategoryEnabled('localRules')
      : true;

    if (isLocalRulesActive && decisionEngine && typeof decisionEngine.evaluate === 'function') {
      localDecision = decisionEngine.evaluate({
        rawPrompt: promptText,
        normalizedPrompt: queryText,
        features,
        taskClassification,
        complexity,
        context: safeContext
      });
    }

    let routingClassification = null;
    if (activeRoutingPolicy && typeof activeRoutingPolicy.classify === 'function') {
      routingClassification = activeRoutingPolicy.classify(
        {
          rawPrompt: promptText,
          normalizedPrompt: queryText,
          features,
          taskClassification,
          complexity,
          candidateContextPackage
        },
        { userOverride: activeOverride }
      );
    }

    // 4. Backend Call & Caching Observation (if enabled)
    const isBackendEnabled = userSettingsManager && typeof userSettingsManager.isBackendEnabled === 'function'
      ? userSettingsManager.isBackendEnabled()
      : true;

    let backendCalled = false;
    let backendStatus = 'SKIPPED';
    let backendDecision = null;
    let backendLatencyMs = null;
    let backendError = null;

    const queryPackage = {
      request_id: requestId,
      correlation_id: correlationId,
      user_override: activeOverride,
      coarse_route: routingClassification ? routingClassification.route : null,
      task_category: taskClassification ? taskClassification.category : null,
      complexity_score: complexity ? complexity.score : null,
      complexity_level: complexity ? complexity.level : null,
      query_text: queryText,
      context_candidates: (candidateContextPackage && candidateContextPackage.includedTurns)
        ? candidateContextPackage.includedTurns.slice(0, 10).map((t) => ({
            turn_id: t.turnId,
            role: t.role,
            content: t.content,
            original_index: t.originalIndex,
            relevance_score: t.relevanceScore || 0.0,
            timestamp: t.timestamp
          }))
        : [],
      local_features: features,
      client_metadata: {
        extension_version: '0.1.0',
        client_type: 'chrome_extension',
        schema_version: '1.0',
        hostname: (safeContext && safeContext.hostname)
          ? String(safeContext.hostname).replace(/[:/\\?#].*$/, '')
          : 'claude.ai'
      }
    };

    if (isBackendEnabled) {
      backendCalled = true;
      const backendStart = Date.now();
      try {
        if (typeof backendDispatcher === 'function') {
          backendDecision = await backendDispatcher(queryPackage);
          backendStatus = 'SUCCESS';
        } else if (backendClient && typeof backendClient.optimizeQuery === 'function') {
          backendDecision = await backendClient.optimizeQuery(queryPackage);
          backendStatus = backendDecision && backendDecision.failOpen ? 'FAIL_OPEN' : 'SUCCESS';
        } else {
          backendStatus = 'NO_DISPATCHER_AVAILABLE';
        }
        backendLatencyMs = Date.now() - backendStart;
      } catch (err) {
        backendLatencyMs = Date.now() - backendStart;
        backendStatus = 'FAIL_OPEN';
        backendError = err.message || 'BACKEND_CALL_FAILED';
        backendDecision = {
          request_id: requestId,
          correlation_id: correlationId,
          decision_type: 'NO_OPTIMIZATION',
          confidence: 0.0,
          reason_code: 'FAIL_OPEN_FALLBACK',
          failOpen: true,
          errorReason: backendError
        };
      }
    }

    // 5. Assemble Structured ProposedActionRecord
    const execMeta = backendDecision && backendDecision.execution_metadata ? backendDecision.execution_metadata : null;
    const cacheOutcome = (backendDecision && backendDecision.cache_outcome) || 'NOT_CHECKED';
    const semanticCacheOutcome = execMeta ? execMeta.semantic_cache_outcome : null;

    // Resolve proposed optimization action type
    let proposedActionType = OptimizerActionType.NO_OPTIMIZATION;
    if (localDecision && localDecision.outcome === 'RULE_MATCH') {
      proposedActionType = OptimizerActionType.LOCAL_COMPLETION;
    } else if (backendDecision && backendDecision.decision_type === 'MODEL_ROUTED') {
      proposedActionType = OptimizerActionType.MODEL_ROUTING;
    } else if (routingClassification && routingClassification.route && routingClassification.route.includes('candidate')) {
      proposedActionType = OptimizerActionType.MODEL_ROUTING;
    } else if (candidateContextPackage && candidateContextPackage.metadata && candidateContextPackage.metadata.prunedTurnIds && candidateContextPackage.metadata.prunedTurnIds.length > 0) {
      proposedActionType = OptimizerActionType.CONTEXT_PRUNING;
    }

    const proposedAction = {
      actionId: requestId,
      mode: 'DRY_RUN',
      timestamp: now,
      requestId,
      correlationId,
      querySummary: {
        characterCount: charCount,
        wordCount: wordCount
      },
      detection: {
        trigger: triggerSource,
        characterCount: charCount,
        wordCount: wordCount,
        taskCategory: taskClassification.category,
        taskConfidence: taskClassification.confidence,
        complexityScore: complexity.score,
        complexityLevel: complexity.level,
        features,
        dedupAccepted,
        dedupReason
      },
      contextSelection: {
        hasContext: Boolean(candidateContextPackage && candidateContextPackage.includedTurns && candidateContextPackage.includedTurns.length > 0),
        totalTurnsTracked: turnTracker && typeof turnTracker.getTurnCount === 'function' ? turnTracker.getTurnCount() : 0,
        candidateTurnsCount: candidateContextPackage && candidateContextPackage.includedTurns ? candidateContextPackage.includedTurns.length : 0,
        includedTurnIds: candidateContextPackage && candidateContextPackage.includedTurns ? candidateContextPackage.includedTurns.map(t => t.turnId) : [],
        prunedTurnIds: candidateContextPackage && candidateContextPackage.metadata && candidateContextPackage.metadata.prunedTurnIds ? candidateContextPackage.metadata.prunedTurnIds : []
      },
      routing: {
        localDecision: localDecision ? { outcome: localDecision.outcome, ruleId: localDecision.ruleId, reason: localDecision.reason } : null,
        coarseRoute: routingClassification ? routingClassification.route : (backendDecision ? backendDecision.coarse_route : null),
        modelTier: routingClassification ? routingClassification.modelTier : null,
        ruleId: routingClassification ? routingClassification.ruleId : null,
        reasonCode: routingClassification ? routingClassification.reasonCode : null,
        confidence: routingClassification ? routingClassification.confidence : 0.0,
        userOverride: activeOverride
      },
      caching: {
        cacheOutcome,
        semanticCacheOutcome,
        cacheKey: execMeta ? execMeta.cache_key : null
      },
      cacheOutcome: {
        status: cacheOutcome,
        semanticStatus: semanticCacheOutcome
      },
      backend: {
        enabled: isBackendEnabled,
        called: backendCalled,
        status: backendStatus,
        decisionType: backendDecision ? backendDecision.decision_type : null,
        modelRoute: backendDecision ? backendDecision.model_route : null,
        latencyMs: backendLatencyMs,
        failOpen: Boolean(backendDecision && backendDecision.failOpen),
        errorReason: backendError
      },
      backendResponse: {
        called: backendCalled,
        status: backendStatus
      },
      proposedOptimization: {
        actionType: proposedActionType,
        targetModel: (backendDecision && backendDecision.model_route) || (routingClassification ? routingClassification.suggestedModel : null),
        instructions: (backendDecision && backendDecision.optimization_instructions) || null,
        candidatePrunedTurns: candidateContextPackage && candidateContextPackage.metadata && candidateContextPackage.metadata.prunedTurnIds ? candidateContextPackage.metadata.prunedTurnIds : []
      },
      proposedRoute: {
        targetModel: (backendDecision && backendDecision.model_route) || (routingClassification ? routingClassification.suggestedModel : null),
        coarseRoute: routingClassification ? routingClassification.route : (backendDecision ? backendDecision.coarse_route : null)
      },
      // STRICT SAFETY GUARANTEES: Under NO circumstances does dry-run alter user-visible behavior
      safetyGuarantees: {
        appliedToUserRequest: false,
        actualClaudeRequestAltered: false,
        actualClaudeResponseAltered: false,
        userVisibleBehaviorAltered: false
      }
    };

    // 6. Record Proposed Action in In-Memory Ring Buffer
    defaultDryRunHistory.add(proposedAction);

    // 7. Telemetry Performance Record
    let telemetryRecord = null;
    const isTelemetryActive = userSettingsManager && typeof userSettingsManager.isTelemetryEnabled === 'function'
      ? userSettingsManager.isTelemetryEnabled('performanceMetrics')
      : true;

    if (isTelemetryActive && telemetryModule && typeof telemetryModule.createPerformanceRecord === 'function') {
      telemetryRecord = telemetryModule.createPerformanceRecord({
        correlationId,
        clientTimestamp: now,
        backendTimestamp: backendDecision ? backendDecision.timestamp : null,
        decisionType: backendDecision ? backendDecision.decision_type : 'NO_OPTIMIZATION',
        coarseRoute: proposedAction.routing.coarseRoute,
        modelRoute: backendDecision ? backendDecision.model_route : null,
        modelVersion: execMeta ? execMeta.model_version : null,
        cacheOutcome: cacheOutcome,
        latencyMs: backendLatencyMs || 0,
        executionLatencyMs: execMeta ? execMeta.latency_ms : null,
        errorCategory: backendError ? 'NETWORK_ERROR' : 'NONE',
        failureCategory: execMeta ? execMeta.failure_category : 'NONE',
        escalationOccurred: execMeta ? Boolean(execMeta.escalation_occurred) : false,
        escalationReason: execMeta ? execMeta.escalation_reason : null,
        localFeatures: features,
        candidateCount: proposedAction.contextSelection.candidateTurnsCount,
        versionIdentifiers: {
          extension: '0.1.0',
          server: '0.1.0',
          schema: '1.0'
        },
        options: { dryRun: true }
      });
    }

    // 8. Safe Logging (Zero raw prompt text)
    if (logger && typeof logger.info === 'function') {
      logger.info('OPTIMIZATION_DECISION', 'Dry-run proposed action recorded', {
        correlationId,
        taskCategory: proposedAction.detection.taskCategory,
        coarseRoute: proposedAction.routing.coarseRoute,
        actionType: proposedAction.proposedOptimization.actionType,
        cacheOutcome: proposedAction.caching.cacheOutcome,
        backendCalled: proposedAction.backend.called,
        userVisibleBehaviorAltered: false
      });
    }

    return {
      executed: true,
      dryRunMode: true,
      proposedAction,
      queryPackage,
      telemetryRecord
    };
  }

  return {
    OptimizerActionType,
    DryRunActionHistory,
    defaultDryRunHistory,
    executeDryRunPipeline
  };
});
