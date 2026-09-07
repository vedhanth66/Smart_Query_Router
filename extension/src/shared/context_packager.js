/**
 * Smart Query Router - Conservative Candidate Context Packager
 * 
 * Decides which portions of recent context are eligible to be packaged and sent
 * to the backend later, based on relevance scores, context dependency, and
 * conservative safety rules.
 * 
 * DESIGN PRINCIPLES:
 * - Conservative default: When relevance is uncertain, retain more context rather
 *   than risking an incomplete or incorrect answer (fallback to full recent window).
 * - Preservation of critical elements: Never delete or prune code blocks, cited
 *   material, explicit user constraints, or named entities solely because they look verbose.
 * - Internal candidate only: Does NOT modify what Claude receives in the browser.
 * - Chronological reconstruction: Candidate turns retain originalIndex and provide
 *   a chronological ordering to prevent conversation flow distortion.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterContextPackager = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Strategy definitions
  const PackagingStrategy = Object.freeze({
    FULL_WINDOW: 'FULL_WINDOW',
    FILTERED_RELEVANT: 'FILTERED_RELEVANT',
    NONE: 'NONE'
  });

  // Regular expressions for critical structure detection
  const CODE_FENCE_REGEX = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
  const INLINE_CODE_REGEX = /`[^`\n]+`/g;
  const BLOCKQUOTE_REGEX = /(?:^|\n)>\s*[^\n]+/g;
  const CITATION_MARKER_REGEX = /\b(?:Source|Reference|Ref|Citing|According to):\s*[^\n]+|\[(?:\d+|[a-zA-Z]+)\]/gi;
  const MARKDOWN_LINK_REGEX = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;

  // Constraint keywords in user messages that govern ongoing expectations
  const USER_CONSTRAINT_PATTERNS = [
    /\b(?:must|must not|should|should not|always|never|do not|don't)\b/i,
    /\b(?:only|ensure|strictly|require|required|mandatory)\b/i,
    /\b(?:format as|output as|respond in|write in|limit to|under \d+|at most|at least)\b/i,
    /\b(?:no more than|keep it|return only|do not include|exclude)\b/i
  ];

  // Domain terms, technology names, and acronyms (Named Entities)
  const NAMED_ENTITY_REGEX = /\b[A-Z][a-z0-9]*(?:[A-Z0-9][a-zA-Z0-9]*)+\b|\b[a-z]+[A-Z][a-zA-Z0-9]*\b|\b[A-Z]{2,}(?:[0-9]+)?\b|\b(?:PostgreSQL|Redis|Kubernetes|Docker|Linux|Python|JavaScript|TypeScript|FastAPI|React|GraphQL|OAuth\d*|JWT|REST|UUID)\b/g;

  /**
   * Extracts code blocks from text
   * @param {string} text
   * @returns {string[]}
   */
  function extractCodeBlocks(text) {
    if (typeof text !== 'string' || !text) return [];
    const matches = [];
    const fences = text.match(CODE_FENCE_REGEX);
    if (fences) matches.push(...fences);
    const inlines = text.match(INLINE_CODE_REGEX);
    if (inlines) matches.push(...inlines);
    return matches;
  }

  /**
   * Extracts cited material (quotes, citations, links) from text
   * @param {string} text
   * @returns {string[]}
   */
  function extractCitations(text) {
    if (typeof text !== 'string' || !text) return [];
    const citations = [];
    const bq = text.match(BLOCKQUOTE_REGEX);
    if (bq) citations.push(...bq.map((s) => s.trim()));
    const citeMarkers = text.match(CITATION_MARKER_REGEX);
    if (citeMarkers) citations.push(...citeMarkers.map((s) => s.trim()));
    const links = text.match(MARKDOWN_LINK_REGEX);
    if (links) citations.push(...links.map((s) => s.trim()));
    return citations;
  }

  /**
   * Extracts explicit user constraints from text
   * @param {string} text
   * @param {'user'|'assistant'} role
   * @returns {string[]}
   */
  function extractUserConstraints(text, role) {
    if (typeof text !== 'string' || !text || role !== 'user') return [];
    const lines = text.split(/\r?\n/);
    const matchedConstraints = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      for (const pattern of USER_CONSTRAINT_PATTERNS) {
        if (pattern.test(trimmed)) {
          matchedConstraints.push(trimmed);
          break;
        }
      }
    }
    return matchedConstraints;
  }

  /**
   * Extracts named entities, acronyms, and tech identifiers
   * @param {string} text
   * @returns {string[]}
   */
  function extractNamedEntities(text) {
    if (typeof text !== 'string' || !text) return [];
    const matches = text.match(NAMED_ENTITY_REGEX);
    if (!matches) return [];
    // Deduplicate and filter trivial short caps
    const unique = Array.from(new Set(matches.filter((m) => m.length >= 2)));
    return unique;
  }

  /**
   * Inspects a turn and generates preservation flags
   * @param {object} turn
   * @returns {object}
   */
  function inspectPreservationStructures(turn) {
    const content = turn.snippet || turn.text || '';
    const codeBlocks = extractCodeBlocks(content);
    const citations = extractCitations(content);
    const userConstraints = extractUserConstraints(content, turn.role);
    const namedEntities = extractNamedEntities(content);

    return {
      hasCode: codeBlocks.length > 0 || Boolean(turn.features && turn.features.hasCode),
      hasCitations: citations.length > 0,
      hasUserConstraints: userConstraints.length > 0,
      hasNamedEntities: namedEntities.length > 0,
      codeBlockCount: codeBlocks.length,
      citationCount: citations.length,
      constraintCount: userConstraints.length,
      namedEntityCount: namedEntities.length,
      detectedCodeBlocks: codeBlocks,
      detectedCitations: citations,
      detectedUserConstraints: userConstraints,
      detectedNamedEntities: namedEntities
    };
  }

  /**
   * Generates a unique package ID
   * @returns {string}
   */
  function generatePackageId() {
    const ts = Date.now();
    const rand = Math.random().toString(36).slice(2, 7);
    return `ctx_pkg_${ts}_${rand}`;
  }

  /**
   * Builds the internal candidate context package based on relevance and conservative rules.
   * 
   * @param {object} params
   * @param {string} params.queryText - Current user query
   * @param {Array<object>} params.recentTurns - Turns from turn_tracker
   * @param {object|null} [params.contextRelevance] - Relevance output from relevance_ranker
   * @param {object|null} [params.contextDependency] - Dependency output from context_detector
   * @param {object} [params.options] - Custom thresholds
   * @returns {object} CandidateContextPackage
   */
  function buildCandidateContextPackage({
    queryText = '',
    recentTurns = [],
    contextRelevance = null,
    contextDependency = null,
    options = {}
  }) {
    const now = Date.now();
    const turns = Array.isArray(recentTurns) ? recentTurns : [];
    const text = typeof queryText === 'string' ? queryText.trim() : '';

    // Threshold configurations with safe defaults
    const confidence = contextRelevance ? contextRelevance.contextConfidence : 'LOW';
    const topScore = contextRelevance ? contextRelevance.topScore : 0;
    const needsContext = contextRelevance ? contextRelevance.needsContext : false;
    const isExplicitlyDependent = Boolean(contextDependency && contextDependency.requiresContextAnalysis);

    // If there are zero turns in the buffer, return an empty package
    if (turns.length === 0) {
      return {
        packageId: generatePackageId(),
        timestamp: now,
        strategy: PackagingStrategy.NONE,
        decisionReason: 'No prior conversational turns available in local window',
        conservativeFallbackApplied: false,
        includedTurns: [],
        chronologicalTurns: [],
        excludedTurns: [],
        metrics: {
          totalAvailableTurns: 0,
          includedTurnCount: 0,
          excludedTurnCount: 0,
          totalCharacters: 0,
          preservedCodeBlockCount: 0,
          preservedCitationCount: 0,
          preservedConstraintCount: 0,
          preservedNamedEntityCount: 0
        }
      };
    }

    // Inspect all turns in window for preservation structures
    const analyzedTurns = turns.map((turn, index) => {
      const structures = inspectPreservationStructures(turn);
      const originalIndex = turn.originalIndex !== undefined ? turn.originalIndex : index;
      
      // Match candidate score if present
      let relevanceScore = 0;
      if (contextRelevance && Array.isArray(contextRelevance.rankedCandidates)) {
        const candidateMatch = contextRelevance.rankedCandidates.find((c) => c.turnId === turn.turnId);
        if (candidateMatch) {
          relevanceScore = candidateMatch.relevanceScore;
        }
      }

      return {
        turnId: turn.turnId,
        role: turn.role,
        originalIndex,
        timestamp: turn.timestamp || now,
        content: turn.snippet || turn.text || '',
        relevanceScore,
        preservationFlags: structures
      };
    });

    // 1. EVALUATE PACKAGING STRATEGY
    // Conservative rule: If relevance is uncertain, retain more context rather than risking a wrong answer.
    const isUncertain =
      confidence === 'MEDIUM' ||
      (topScore >= 0.18 && topScore < 0.55) ||
      (isExplicitlyDependent && topScore < 0.60);

    const isConfidentStandalone =
      confidence === 'LOW' &&
      !needsContext &&
      !isExplicitlyDependent &&
      topScore < 0.18;

    let strategy = PackagingStrategy.FULL_WINDOW;
    let decisionReason = '';
    let conservativeFallbackApplied = false;

    if (isConfidentStandalone) {
      // Standalone query with zero ambiguity: no context package needed
      strategy = PackagingStrategy.NONE;
      decisionReason = 'Query classified as standalone with low context dependence and negligible turn relevance';
    } else if (isUncertain) {
      // Ambiguous or medium confidence: conservative fallback keeps all available turns
      strategy = PackagingStrategy.FULL_WINDOW;
      conservativeFallbackApplied = true;
      decisionReason = `Relevance is uncertain (confidence: ${confidence}, topScore: ${topScore.toFixed(2)}); conservatively retaining all recent turns to prevent risking an incorrect answer`;
    } else if (confidence === 'HIGH') {
      // High confidence: can use filtered relevant strategy while strictly preserving critical structures
      strategy = PackagingStrategy.FILTERED_RELEVANT;
      decisionReason = `High relevance confidence (topScore: ${topScore.toFixed(2)}); retaining top matching turns and critical structural elements`;
    } else {
      // Default fallback is always conservative
      strategy = PackagingStrategy.FULL_WINDOW;
      conservativeFallbackApplied = true;
      decisionReason = 'Conservative default applied: keeping recent context to avoid information loss';
    }

    // 2. TURN ELIGIBILITY DECISION
    const includedTurns = [];
    const excludedTurns = [];

    if (strategy === PackagingStrategy.NONE) {
      // Exclude all turns with explicit reasoning
      for (const t of analyzedTurns) {
        excludedTurns.push({
          turnId: t.turnId,
          originalIndex: t.originalIndex,
          reason: 'STANDALONE_QUERY_EXEMPTION'
        });
      }
    } else if (strategy === PackagingStrategy.FULL_WINDOW) {
      // Include all turns under conservative fallback
      for (const t of analyzedTurns) {
        includedTurns.push({
          ...t,
          selectionReason: conservativeFallbackApplied
            ? 'CONSERVATIVE_UNCERTAINTY_FALLBACK'
            : 'FULL_WINDOW_RETENTION'
        });
      }
    } else if (strategy === PackagingStrategy.FILTERED_RELEVANT) {
      // Filtered relevant strategy:
      // Include:
      // a) Turns with high relevance score (>= 0.28)
      // b) Any turn with explicit user constraints (NEVER drop user constraints solely because verbose)
      // c) Any turn with code blocks or citations that match query keywords or are adjacent to top candidates
      // d) Reciprocal pair turns (user question + assistant answer pairs)
      
      const candidateTurnIds = new Set();
      const highRelevanceTurnIndexes = new Set();

      // a) Top relevance candidates
      for (const t of analyzedTurns) {
        if (t.relevanceScore >= 0.28) {
          candidateTurnIds.add(t.turnId);
          highRelevanceTurnIndexes.add(t.originalIndex);
        }
      }

      // b) Structural protection: Never delete turns with user constraints
      for (const t of analyzedTurns) {
        if (t.preservationFlags.hasUserConstraints) {
          candidateTurnIds.add(t.turnId);
        }
      }

      // c) Structural protection: Never delete turns with code blocks or citations solely for verbosity
      const queryMentionsCode = /\b(?:code|function|error|snippet|script|trace|bug|implement)\b/i.test(text);
      for (const t of analyzedTurns) {
        if (t.preservationFlags.hasCode && (queryMentionsCode || t.relevanceScore > 0.15)) {
          candidateTurnIds.add(t.turnId);
        }
        if (t.preservationFlags.hasCitations && t.relevanceScore > 0.15) {
          candidateTurnIds.add(t.turnId);
        }
      }

      // d) Reciprocal pair preservation: If an assistant turn is included, keep the preceding user turn
      for (let i = 0; i < analyzedTurns.length; i++) {
        const current = analyzedTurns[i];
        if (candidateTurnIds.has(current.turnId)) {
          if (current.role === 'assistant' && i > 0) {
            candidateTurnIds.add(analyzedTurns[i - 1].turnId);
          }
        }
      }

      // If filtering eliminated all candidates, fall back to conservative full window
      if (candidateTurnIds.size === 0) {
        for (const t of analyzedTurns) {
          includedTurns.push({
            ...t,
            selectionReason: 'CONSERVATIVE_EMPTY_FILTER_FALLBACK'
          });
        }
      } else {
        for (const t of analyzedTurns) {
          if (candidateTurnIds.has(t.turnId)) {
            let reason = 'RELEVANT_CANDIDATE';
            if (t.preservationFlags.hasUserConstraints) {
              reason = 'USER_CONSTRAINT_PRESERVATION';
            } else if (t.preservationFlags.hasCode && queryMentionsCode) {
              reason = 'CODE_BLOCK_PRESERVATION';
            } else if (t.relevanceScore >= 0.28) {
              reason = 'HIGH_RELEVANCE_MATCH';
            } else {
              reason = 'RECIPROCAL_PAIR_PRESERVATION';
            }

            includedTurns.push({
              ...t,
              selectionReason: reason
            });
          } else {
            excludedTurns.push({
              turnId: t.turnId,
              originalIndex: t.originalIndex,
              reason: 'LOW_RELEVANCE_FILTERED'
            });
          }
        }
      }
    }

    // Sort strictly in original chronological order for reconstructability
    const chronologicalTurns = includedTurns.slice().sort((a, b) => a.originalIndex - b.originalIndex);

    // Compute metrics
    let totalCharacters = 0;
    let preservedCodeBlockCount = 0;
    let preservedCitationCount = 0;
    let preservedConstraintCount = 0;
    let preservedNamedEntityCount = 0;

    for (const t of includedTurns) {
      totalCharacters += t.content.length;
      preservedCodeBlockCount += t.preservationFlags.codeBlockCount;
      preservedCitationCount += t.preservationFlags.citationCount;
      preservedConstraintCount += t.preservationFlags.constraintCount;
      preservedNamedEntityCount += t.preservationFlags.namedEntityCount;
    }

    return {
      packageId: generatePackageId(),
      timestamp: now,
      strategy,
      decisionReason,
      conservativeFallbackApplied,
      includedTurns,
      chronologicalTurns,
      excludedTurns,
      metrics: {
        totalAvailableTurns: turns.length,
        includedTurnCount: includedTurns.length,
        excludedTurnCount: excludedTurns.length,
        totalCharacters,
        preservedCodeBlockCount,
        preservedCitationCount,
        preservedConstraintCount,
        preservedNamedEntityCount
      }
    };
  }

  return {
    PackagingStrategy,
    buildCandidateContextPackage,
    extractCodeBlocks,
    extractCitations,
    extractUserConstraints,
    extractNamedEntities,
    inspectPreservationStructures
  };
});
