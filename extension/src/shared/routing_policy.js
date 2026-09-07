/**
 * Smart Query Router - Deterministic Routing Policy Engine
 * 
 * DESIGN PRINCIPLES:
 * - Deterministic, non-generative classification: zero machine learning models.
 * - Categorizes requests into coarse routes:
 *   1. 'local-eligible': Utility requests answerable locally (arithmetic, date/time, greetings).
 *   2. 'simple-model candidate': Self-contained, single-intent inquiries with no code, math, or reasoning cues.
 *   3. 'complex-model candidate': Queries with code, math notation, multi-step reasoning, comparisons, or multi-questions.
 *   4. 'needs-evaluation': Context-dependent queries or ambiguous signals requiring conversation turn analysis.
 * - Explicit and explainable: every classification provides a human-readable explanation,
 *   machine-readable reason code, matched signal list, and rule trace.
 * - Strict guardrail: NEVER routes based on word count alone.
 * - Configuration is completely decoupled from UI code.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const configModule = require('./routing_policy_config');
    let taskClassifier = null;
    try {
      taskClassifier = require('./task_classifier');
    } catch (_) {}
    let userSettings = null;
    try {
      userSettings = require('./user_settings');
    } catch (_) {}
    module.exports = factory(configModule, taskClassifier, userSettings);
  } else {
    const configModule = root.SmartQueryRouterRoutingPolicyConfig;
    const taskClassifier = root.SmartQueryRouterTaskClassifier || null;
    const userSettings = root.SmartQueryRouterUserSettings || null;
    root.SmartQueryRouterRoutingPolicy = factory(configModule, taskClassifier, userSettings);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (configModule, taskClassifierModule, userSettingsModule) {
  'use strict';

  const {
    DEFAULT_ROUTING_POLICY_CONFIG,
    createRoutingPolicyConfig,
    validateRoutingPolicyConfig
  } = configModule || {
    DEFAULT_ROUTING_POLICY_CONFIG: {},
    createRoutingPolicyConfig: (c) => c || {},
    validateRoutingPolicyConfig: () => ({ valid: true })
  };

  /**
   * Coarse routing classifications
   */
  const CoarseRoute = Object.freeze({
    LOCAL_ELIGIBLE: 'local-eligible',
    SIMPLE_MODEL_CANDIDATE: 'simple-model candidate',
    COMPLEX_MODEL_CANDIDATE: 'complex-model candidate',
    NEEDS_EVALUATION: 'needs-evaluation'
  });

  /**
   * Machine-readable reason codes
   */
  const RoutingReasonCode = Object.freeze({
    LOCAL_DETERMINISTIC_RULE_MATCH: 'LOCAL_DETERMINISTIC_RULE_MATCH',
    CONTEXT_DEPENDENCY_DETECTED: 'CONTEXT_DEPENDENCY_DETECTED',
    COMPLEX_CODE_SYNTAX: 'COMPLEX_CODE_SYNTAX',
    COMPLEX_MATH_NOTATION: 'COMPLEX_MATH_NOTATION',
    COMPLEX_REASONING_CUE: 'COMPLEX_REASONING_CUE',
    COMPLEX_COMPARISON_CUE: 'COMPLEX_COMPARISON_CUE',
    COMPLEX_ANALYSIS_TASK: 'COMPLEX_ANALYSIS_TASK',
    COMPLEX_MULTI_QUESTION: 'COMPLEX_MULTI_QUESTION',
    COMPLEX_STRUCTURED_LIST: 'COMPLEX_STRUCTURED_LIST',
    SIMPLE_DIRECT_INQUIRY: 'SIMPLE_DIRECT_INQUIRY',
    USER_OVERRIDE_PREFER_SIMPLE: 'USER_OVERRIDE_PREFER_SIMPLE',
    USER_OVERRIDE_PREFER_STRONG: 'USER_OVERRIDE_PREFER_STRONG',
    POLICY_DISABLED_FALLTHROUGH: 'POLICY_DISABLED_FALLTHROUGH',
    FALLTHROUGH_NEEDS_EVALUATION: 'FALLTHROUGH_NEEDS_EVALUATION'
  });

  class DeterministicRoutingPolicy {
    /**
     * @param {object} [configOverrides]
     * @param {object} [userSettingsManager] - Optional UserSettingsManager instance
     */
    constructor(configOverrides = {}, userSettingsManager = null) {
      this.config = createRoutingPolicyConfig(configOverrides);
      this.userSettingsManager = userSettingsManager;
    }

    /**
     * Update or reload policy configuration
     * @param {object} newConfig
     */
    updateConfig(newConfig) {
      this.config = createRoutingPolicyConfig(newConfig);
    }

    /**
     * Inspects query event and feature signals to determine coarse route.
     * 
     * @param {object} queryEvent - DetectedQueryEvent or mock object
     * @param {object} [options] - Optional classification options
     * @param {string} [options.userOverride] - User override ('automatic', 'prefer-simple', 'prefer-strong')
     * @returns {object} RoutingClassification
     */
    classify(queryEvent, options = {}) {
      const result = this._classify(queryEvent, options);
      if (result && typeof result === 'object') {
        const userOverride = (options && typeof options.userOverride === 'string' && options.userOverride.trim())
          ? options.userOverride.trim()
          : (queryEvent && typeof queryEvent.userOverride === 'string' && queryEvent.userOverride.trim()
            ? queryEvent.userOverride.trim()
            : (this.userSettingsManager && typeof this.userSettingsManager.getRoutingOverride === 'function'
              ? this.userSettingsManager.getRoutingOverride()
              : 'automatic'));
        if (!result.userOverride) {
          result.userOverride = userOverride;
        }
      }
      return result;
    }

    /**
     * Internal classification logic
     * @private
     */
    _classify(queryEvent, options = {}) {
      const trace = [];

      if (!this.config.enabled) {
        return {
          route: CoarseRoute.NEEDS_EVALUATION,
          ruleId: 'RULE_POLICY_DISABLED',
          reasonCode: RoutingReasonCode.POLICY_DISABLED_FALLTHROUGH,
          explanation: 'Routing policy is globally disabled in configuration; routed to needs-evaluation.',
          confidence: 1.0,
          matchedSignals: ['POLICY_DISABLED'],
          ruleTrace: ['RULE_POLICY_DISABLED:MATCH']
        };
      }

      if (!queryEvent || typeof queryEvent !== 'object') {
        return {
          route: CoarseRoute.NEEDS_EVALUATION,
          ruleId: 'RULE_INVALID_INPUT',
          reasonCode: RoutingReasonCode.FALLTHROUGH_NEEDS_EVALUATION,
          explanation: 'Query event is null or not an object; safely routed to needs-evaluation.',
          confidence: 0.5,
          matchedSignals: ['INVALID_QUERY_EVENT'],
          ruleTrace: ['RULE_INVALID_INPUT:MATCH']
        };
      }

      const content = queryEvent.content || {};
      const promptText = (typeof content.normalizedPrompt === 'string' && content.normalizedPrompt)
        ? content.normalizedPrompt
        : (typeof content.rawPrompt === 'string' ? content.rawPrompt : '');

      const features = queryEvent.features || {};
      const contextDep = queryEvent.contextDependency || {};
      const optDecision = (queryEvent.optimization && queryEvent.optimization.decision) || null;

      // Resolve task classification signal from task classifier
      const taskClassifier = taskClassifierModule
        ? (taskClassifierModule.defaultTaskClassifier || taskClassifierModule)
        : (typeof globalThis !== 'undefined' && globalThis.SmartQueryRouterTaskClassifier
          ? globalThis.SmartQueryRouterTaskClassifier.defaultTaskClassifier
          : null);

      let taskSignal = queryEvent.taskClassification || null;
      if (!taskSignal && taskClassifier && typeof taskClassifier.classifyTask === 'function' && promptText) {
        taskSignal = taskClassifier.classifyTask(promptText, features, contextDep);
      }
      const taskCategory = taskSignal && taskSignal.category ? taskSignal.category : 'unknown';

      // Resolve optional user routing override (defaults to 'automatic')
      let userOverride = 'automatic';
      if (options && typeof options.userOverride === 'string' && options.userOverride.trim()) {
        userOverride = options.userOverride.trim();
      } else if (queryEvent && typeof queryEvent.userOverride === 'string' && queryEvent.userOverride.trim()) {
        userOverride = queryEvent.userOverride.trim();
      } else if (this.userSettingsManager && typeof this.userSettingsManager.getRoutingOverride === 'function') {
        userOverride = this.userSettingsManager.getRoutingOverride();
      }

      // Iterate through configured rule precedence
      for (const ruleId of this.config.rulePrecedence) {
        switch (ruleId) {
          case 'RULE_LOCAL_ELIGIBLE': {
            // Check if local rule matched (e.g. arithmetic, datetime, greeting)
            const isLocalAnswer = optDecision && optDecision.outcome === 'LOCAL_ANSWER_CANDIDATE';
            if (isLocalAnswer && this.config.enabledRoutes[CoarseRoute.LOCAL_ELIGIBLE]) {
              trace.push('RULE_LOCAL_ELIGIBLE:MATCH');
              return {
                route: CoarseRoute.LOCAL_ELIGIBLE,
                ruleId: 'RULE_LOCAL_ELIGIBLE',
                reasonCode: RoutingReasonCode.LOCAL_DETERMINISTIC_RULE_MATCH,
                explanation: `Query satisfied local deterministic rule (${optDecision.ruleId}): "${optDecision.reason}". No LLM call required.`,
                confidence: typeof optDecision.confidence === 'number' ? optDecision.confidence : 1.0,
                matchedSignals: [optDecision.ruleId, 'LOCAL_ANSWER_CANDIDATE', `task:${taskCategory}`],
                taskCategory,
                taskSignal,
                userOverride,
                ruleTrace: trace
              };
            }
            trace.push('RULE_LOCAL_ELIGIBLE:PASS');

            // If user explicitly configured prefer-simple, route non-local queries to simple-model candidate
            if (userOverride === 'prefer-simple' && this.config.enabledRoutes[CoarseRoute.SIMPLE_MODEL_CANDIDATE]) {
              trace.push('RULE_USER_OVERRIDE_PREFER_SIMPLE:MATCH');
              return {
                route: CoarseRoute.SIMPLE_MODEL_CANDIDATE,
                ruleId: 'RULE_USER_OVERRIDE_PREFER_SIMPLE',
                reasonCode: RoutingReasonCode.USER_OVERRIDE_PREFER_SIMPLE,
                explanation: 'User override "prefer-simple" active; routed to simple-model candidate.',
                confidence: 1.0,
                matchedSignals: ['USER_OVERRIDE_PREFER_SIMPLE', `task:${taskCategory}`],
                taskCategory,
                taskSignal,
                userOverride,
                ruleTrace: trace
              };
            }

            // If user explicitly configured prefer-strong, route non-local queries to complex-model candidate
            if (userOverride === 'prefer-strong' && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_USER_OVERRIDE_PREFER_STRONG:MATCH');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_USER_OVERRIDE_PREFER_STRONG',
                reasonCode: RoutingReasonCode.USER_OVERRIDE_PREFER_STRONG,
                explanation: 'User override "prefer-strong" active; routed to complex-model candidate.',
                confidence: 1.0,
                matchedSignals: ['USER_OVERRIDE_PREFER_STRONG', `task:${taskCategory}`],
                taskCategory,
                taskSignal,
                userOverride,
                ruleTrace: trace
              };
            }
            break;
          }

          case 'RULE_CONTEXT_DEPENDENCY_EVALUATION': {
            // Check if prompt requires conversation history analysis
            const requiresContext = contextDep.requiresContextAnalysis === true;
            const category = contextDep.category || 'STANDALONE';
            const isAnaphoricOrContinuation = category === 'CONTINUATION' || category === 'ANAPHORIC_REFERENCE';

            if (requiresContext && isAnaphoricOrContinuation && this.config.enabledRoutes[CoarseRoute.NEEDS_EVALUATION]) {
              trace.push('RULE_CONTEXT_DEPENDENCY_EVALUATION:MATCH');
              return {
                route: CoarseRoute.NEEDS_EVALUATION,
                ruleId: 'RULE_CONTEXT_DEPENDENCY_EVALUATION',
                reasonCode: RoutingReasonCode.CONTEXT_DEPENDENCY_DETECTED,
                explanation: `Query is context-dependent (${category}): "${contextDep.reason}". Requires conversational turn evaluation.`,
                confidence: contextDep.confidence === 'HIGH' ? 0.95 : (contextDep.confidence === 'MEDIUM' ? 0.80 : 0.70),
                matchedSignals: contextDep.matchedSignals && contextDep.matchedSignals.length > 0
                  ? contextDep.matchedSignals.slice()
                  : [category],
                ruleTrace: trace
              };
            }
            trace.push('RULE_CONTEXT_DEPENDENCY_EVALUATION:PASS');
            break;
          }

          case 'RULE_COMPLEX_CODE': {
            const code = features.code || {};
            const hasExplicitCodeChars = /[(){}\[\];=:]/.test(promptText);
            const hasFunctionOrClass = /\b(?:def|class|function|async|await|return|import|export)\b/.test(promptText);
            const isCodeTask = taskCategory === 'coding' || taskCategory === 'debugging';
            const hasCode = Boolean(
              code.hasCodeFence ||
              code.hasIndentedCode ||
              code.hasCodeSyntax ||
              code.hasInlineCode ||
              isCodeTask ||
              (code.hasCodeKeywords && hasFunctionOrClass && hasExplicitCodeChars)
            );

            if (hasCode && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_CODE:MATCH');
              const signals = [];
              if (code.hasCodeFence) signals.push('hasCodeFence');
              if (code.hasInlineCode) signals.push('hasInlineCode');
              if (code.hasIndentedCode) signals.push('hasIndentedCode');
              if (code.hasCodeSyntax) signals.push('hasCodeSyntax');
              if (code.hasCodeKeywords) signals.push('hasCodeKeywords');
              if (isCodeTask) signals.push(`task:${taskCategory}`);

              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_CODE',
                reasonCode: RoutingReasonCode.COMPLEX_CODE_SYNTAX,
                explanation: `Detected programming code blocks, syntax keywords, or ${taskCategory} task intent. Recommends flagship model for technical code analysis and execution.`,
                confidence: this.config.thresholds.minCodeSyntaxConfidence || 0.90,
                matchedSignals: signals,
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_CODE:PASS');
            break;
          }

          case 'RULE_COMPLEX_MATH': {
            const math = features.math || {};
            const hasComplexMath = Boolean(math.hasLatexMath || (math.hasMathSymbols && math.symbolCount >= 2));

            if (hasComplexMath && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_MATH:MATCH');
              const signals = [];
              if (math.hasLatexMath) signals.push('hasLatexMath');
              if (math.hasMathSymbols) signals.push(`symbolCount:${math.symbolCount}`);

              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_MATH',
                reasonCode: RoutingReasonCode.COMPLEX_MATH_NOTATION,
                explanation: 'Detected mathematical formulas, operators, or LaTeX notation. Recommends complex model capable of advanced mathematical derivation.',
                confidence: this.config.thresholds.minMathConfidence || 0.85,
                matchedSignals: signals,
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_MATH:PASS');
            break;
          }

          case 'RULE_COMPLEX_REASONING_CUE': {
            const cues = features.cues || {};
            const detectedCues = Array.isArray(cues.detectedCues) ? cues.detectedCues.slice() : [];
            const isReasoningTask = taskCategory === 'reasoning';
            const hasReasoning = Boolean(cues.hasReasoningCue || isReasoningTask);

            if (hasReasoning && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_REASONING_CUE:MATCH');
              if (isReasoningTask && !detectedCues.includes('task:reasoning')) detectedCues.push('task:reasoning');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_REASONING_CUE',
                reasonCode: RoutingReasonCode.COMPLEX_REASONING_CUE,
                explanation: `Detected multi-step reasoning cues or task (${detectedCues.join(', ')}). Recommends complex model for deep explanation and analytical reasoning.`,
                confidence: this.config.thresholds.minReasoningCueConfidence || 0.85,
                matchedSignals: detectedCues.length > 0 ? detectedCues : ['hasReasoningCue'],
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_REASONING_CUE:PASS');
            break;
          }

          case 'RULE_COMPLEX_COMPARISON_CUE': {
            const cues = features.cues || {};
            const detectedCues = Array.isArray(cues.detectedCues) ? cues.detectedCues.slice() : [];
            const isComparisonTask = taskCategory === 'comparison';
            const hasComparison = Boolean(cues.hasComparisonCue || isComparisonTask);

            if (hasComparison && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_COMPARISON_CUE:MATCH');
              if (isComparisonTask && !detectedCues.includes('task:comparison')) detectedCues.push('task:comparison');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_COMPARISON_CUE',
                reasonCode: RoutingReasonCode.COMPLEX_COMPARISON_CUE,
                explanation: `Detected comparative analysis cues or task (${detectedCues.join(', ')}). Recommends complex model for nuanced trade-off evaluation.`,
                confidence: this.config.thresholds.minComparisonCueConfidence || 0.85,
                matchedSignals: detectedCues.length > 0 ? detectedCues : ['hasComparisonCue'],
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_COMPARISON_CUE:PASS');
            break;
          }

          case 'RULE_COMPLEX_ANALYSIS': {
            const isAnalysisTask = taskCategory === 'analysis';
            if (isAnalysisTask && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_ANALYSIS:MATCH');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_ANALYSIS',
                reasonCode: RoutingReasonCode.COMPLEX_ANALYSIS_TASK,
                explanation: 'Task classified as in-depth analytical examination or breakdown. Recommends complex model for nuanced analytical reasoning.',
                confidence: 0.85,
                matchedSignals: ['task:analysis'],
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_ANALYSIS:PASS');
            break;
          }

          case 'RULE_COMPLEX_MULTI_QUESTION': {
            const questions = features.questions || {};
            const isMultiQuestion = Boolean(
              this.config.thresholds.treatMultipleQuestionsAsComplex &&
              questions.hasMultipleQuestions &&
              questions.questionCount > 1
            );

            if (isMultiQuestion && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_MULTI_QUESTION:MATCH');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_MULTI_QUESTION',
                reasonCode: RoutingReasonCode.COMPLEX_MULTI_QUESTION,
                explanation: `Detected multiple questions (${questions.questionCount} questions). Recommends complex model for addressing compound inquiries.`,
                confidence: 0.80,
                matchedSignals: [`questionCount:${questions.questionCount}`],
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_MULTI_QUESTION:PASS');
            break;
          }

          case 'RULE_COMPLEX_STRUCTURED_LIST': {
            const lists = features.lists || {};
            const isStructuredList = Boolean(
              this.config.thresholds.treatStructuredListsAsComplex &&
              lists.hasList &&
              lists.totalCount >= 3
            );

            if (isStructuredList && this.config.enabledRoutes[CoarseRoute.COMPLEX_MODEL_CANDIDATE]) {
              trace.push('RULE_COMPLEX_STRUCTURED_LIST:MATCH');
              return {
                route: CoarseRoute.COMPLEX_MODEL_CANDIDATE,
                ruleId: 'RULE_COMPLEX_STRUCTURED_LIST',
                reasonCode: RoutingReasonCode.COMPLEX_STRUCTURED_LIST,
                explanation: `Detected structured multi-item list (${lists.totalCount} items, type: ${lists.listType}). Recommends complex model for structured itemization.`,
                confidence: 0.75,
                matchedSignals: [`listType:${lists.listType}`, `totalListItems:${lists.totalCount}`],
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_COMPLEX_STRUCTURED_LIST:PASS');
            break;
          }

          case 'RULE_SIMPLE_INQUIRY': {
            // Single-intent standalone query with zero complex features
            const code = features.code || {};
            const math = features.math || {};
            const cues = features.cues || {};
            const questions = features.questions || {};
            const lists = features.lists || {};

            const hasAnyCode = Boolean(code.hasCodeSyntax || code.hasCodeFence || code.hasIndentedCode || taskCategory === 'coding' || taskCategory === 'debugging');
            const hasAnyMath = Boolean(math.hasLatexMath || math.hasMathSymbols);
            const hasAnyCue = Boolean(cues.hasReasoningCue || cues.hasComparisonCue || taskCategory === 'reasoning' || taskCategory === 'comparison' || taskCategory === 'analysis');
            const hasMultipleQuestions = Boolean(questions.hasMultipleQuestions);
            const hasComplexList = Boolean(lists.hasList && lists.totalCount >= 3);
            const isContextDependent = Boolean(contextDep.requiresContextAnalysis);

            const isCleanSimple = !hasAnyCode && !hasAnyMath && !hasAnyCue &&
              !hasMultipleQuestions && !hasComplexList && !isContextDependent;

            if (isCleanSimple && this.config.enabledRoutes[CoarseRoute.SIMPLE_MODEL_CANDIDATE]) {
              trace.push('RULE_SIMPLE_INQUIRY:MATCH');
              const signals = ['STANDALONE_INQUIRY', 'NO_COMPLEX_SIGNALS'];
              if (taskCategory !== 'unknown') {
                signals.push(`task:${taskCategory}`);
              }
              return {
                route: CoarseRoute.SIMPLE_MODEL_CANDIDATE,
                ruleId: 'RULE_SIMPLE_INQUIRY',
                reasonCode: RoutingReasonCode.SIMPLE_DIRECT_INQUIRY,
                explanation: `Self-contained standalone inquiry (task: ${taskCategory}) with no code, math notation, multi-step reasoning, or conversational dependencies. Suitable for fast, cost-effective model.`,
                confidence: 0.85,
                matchedSignals: signals,
                taskCategory,
                taskSignal,
                ruleTrace: trace
              };
            }
            trace.push('RULE_SIMPLE_INQUIRY:PASS');
            break;
          }

          case 'RULE_FALLTHROUGH_EVALUATION': {
            trace.push('RULE_FALLTHROUGH_EVALUATION:MATCH');
            return {
              route: CoarseRoute.NEEDS_EVALUATION,
              ruleId: 'RULE_FALLTHROUGH_EVALUATION',
              reasonCode: RoutingReasonCode.FALLTHROUGH_NEEDS_EVALUATION,
              explanation: 'Query signals do not cleanly match local or simple rules, or contain ambiguous features. Routed to needs-evaluation for safety.',
              confidence: 0.70,
              matchedSignals: ['AMBIGUOUS_OR_UNCLASSIFIED', `task:${taskCategory}`],
              taskCategory,
              taskSignal,
              ruleTrace: trace
            };
          }
        }
      }

      // Default safe fallback if precedence array exhausted
      trace.push('DEFAULT_EXHAUSTION:FALLBACK');
      return {
        route: CoarseRoute.NEEDS_EVALUATION,
        ruleId: 'RULE_DEFAULT_FALLBACK',
        reasonCode: RoutingReasonCode.FALLTHROUGH_NEEDS_EVALUATION,
        explanation: 'Default fallback: routed to needs-evaluation.',
        confidence: 0.70,
        matchedSignals: ['DEFAULT_FALLTHROUGH', `task:${taskCategory}`],
        taskCategory,
        taskSignal,
        ruleTrace: trace
      };
    }
  }

  // Default singleton instance
  const defaultRoutingPolicy = new DeterministicRoutingPolicy();

  return {
    CoarseRoute,
    RoutingReasonCode,
    DeterministicRoutingPolicy,
    defaultRoutingPolicy
  };
});
