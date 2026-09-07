/**
 * Smart Query Router - Non-Generative Conversation Turn Relevance Ranker
 * 
 * Ranks recent conversation turns by simple, deterministic relevance signals:
 * - Lexical overlap (shared content words / keyword match)
 * - Explicit conversational references ("the above", "previous", "you said", anaphoric pronouns)
 * - Recency weighting (linear / exponential decay)
 * - Structural cues (code matching, mathematical expressions, question-response pairing)
 * 
 * DESIGN PRINCIPLES:
 * - Zero LLM dependencies: fast, synchronous, deterministic scoring.
 * - Small ranked candidate set: bounded to top K relevant candidates.
 * - Confidence estimation: outputs whether more context is needed.
 * - Chronological reconstruction: retains original turn indexes and provides
 *   helpers to reconstruct turns in their original conversation order.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterRelevanceRanker = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_MAX_CANDIDATES = 3;

  // Common English stop words to filter out before lexical comparison
  const STOP_WORDS = new Set([
    'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and', 'any',
    'are', 'as', 'at', 'be', 'because', 'been', 'before', 'being', 'below', 'between',
    'both', 'but', 'by', 'can', 'did', 'do', 'does', 'doing', 'down', 'during', 'each',
    'few', 'for', 'from', 'further', 'had', 'has', 'have', 'having', 'he', 'her', 'here',
    'hers', 'herself', 'him', 'himself', 'his', 'how', 'i', 'if', 'in', 'into', 'is',
    'it', 'its', 'itself', 'just', 'me', 'more', 'most', 'my', 'myself', 'no', 'nor',
    'not', 'now', 'of', 'off', 'on', 'once', 'only', 'or', 'other', 'our', 'ours',
    'ourselves', 'out', 'over', 'own', 's', 'same', 'she', 'should', 'so', 'some', 'such',
    't', 'than', 'that', 'the', 'their', 'theirs', 'them', 'themselves', 'then', 'there',
    'these', 'they', 'this', 'those', 'through', 'to', 'too', 'under', 'until', 'up',
    'very', 'was', 'we', 'were', 'what', 'when', 'where', 'which', 'while', 'who', 'whom',
    'why', 'will', 'with', 'you', 'your', 'yours', 'yourself', 'yourselves', 'please'
  ]);

  /**
   * Tokenizes text into meaningful content words for lexical overlap
   * @param {string} text
   * @returns {Set<string>}
   */
  function extractContentWords(text) {
    if (typeof text !== 'string') return new Set();
    const words = text
      .toLowerCase()
      .replace(/[^\w\s]/g, ' ')
      .split(/\s+/)
      .filter((w) => w.length > 2 && !STOP_WORDS.has(w));
    return new Set(words);
  }

  /**
   * Checks equality or shared prefix for words >= 4 chars
   * @param {string} w1
   * @param {string} w2
   * @returns {boolean}
   */
  function wordsMatch(w1, w2) {
    if (w1 === w2) return true;
    if (w1.length >= 4 && w2.length >= 4) {
      if (w1.startsWith(w2) || w2.startsWith(w1)) return true;
    }
    return false;
  }

  /**
   * Computes lexical overlap score between query words and turn words
   * @param {Set<string>} queryWords
   * @param {Set<string>} turnWords
   * @returns {{ score: number, matchedWords: string[] }}
   */
  function computeLexicalOverlap(queryWords, turnWords) {
    if (queryWords.size === 0 || turnWords.size === 0) {
      return { score: 0, matchedWords: [] };
    }

    const matchedWords = [];
    for (const qWord of queryWords) {
      for (const tWord of turnWords) {
        if (wordsMatch(qWord, tWord)) {
          matchedWords.push(qWord);
          break;
        }
      }
    }

    // Jaccard-like ratio biased towards query coverage
    const coverage = matchedWords.length / queryWords.size;
    const unionSize = new Set([...queryWords, ...turnWords]).size;
    const jaccard = unionSize > 0 ? matchedWords.length / unionSize : 0;

    // Weighted combination of coverage and jaccard
    const score = Number((coverage * 0.75 + jaccard * 0.25).toFixed(3));
    return { score: Math.min(1.0, score), matchedWords };
  }

  /**
   * Computes explicit conversational reference score
   * @param {string} queryText
   * @param {number} turnIndex
   * @param {number} totalTurns
   * @param {'user'|'assistant'} role
   * @returns {number} Score between 0.0 and 1.0
   */
  function computeExplicitReferenceScore(queryText, turnIndex, totalTurns, role) {
    const q = queryText.toLowerCase();
    const isMostRecent = turnIndex === totalTurns - 1;
    const isSecondMostRecent = turnIndex === totalTurns - 2;

    // Direct reference to previous/above turns
    if (/\b(?:the\s+above|mentioned\s+above|from\s+above|as\s+above)\b/.test(q)) {
      if (isMostRecent) return 1.0;
      if (isSecondMostRecent) return 0.6;
      return 0.2;
    }

    if (/\b(?:previous|earlier|last)\s+(?:response|answer|message|code|output)\b/.test(q)) {
      if (role === 'assistant' && isMostRecent) return 1.0;
      if (role === 'assistant') return 0.8;
      return 0.3;
    }

    if (/\b(?:you\s+(?:said|mentioned|wrote|suggested))\b/.test(q)) {
      if (role === 'assistant') return isMostRecent ? 1.0 : 0.75;
      return 0.1;
    }

    // Anaphoric pronoun targets ("fix it", "rewrite that", "why did it fail")
    if (/\b(?:fix|rewrite|debug|optimize|explain|run|make)\s+(?:it|that|this)\b/.test(q) ||
        /\bwhy\s+did\s+(?:it|that|this)\s+(?:fail|break|error)\b/.test(q)) {
      if (isMostRecent) return 0.9;
      if (isSecondMostRecent) return 0.5;
      return 0.1;
    }

    // Continuation cues
    if (/^(?:continue|go on|tell me more|next step|more)[.!?]*$/.test(q.trim())) {
      if (isMostRecent) return 1.0;
      if (isSecondMostRecent) return 0.4;
      return 0.1;
    }

    return 0.0;
  }

  /**
   * Computes recency score based on position in turns array
   * @param {number} turnIndex
   * @param {number} totalTurns
   * @returns {number}
   */
  function computeRecencyScore(turnIndex, totalTurns) {
    if (totalTurns <= 1) return 1.0;
    // Normalized distance from newest (newest = 1.0, oldest decay)
    const distance = totalTurns - 1 - turnIndex;
    return Math.max(0.1, Number((1.0 - (distance * 0.25)).toFixed(2)));
  }

  /**
   * Computes structural cue match score
   * @param {string} queryText
   * @param {object} turn
   * @returns {number}
   */
  function computeStructuralScore(queryText, turn) {
    let score = 0;
    const q = queryText.toLowerCase();

    // 1. Code match
    const queryHasCode = /```|~~~|`[^`\n]+`|\b(?:function|code|def|class|error|script|trace)\b/.test(q);
    const turnHasCode = turn.features && turn.features.hasCode;
    if (queryHasCode && turnHasCode) {
      score += 0.5;
    }

    // 2. Math match
    const queryHasMath = /[+\-*/=^%×÷≤≥∑∫√π]/.test(queryText) || /\b(?:calculate|equation|formula|math)\b/.test(q);
    const turnHasMath = turn.features && turn.features.hasMath;
    if (queryHasMath && turnHasMath) {
      score += 0.3;
    }

    // 3. Question-Response complementarity
    const queryHasQuestion = q.includes('?');
    const turnHasQuestion = turn.features && turn.features.hasQuestions;
    if (queryHasQuestion && turn.role === 'assistant') {
      score += 0.2;
    } else if (!queryHasQuestion && turnHasQuestion && turn.role === 'user') {
      score += 0.2;
    }

    return Math.min(1.0, score);
  }

  /**
   * Evaluates overall confidence that more context is needed
   * @param {Array<object>} scoredCandidates
   * @param {object|null} contextDependency
   * @returns {{ confidence: 'HIGH' | 'MEDIUM' | 'LOW', needsContext: boolean }}
   */
  function evaluateContextConfidence(scoredCandidates, contextDependency) {
    const isExplicitlyDependent = contextDependency && contextDependency.requiresContextAnalysis;
    const topScore = scoredCandidates.length > 0 ? scoredCandidates[0].relevanceScore : 0;

    if (isExplicitlyDependent) {
      if (topScore >= 0.4) {
        return { confidence: 'HIGH', needsContext: true };
      }
      return { confidence: 'MEDIUM', needsContext: true };
    }

    if (topScore >= 0.6) {
      return { confidence: 'HIGH', needsContext: true };
    }

    if (topScore >= 0.35) {
      return { confidence: 'MEDIUM', needsContext: true };
    }

    return { confidence: 'LOW', needsContext: false };
  }

  /**
   * Reconstructs candidate turns into their original chronological order
   * @param {Array<object>} candidateTurns
   * @returns {Array<object>}
   */
  function reconstructChronologicalOrder(candidateTurns) {
    if (!Array.isArray(candidateTurns)) return [];
    return candidateTurns.slice().sort((a, b) => a.originalIndex - b.originalIndex);
  }

  /**
   * Main entry point: Ranks recent conversation turns by relevance to the query.
   * 
   * @param {string} queryText - The current user query
   * @param {Array<object>} turns - Array of turn objects from RecentTurnsTracker
   * @param {object} [options]
   * @param {object|null} [options.contextDependency] - Output from context_detector
   * @param {number} [options.maxCandidates] - Maximum candidate turns to return (default: 3)
   * @param {object} [options.weights] - Custom scoring weights
   * @returns {{
   *   rankedCandidates: Array<object>,
   *   reconstructedOrder: Array<object>,
   *   contextConfidence: 'HIGH' | 'MEDIUM' | 'LOW',
   *   needsContext: boolean,
   *   topScore: number
   * }}
   */
  function rankTurnsByRelevance(queryText, turns, options = {}) {
    const text = typeof queryText === 'string' ? queryText : '';
    const turnList = Array.isArray(turns) ? turns : [];
    const maxCandidates = options.maxCandidates !== undefined ? options.maxCandidates : DEFAULT_MAX_CANDIDATES;
    const contextDep = options.contextDependency || null;

    if (turnList.length === 0 || !text.trim()) {
      return {
        rankedCandidates: [],
        reconstructedOrder: [],
        contextConfidence: 'LOW',
        needsContext: false,
        topScore: 0
      };
    }

    const queryWords = extractContentWords(text);
    const totalTurns = turnList.length;

    // Weights for composite relevance
    const weights = Object.assign(
      { lexical: 0.40, explicitRef: 0.35, recency: 0.15, structural: 0.10 },
      options.weights || {}
    );

    const scoredTurns = [];

    for (let i = 0; i < totalTurns; i++) {
      const turn = turnList[i];
      const turnWords = extractContentWords(turn.snippet || '');

      const lexical = computeLexicalOverlap(queryWords, turnWords);
      const explicitRef = computeExplicitReferenceScore(text, i, totalTurns, turn.role);
      const rawRecency = computeRecencyScore(i, totalTurns);
      const structural = computeStructuralScore(text, turn);

      // Recency acts as a booster for turns with substantive topical/referential signals;
      // an entirely unrelated turn cannot score high on recency alone.
      const hasSubstantiveSignal = lexical.score > 0 || explicitRef > 0;
      const recency = hasSubstantiveSignal ? rawRecency : (rawRecency * 0.1);

      const compositeScore = Number(
        (
          lexical.score * weights.lexical +
          explicitRef * weights.explicitRef +
          recency * weights.recency +
          structural * weights.structural
        ).toFixed(3)
      );

      scoredTurns.push({
        turnId: turn.turnId,
        role: turn.role,
        originalIndex: i, // Preserved for chronological reconstruction
        timestamp: turn.timestamp,
        snippet: turn.snippet,
        characterCount: turn.characterCount,
        truncated: turn.truncated,
        relevanceScore: Math.min(1.0, compositeScore),
        signals: {
          lexicalOverlap: lexical.score,
          explicitReference: explicitRef,
          recency,
          structural
        },
        matchedKeywords: lexical.matchedWords
      });
    }

    // Sort candidates by relevance score descending
    scoredTurns.sort((a, b) => b.relevanceScore - a.relevanceScore);

    // Pick top K candidates
    const rankedCandidates = scoredTurns.slice(0, maxCandidates);
    const topScore = rankedCandidates.length > 0 ? rankedCandidates[0].relevanceScore : 0;

    // Evaluate confidence that more context is needed
    const { confidence, needsContext } = evaluateContextConfidence(rankedCandidates, contextDep);

    // Reconstruct candidates into their original chronological order
    const reconstructedOrder = reconstructChronologicalOrder(rankedCandidates);

    return {
      rankedCandidates,
      reconstructedOrder,
      contextConfidence: confidence,
      needsContext,
      topScore
    };
  }

  return {
    rankTurnsByRelevance,
    reconstructChronologicalOrder,
    extractContentWords,
    computeLexicalOverlap,
    computeExplicitReferenceScore,
    computeRecencyScore,
    computeStructuralScore,
    DEFAULT_MAX_CANDIDATES
  };
});
