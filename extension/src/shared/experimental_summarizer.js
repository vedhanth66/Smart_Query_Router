/**
 * Smart Query Router - Experimental Local Context Summarizer & Comparative Metrics
 * 
 * DESIGN CONSTRAINTS & ISOLATION GUARANTEES:
 * - Disabled by default: Strict feature gating ensures this module NEVER runs in production mode.
 * - Strict production lock: Even if `enabled: true` is passed, refuses execution if `environment === 'production'`.
 * - No on-device LLM required: Uses deterministic, structural/extractive summarization.
 * - Non-intrusive comparison: Benchmarks direct relevant-turn selection vs. local summarization.
 * - Zero conversation leakage in telemetry: Telemetry metrics are strictly numeric and categorical.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterExperimentalSummarizer = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Default configuration: Strictly disabled and locked to production by default
  const DEFAULT_CONFIG = Object.freeze({
    enabled: false,
    environment: 'production' // 'production' | 'test' | 'experimental'
  });

  /**
   * Evaluates if experimental summarization is permitted.
   * STRICT SAFETY RULE: Must NEVER run in production mode by accident.
   * 
   * @param {object} config
   * @returns {boolean}
   */
  function isExperimentalSummarizationAllowed(config = {}) {
    const enabled = config.enabled === true;
    const env = (config.environment || 'production').toLowerCase();
    
    // Explicit production lock
    if (env === 'production') {
      return false;
    }

    return enabled;
  }

  // Regex patterns for extractive structural summarization
  const CODE_SIGNATURE_REGEX = /(?:def|class|function|async\s+function|const|let|var|type|interface)\s+([A-Za-z0-9_]+)\s*(?:\([^)]*\)|[={])/g;
  const FIRST_SENTENCE_REGEX = /^([^.\n?!]+[.?!])/;
  const CONSTRAINT_LINE_REGEX = /\b(?:must|must not|should|should not|always|never|do not|don't|only|strictly|format as|output as|limit to|under \d+)\b/i;
  const NAMED_ENTITY_REGEX = /\b[A-Z][a-z0-9]*(?:[A-Z0-9][a-zA-Z0-9]*)+\b|\b[a-z]+[A-Z][a-zA-Z0-9]*\b|\b[A-Z]{2,}(?:[0-9]+)?\b|\b(?:PostgreSQL|Redis|Kubernetes|Docker|Linux|Python|JavaScript|TypeScript|FastAPI|React|GraphQL|OAuth\d*|JWT|REST|UUID)\b/g;

  /**
   * Extracts code signatures from turn content
   * @param {string} text
   * @returns {string[]}
   */
  function extractCodeSignatures(text) {
    if (typeof text !== 'string') return [];
    const signatures = [];
    let match;
    const regex = new RegExp(CODE_SIGNATURE_REGEX.source, 'g');
    while ((match = regex.exec(text)) !== null) {
      signatures.push(match[0].trim());
    }
    return signatures;
  }

  /**
   * Deterministic, non-generative extractive summarization of conversational turns.
   * Does NOT require an on-device model.
   * 
   * @param {Array<object>} turns - Candidate turns from direct selection
   * @returns {object} ExtractiveSummaryPackage
   */
  function generateExtractiveSummary(turns) {
    const turnList = Array.isArray(turns) ? turns : [];
    if (turnList.length === 0) {
      return {
        summaryType: 'EMPTY_SUMMARY',
        topicEntities: [],
        activeConstraints: [],
        codeSignatures: [],
        turnKeyPoints: [],
        summaryContent: '',
        characterCount: 0,
        estimatedTokens: 0
      };
    }

    const topicEntities = new Set();
    const activeConstraints = [];
    const codeSignatures = [];
    const turnKeyPoints = [];

    for (const turn of turnList) {
      const content = turn.content || turn.snippet || turn.text || '';
      const role = turn.role || 'user';
      const originalIndex = turn.originalIndex !== undefined ? turn.originalIndex : 0;

      // 1. Extract named entities
      const entities = content.match(NAMED_ENTITY_REGEX) || [];
      for (const ent of entities) {
        if (ent.length >= 2) topicEntities.add(ent);
      }

      // 2. Extract active user constraints
      if (role === 'user') {
        const clauses = content.split(/(?<=[.?!;])\s+|\r?\n/);
        for (const clause of clauses) {
          const trimmed = clause.trim();
          if (CONSTRAINT_LINE_REGEX.test(trimmed)) {
            activeConstraints.push(trimmed.slice(0, 80));
          }
        }
      }

      // 3. Extract code signatures
      const sigs = extractCodeSignatures(content);
      for (const sig of sigs) {
        codeSignatures.push(sig);
      }

      // 4. Extract first salient sentence as key point
      const firstSentenceMatch = content.match(FIRST_SENTENCE_REGEX);
      const salientSentence = firstSentenceMatch
        ? firstSentenceMatch[1].trim()
        : content.slice(0, 60).trim();

      turnKeyPoints.push({
        role,
        originalIndex,
        point: salientSentence
      });
    }

    // Build synthesized compact summary representation
    const entityList = Array.from(topicEntities).slice(0, 6);
    const uniqueConstraints = Array.from(new Set(activeConstraints)).slice(0, 3);
    const uniqueSigs = Array.from(new Set(codeSignatures)).slice(0, 3);
    const summarySections = [];

    if (entityList.length > 0) {
      summarySections.push(`Topics: ${entityList.join(', ')}`);
    }
    if (uniqueConstraints.length > 0) {
      summarySections.push(`Constraints: ${uniqueConstraints.join('; ')}`);
    }
    if (uniqueSigs.length > 0) {
      summarySections.push(`Code: ${uniqueSigs.join(', ')}`);
    }

    const dialogueFlow = turnKeyPoints
      .map((kp) => `[${kp.role === 'assistant' ? 'Asst' : 'User'}]: ${kp.point.slice(0, 60)}`)
      .join(' | ');

    if (dialogueFlow) {
      summarySections.push(`Flow: ${dialogueFlow}`);
    }

    const summaryContent = summarySections.join('\n');
    const characterCount = summaryContent.length;
    const estimatedTokens = Math.ceil(characterCount / 4);

    return {
      summaryType: 'NON_GENERATIVE_EXTRACTIVE_SUMMARY',
      topicEntities: entityList,
      activeConstraints,
      codeSignatures,
      turnKeyPoints,
      summaryContent,
      characterCount,
      estimatedTokens
    };
  }

  /**
   * Compares direct relevant-turn selection against the experimental summarized context.
   * 
   * @param {object} directCandidatePackage - Output from context_packager
   * @param {object} summaryPackage - Output from generateExtractiveSummary
   * @param {number} [latencyMs] - Processing latency in milliseconds
   * @returns {object} ComparisonResult
   */
  function compareContextStrategies(directCandidatePackage, summaryPackage, latencyMs = 0) {
    const directTurns = directCandidatePackage && Array.isArray(directCandidatePackage.includedTurns)
      ? directCandidatePackage.includedTurns
      : [];

    let directTotalCharacters = 0;
    let directCodeCount = 0;
    let directCitationCount = 0;
    let directConstraintCount = 0;
    const directEntities = new Set();

    for (const t of directTurns) {
      const content = t.content || t.snippet || '';
      directTotalCharacters += content.length;

      if (t.preservationFlags) {
        directCodeCount += t.preservationFlags.codeBlockCount || 0;
        directCitationCount += t.preservationFlags.citationCount || 0;
        directConstraintCount += t.preservationFlags.constraintCount || 0;
      }

      const ents = content.match(NAMED_ENTITY_REGEX) || [];
      for (const e of ents) directEntities.add(e);
    }

    const directEstimatedTokens = Math.ceil(directTotalCharacters / 4);

    const summaryTotalCharacters = summaryPackage ? summaryPackage.characterCount : 0;
    const summaryEstimatedTokens = summaryPackage ? summaryPackage.estimatedTokens : 0;
    const summaryEntityCount = summaryPackage && Array.isArray(summaryPackage.topicEntities)
      ? summaryPackage.topicEntities.length
      : 0;
    const summaryConstraintCount = summaryPackage && Array.isArray(summaryPackage.activeConstraints)
      ? summaryPackage.activeConstraints.length
      : 0;
    const summaryCodeCount = summaryPackage && Array.isArray(summaryPackage.codeSignatures)
      ? summaryPackage.codeSignatures.length
      : 0;

    // Size comparison metrics
    const characterSavings = Math.max(0, directTotalCharacters - summaryTotalCharacters);
    const characterReductionRatio = directTotalCharacters > 0
      ? Number((characterSavings / directTotalCharacters).toFixed(3))
      : 0.0;
    const estimatedTokenSavings = Math.max(0, directEstimatedTokens - summaryEstimatedTokens);

    // Downstream fidelity retention metrics
    const entityRetentionRate = directEntities.size > 0
      ? Math.min(1.0, Number((summaryEntityCount / directEntities.size).toFixed(2)))
      : 1.0;

    const constraintRetentionRate = directConstraintCount > 0
      ? Math.min(1.0, Number((summaryConstraintCount / directConstraintCount).toFixed(2)))
      : 1.0;

    const codeRetentionRate = directCodeCount > 0
      ? Math.min(1.0, Number((summaryCodeCount / directCodeCount).toFixed(2)))
      : 1.0;

    return {
      directTotalCharacters,
      directEstimatedTokens,
      directTurnCount: directTurns.length,
      summaryTotalCharacters,
      summaryEstimatedTokens,
      characterSavings,
      characterReductionRatio,
      estimatedTokenSavings,
      entityRetentionRate,
      constraintRetentionRate,
      codeRetentionRate,
      latencyMs: Number(latencyMs.toFixed(2))
    };
  }

  /**
   * Formats comparison metrics into a strictly privacy-preserving telemetry payload.
   * GUARANTEE: NEVER exposes raw conversational text, prompt queries, or entity strings.
   * 
   * @param {object} comparisonResult - Output from compareContextStrategies
   * @returns {object} SafeTelemetryPayload
   */
  function toSafeComparisonTelemetry(comparisonResult) {
    if (!comparisonResult || typeof comparisonResult !== 'object') {
      return { metric_type: 'context_compression_comparison', valid: false };
    }

    return {
      metric_type: 'context_compression_comparison',
      valid: true,
      strategy_direct_chars: comparisonResult.directTotalCharacters,
      strategy_direct_tokens_est: comparisonResult.directEstimatedTokens,
      strategy_direct_turns: comparisonResult.directTurnCount,
      strategy_summary_chars: comparisonResult.summaryTotalCharacters,
      strategy_summary_tokens_est: comparisonResult.summaryEstimatedTokens,
      reduction_ratio: comparisonResult.characterReductionRatio,
      token_savings_est: comparisonResult.estimatedTokenSavings,
      entity_retention_rate: comparisonResult.entityRetentionRate,
      constraint_retention_rate: comparisonResult.constraintRetentionRate,
      code_retention_rate: comparisonResult.codeRetentionRate,
      latency_ms: comparisonResult.latencyMs
    };
  }

  /**
   * Main entry point for the experimental summarization evaluation.
   * 
   * @param {object} params
   * @param {object} params.directCandidatePackage - From context_packager
   * @param {object} [params.config] - Configuration overrides (strictly gated)
   * @returns {object|null} Evaluation result if permitted, or null if disabled
   */
  function evaluateExperimentalPath({ directCandidatePackage, config = DEFAULT_CONFIG }) {
    // 1. Strict safety guard: check permission
    if (!isExperimentalSummarizationAllowed(config)) {
      return null;
    }

    const startTime = performance.now ? performance.now() : Date.now();

    const turns = directCandidatePackage && Array.isArray(directCandidatePackage.includedTurns)
      ? directCandidatePackage.includedTurns
      : [];

    // 2. Generate non-generative extractive summary
    const summaryPackage = generateExtractiveSummary(turns);

    const endTime = performance.now ? performance.now() : Date.now();
    const latencyMs = endTime - startTime;

    // 3. Compute comparative metrics
    const comparison = compareContextStrategies(directCandidatePackage, summaryPackage, latencyMs);

    // 4. Generate safe telemetry
    const telemetry = toSafeComparisonTelemetry(comparison);

    return {
      summaryPackage,
      comparison,
      telemetry
    };
  }

  return {
    DEFAULT_CONFIG,
    isExperimentalSummarizationAllowed,
    generateExtractiveSummary,
    compareContextStrategies,
    toSafeComparisonTelemetry,
    evaluateExperimentalPath
  };
});
