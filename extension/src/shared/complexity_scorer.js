/**
 * Smart Query Router - Initial Complexity Scorer Engine
 * 
 * Computes an indicative complexity score and categorical level using available
 * deterministic local features:
 * - Length: word count, token estimates
 * - Code presence: code fences, syntax characters, keywords
 * - List structure: ordered, unordered, mixed counts
 * - Reasoning & comparison cues: cognitive cues ('why', 'step-by-step', 'versus')
 * - Context dependency: anaphoric, continuation, elliptical markers
 * - Task type: 13 stable task categories from task classifier
 * 
 * DESIGN PRINCIPLES:
 * - Signal, NOT absolute truth: explicitly marked `isSignalOnly: true` with a human-readable explanation.
 * - Thresholds & weights are decoupled in configuration rather than scattered constants.
 * - Conservative, balanced initial defaults with generous headroom.
 * - Zero LLM inference.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let configModule = null;
    try {
      configModule = require('./complexity_scorer_config');
    } catch (_) {}
    module.exports = factory(configModule);
  } else {
    const configModule = root.SmartQueryRouterComplexityScorerConfig || null;
    root.SmartQueryRouterComplexityScorer = factory(configModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (configModule) {
  'use strict';

  const DEFAULT_CONFIG = (configModule && configModule.DEFAULT_COMPLEXITY_SCORER_CONFIG)
    ? configModule.DEFAULT_COMPLEXITY_SCORER_CONFIG
    : {
        version: '1.0.0',
        enabled: true,
        weights: { length: 0.15, code: 0.25, listStructure: 0.10, cues: 0.15, contextDependency: 0.15, taskType: 0.20 },
        thresholds: { veryLow: 0.06, low: 0.20, medium: 0.45, high: 0.70 },
        lengthThresholds: { shortWords: 20, mediumWords: 80, longWords: 200, veryLongWords: 500 },
        codeFactors: { codeFence: 1.0, codeSyntax: 0.85, indentedCode: 0.75, inlineCodeWithKeywords: 0.65, inlineCodeOnly: 0.35, keywordsOnly: 0.25 },
        listFactors: { singleType: 0.6, mixedType: 1.0, fullScoreItemCount: 5 },
        cueFactors: { singleCue: 0.6, multipleCues: 1.0 },
        contextFactors: { ANAPHORIC: 0.9, ANAPHORIC_REFERENCE: 0.9, CONTINUATION: 0.8, CONTINUATION_COMMAND: 0.8, ELLIPTICAL: 0.7, FEEDBACK: 0.6, UNKNOWN: 0.3, STANDALONE: 0.0 },
        taskFactors: {
          debugging: 1.0, coding: 0.90, analysis: 0.85, reasoning: 0.80, comparison: 0.75,
          'creative writing': 0.55, summarization: 0.50, rewriting: 0.45, translation: 0.40,
          'factual question': 0.35, unknown: 0.30, arithmetic: 0.15, greeting: 0.05
        }
      };

  /**
   * Categorical complexity levels
   */
  const ComplexityLevel = Object.freeze({
    VERY_LOW: 'VERY_LOW',
    LOW: 'LOW',
    MEDIUM: 'MEDIUM',
    HIGH: 'HIGH',
    VERY_HIGH: 'VERY_HIGH'
  });

  class ComplexityScorer {
    /**
     * @param {object} [config]
     */
    constructor(config = null) {
      this.config = config || DEFAULT_CONFIG;
    }

    /**
     * Computes an indicative complexity score and explanation from deterministic features.
     * 
     * @param {object} params
     * @param {string} [params.promptText]
     * @param {object|null} [params.localFeatures]
     * @param {object|null} [params.contextDependency]
     * @param {object|null} [params.taskClassification]
     * @returns {{
     *   score: number,
     *   level: string,
     *   confidence: number,
     *   isSignalOnly: boolean,
     *   explanation: string,
     *   breakdown: object,
     *   matchedFactors: string[]
     * }}
     */
    computeScore({ promptText = '', localFeatures = null, contextDependency = null, taskClassification = null } = {}) {
      const cfg = this.config;

      if (!cfg.enabled) {
        return {
          score: 0.0,
          level: ComplexityLevel.VERY_LOW,
          confidence: 1.0,
          isSignalOnly: true,
          explanation: 'Complexity scorer is disabled by configuration.',
          breakdown: {},
          matchedFactors: ['SCORER_DISABLED']
        };
      }

      const text = typeof promptText === 'string' ? promptText.trim() : '';
      const features = localFeatures || {};
      const contextDep = contextDependency || {};
      const task = taskClassification || {};

      const matchedFactors = [];
      const narrativeParts = [];

      // 1. Length Dimension (weight: cfg.weights.length)
      const wordCount = (features.length && typeof features.length.wordCount === 'number')
        ? features.length.wordCount
        : (text.length > 0 ? text.split(/\s+/).filter(Boolean).length : 0);

      const lengthCfg = cfg.lengthThresholds;
      let lengthFactor = 0.0;
      let lengthReason = '';

      if (wordCount === 0) {
        lengthFactor = 0.0;
        lengthReason = 'Empty prompt';
      } else if (wordCount <= lengthCfg.shortWords) {
        // [1, shortWords] -> 0.05 to 0.25
        lengthFactor = 0.05 + (wordCount / lengthCfg.shortWords) * 0.20;
        lengthReason = `Brief length (${wordCount} words)`;
      } else if (wordCount <= lengthCfg.mediumWords) {
        // (shortWords, mediumWords] -> 0.25 to 0.65
        const ratio = (wordCount - lengthCfg.shortWords) / (lengthCfg.mediumWords - lengthCfg.shortWords);
        lengthFactor = 0.25 + ratio * 0.40;
        lengthReason = `Moderate length (${wordCount} words)`;
        matchedFactors.push('LENGTH_MODERATE');
      } else if (wordCount <= lengthCfg.longWords) {
        // (mediumWords, longWords] -> 0.65 to 0.85
        const ratio = (wordCount - lengthCfg.mediumWords) / (lengthCfg.longWords - lengthCfg.mediumWords);
        lengthFactor = 0.65 + ratio * 0.20;
        lengthReason = `Substantial length (${wordCount} words)`;
        matchedFactors.push('LENGTH_SUBSTANTIAL');
        narrativeParts.push(`substantial length (${wordCount} words)`);
      } else {
        // > longWords -> 0.85 to 1.0
        const extraRatio = Math.min(1.0, (wordCount - lengthCfg.longWords) / (lengthCfg.veryLongWords - lengthCfg.longWords));
        lengthFactor = 0.85 + extraRatio * 0.15;
        lengthReason = `Very long prompt (${wordCount} words)`;
        matchedFactors.push('LENGTH_VERY_LONG');
        narrativeParts.push(`extensive length (${wordCount} words)`);
      }

      const lengthScore = lengthFactor * cfg.weights.length;

      // 2. Code Presence Dimension (weight: cfg.weights.code)
      const codeFeats = features.code || {};
      let codeFactor = 0.0;
      let codeReason = 'No code detected';

      if (codeFeats.hasCodeFence) {
        codeFactor = cfg.codeFactors.codeFence;
        codeReason = 'Fenced multi-line code block present';
        matchedFactors.push('CODE_FENCE');
        narrativeParts.push('fenced code block');
      } else if (codeFeats.hasCodeSyntax) {
        codeFactor = cfg.codeFactors.codeSyntax;
        codeReason = 'Explicit programming syntax characters present';
        matchedFactors.push('CODE_SYNTAX');
        narrativeParts.push('programming syntax');
      } else if (codeFeats.hasIndentedCode) {
        codeFactor = cfg.codeFactors.indentedCode;
        codeReason = 'Indented code lines present';
        matchedFactors.push('CODE_INDENTED');
        narrativeParts.push('indented code');
      } else if (codeFeats.hasInlineCode && codeFeats.hasCodeKeywords) {
        codeFactor = cfg.codeFactors.inlineCodeWithKeywords;
        codeReason = 'Inline code backticks with programming keywords';
        matchedFactors.push('CODE_INLINE_WITH_KEYWORDS');
        narrativeParts.push('inline code and keywords');
      } else if (codeFeats.hasInlineCode) {
        codeFactor = cfg.codeFactors.inlineCodeOnly;
        codeReason = 'Inline code snippets present';
        matchedFactors.push('CODE_INLINE_ONLY');
      } else if (codeFeats.hasCodeKeywords) {
        codeFactor = cfg.codeFactors.keywordsOnly;
        codeReason = 'Programming keywords present';
        matchedFactors.push('CODE_KEYWORDS_ONLY');
      }

      const codeScore = codeFactor * cfg.weights.code;

      // 3. List Structure Dimension (weight: cfg.weights.listStructure)
      const listFeats = features.lists || {};
      let listFactor = 0.0;
      let listReason = 'No structured lists';

      if (listFeats.hasList) {
        const baseFactor = listFeats.listType === 'MIXED'
          ? cfg.listFactors.mixedType
          : cfg.listFactors.singleType;
        const count = listFeats.totalCount || 1;
        const countRatio = Math.min(1.0, Math.max(0.5, count / cfg.listFactors.fullScoreItemCount));
        listFactor = baseFactor * countRatio;
        listReason = `${listFeats.listType} list with ${count} items`;
        matchedFactors.push(`LIST_${listFeats.listType}`);
        if (count >= 3) {
          narrativeParts.push(`structured ${listFeats.listType.toLowerCase()} list (${count} items)`);
        }
      }

      const listScore = listFactor * cfg.weights.listStructure;

      // 4. Reasoning & Comparison Cues Dimension (weight: cfg.weights.cues)
      const cueFeats = features.cues || {};
      const detectedCues = Array.isArray(cueFeats.detectedCues) ? cueFeats.detectedCues : [];
      let cueFactor = 0.0;
      let cueReason = 'No reasoning or comparison cues';

      if (detectedCues.length >= 2 || (cueFeats.hasReasoningCue && cueFeats.hasComparisonCue)) {
        cueFactor = cfg.cueFactors.multipleCues;
        cueReason = `Multiple cognitive cues detected: ${detectedCues.join(', ')}`;
        matchedFactors.push('MULTIPLE_COGNITIVE_CUES');
        narrativeParts.push(`cognitive cues (${detectedCues.slice(0, 2).join(', ')})`);
      } else if (detectedCues.length === 1 || cueFeats.hasReasoningCue || cueFeats.hasComparisonCue) {
        cueFactor = cfg.cueFactors.singleCue;
        const label = detectedCues[0] || (cueFeats.hasReasoningCue ? 'REASONING' : 'COMPARISON');
        cueReason = `Cognitive cue detected: ${label}`;
        matchedFactors.push(`CUE_${label}`);
        narrativeParts.push(`cognitive cue (${label.toLowerCase()})`);
      }

      const cueScore = cueFactor * cfg.weights.cues;

      // 5. Context Dependency Dimension (weight: cfg.weights.contextDependency)
      const contextCat = contextDep.category || 'STANDALONE';
      let contextFactor = 0.0;
      let contextReason = 'Standalone query';

      if (cfg.contextFactors[contextCat] !== undefined) {
        contextFactor = cfg.contextFactors[contextCat];
        contextReason = `Context category: ${contextCat}`;
      } else if (contextCat.startsWith('ANAPHORIC') && cfg.contextFactors.ANAPHORIC !== undefined) {
        contextFactor = cfg.contextFactors.ANAPHORIC;
        contextReason = `Context category: ${contextCat}`;
      } else if (contextCat.startsWith('CONTINUATION') && cfg.contextFactors.CONTINUATION !== undefined) {
        contextFactor = cfg.contextFactors.CONTINUATION;
        contextReason = `Context category: ${contextCat}`;
      } else if (contextDep.requiresContextAnalysis) {
        contextFactor = 0.7;
        contextReason = 'Context-dependent inquiry';
      }

      if (contextFactor > 0.4) {
        matchedFactors.push(`CONTEXT_${contextCat}`);
        narrativeParts.push(`context dependency (${contextCat.toLowerCase()})`);
      }

      const contextScore = contextFactor * cfg.weights.contextDependency;

      // 6. Task Type Dimension (weight: cfg.weights.taskType)
      const taskCat = (typeof task.category === 'string' && task.category.trim())
        ? task.category.toLowerCase().trim()
        : 'unknown';

      let taskFactor = cfg.taskFactors[taskCat] !== undefined
        ? cfg.taskFactors[taskCat]
        : 0.30;
      let taskReason = `Task classified as ${taskCat}`;

      if (taskFactor >= 0.70) {
        matchedFactors.push(`TASK_${taskCat.toUpperCase().replace(/\s+/g, '_')}`);
        narrativeParts.push(`${taskCat} task intent`);
      }

      const taskScore = taskFactor * cfg.weights.taskType;

      // Total Score Aggregation (bounded in [0.0, 1.0])
      const rawTotal = lengthScore + codeScore + listScore + cueScore + contextScore + taskScore;
      const finalScore = Math.min(1.0, Math.max(0.0, Math.round(rawTotal * 100) / 100));

      // Map to categorical ComplexityLevel
      let level = ComplexityLevel.MEDIUM;
      if (finalScore < cfg.thresholds.veryLow) {
        level = ComplexityLevel.VERY_LOW;
      } else if (finalScore < cfg.thresholds.low) {
        level = ComplexityLevel.LOW;
      } else if (finalScore < cfg.thresholds.medium) {
        level = ComplexityLevel.MEDIUM;
      } else if (finalScore < cfg.thresholds.high) {
        level = ComplexityLevel.HIGH;
      } else {
        level = ComplexityLevel.VERY_HIGH;
      }

      // Synthesize Human-Readable Explanation
      let explanation = '';
      const levelDescriptor = level.replace('_', ' ').toLowerCase();
      if (narrativeParts.length > 0) {
        explanation = `${levelDescriptor.charAt(0).toUpperCase() + levelDescriptor.slice(1)} complexity (${finalScore.toFixed(2)}): characterized by ${narrativeParts.join(', ')}.`;
      } else {
        explanation = `${levelDescriptor.charAt(0).toUpperCase() + levelDescriptor.slice(1)} complexity (${finalScore.toFixed(2)}): ${lengthReason.toLowerCase()} without complex code or cognitive cues.`;
      }

      // Confidence in heuristic assessment (higher if features & task signal are populated)
      let confidence = 0.85;
      if (features.length && task.category && contextDep.category) {
        confidence = 0.92;
      }

      return {
        score: finalScore,
        level,
        confidence,
        isSignalOnly: true,
        explanation,
        breakdown: {
          length: {
            weight: cfg.weights.length,
            factor: Math.round(lengthFactor * 100) / 100,
            contribution: Math.round(lengthScore * 100) / 100,
            reason: lengthReason
          },
          code: {
            weight: cfg.weights.code,
            factor: Math.round(codeFactor * 100) / 100,
            contribution: Math.round(codeScore * 100) / 100,
            reason: codeReason
          },
          listStructure: {
            weight: cfg.weights.listStructure,
            factor: Math.round(listFactor * 100) / 100,
            contribution: Math.round(listScore * 100) / 100,
            reason: listReason
          },
          cues: {
            weight: cfg.weights.cues,
            factor: Math.round(cueFactor * 100) / 100,
            contribution: Math.round(cueScore * 100) / 100,
            reason: cueReason
          },
          contextDependency: {
            weight: cfg.weights.contextDependency,
            factor: Math.round(contextFactor * 100) / 100,
            contribution: Math.round(contextScore * 100) / 100,
            reason: contextReason
          },
          taskType: {
            weight: cfg.weights.taskType,
            factor: Math.round(taskFactor * 100) / 100,
            contribution: Math.round(taskScore * 100) / 100,
            reason: taskReason
          }
        },
        matchedFactors: matchedFactors.length > 0 ? matchedFactors : ['BASELINE_QUERY']
      };
    }
  }

  const defaultComplexityScorer = new ComplexityScorer();

  return {
    ComplexityLevel,
    ComplexityScorer,
    defaultComplexityScorer
  };
});
