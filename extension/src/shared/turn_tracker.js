/**
 * Smart Query Router - Bounded Local Turn Tracker
 * 
 * Builds and maintains a bounded, in-memory representation of recent
 * conversation turns for local relevance and context analysis.
 * 
 * DESIGN CONSTRAINTS:
 * - Small, configurable window (default: max 4 turns, max 300 chars per snippet).
 * - Minimum information stored: role, timestamp, bounded snippet, length, truncated flag.
 * - Strictly in-memory: ZERO storage persistence (no localStorage, no chrome.storage).
 * - Zero upload: strictly local, not transmitted to backend.
 * - Strict cleanup on navigation and conversation switch.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterTurnTracker = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_MAX_TURNS = 4;
  const DEFAULT_MAX_SNIPPET_CHARS = 300;
  const DEFAULT_RETENTION_TTL_MS = 900_000; // 15 minutes max in-memory retention

  /**
   * Generates a unique, non-sensitive local turn identifier
   * @param {string} role
   * @returns {string}
   */
  function generateTurnId(role) {
    const ts = Date.now();
    const rand = Math.random().toString(36).slice(2, 7);
    return `turn_${role}_${ts}_${rand}`;
  }

  /**
   * Extracts minimal, bounded turn information needed for relevance analysis
   * @param {string} text
   * @param {number} maxSnippetChars
   * @returns {{ snippet: string, characterCount: number, truncated: boolean }}
   */
  function createBoundedSnippet(text, maxSnippetChars) {
    if (typeof text !== 'string') {
      return { snippet: '', characterCount: 0, truncated: false };
    }

    const raw = text.trim();
    const characterCount = raw.length;

    if (characterCount <= maxSnippetChars) {
      return {
        snippet: raw,
        characterCount,
        truncated: false
      };
    }

    return {
      snippet: raw.slice(0, maxSnippetChars).trimEnd(),
      characterCount,
      truncated: true
    };
  }

  class RecentTurnsTracker {
    /**
     * @param {object} [options]
     * @param {number} [options.maxTurns] - Maximum number of turns to retain (default: 4)
     * @param {number} [options.maxSnippetChars] - Maximum characters per turn snippet (default: 300)
     * @param {number} [options.retentionTtlMs] - Maximum duration to retain turns in memory (default: 15 min)
     * @param {string|null} [options.conversationId] - Initial conversation UUID
     */
    constructor(options = {}) {
      this.maxTurns = options.maxTurns !== undefined ? Math.max(1, options.maxTurns) : DEFAULT_MAX_TURNS;
      this.maxSnippetChars = options.maxSnippetChars !== undefined ? Math.max(50, options.maxSnippetChars) : DEFAULT_MAX_SNIPPET_CHARS;
      this.retentionTtlMs = options.retentionTtlMs !== undefined ? Math.max(10, options.retentionTtlMs) : DEFAULT_RETENTION_TTL_MS;
      this.currentConversationId = options.conversationId || null;

      // In-memory bounded array (FIFO ring buffer)
      this.turns = [];

      // Deduplication guard for repeated DOM observations
      this.lastRecordedTextHash = null;
      this.lastRecordedTimestamp = 0;
    }

    /**
     * Evicts turns that have exceeded the retention TTL.
     * Ensures conversation content is not persisted in memory longer than required.
     * @param {number} [now]
     * @returns {number} Number of turns pruned
     */
    pruneExpiredTurns(now = Date.now()) {
      const initialCount = this.turns.length;
      this.turns = this.turns.filter((turn) => {
        const turnTime = typeof turn.recordedAt === 'number' ? turn.recordedAt : turn.timestamp;
        return now - turnTime <= this.retentionTtlMs;
      });
      return initialCount - this.turns.length;
    }

    /**
     * Record a new conversational turn (user or assistant)
     * @param {object} params
     * @param {'user'|'assistant'} params.role
     * @param {string} params.text
     * @param {string|null} [params.conversationId]
     * @param {number} [params.timestamp]
     * @returns {object|null} The recorded turn object or null if ignored
     */
    recordTurn({ role, text, conversationId = null, timestamp = Date.now() }) {
      this.pruneExpiredTurns();
      if (!text || typeof text !== 'string' || !text.trim()) {
        return null;
      }

      // Check conversation boundary: if conversation changed, clear prior history
      if (conversationId && this.currentConversationId && conversationId !== this.currentConversationId) {
        this.switchConversation(conversationId);
      } else if (conversationId && !this.currentConversationId) {
        this.currentConversationId = conversationId;
      }

      const normalizedRole = role === 'assistant' ? 'assistant' : 'user';
      const bounded = createBoundedSnippet(text, this.maxSnippetChars);

      // Simple deduplication guard (prevents duplicate observation within 1500ms)
      const textSample = bounded.snippet.slice(0, 50);
      if (
        this.lastRecordedTextHash === `${normalizedRole}_${textSample}` &&
        timestamp - this.lastRecordedTimestamp < 1500
      ) {
        return null;
      }

      this.lastRecordedTextHash = `${normalizedRole}_${textSample}`;
      this.lastRecordedTimestamp = timestamp;

      // Compute minimal features needed for relevance analysis
      const hasCode = /```|~~~|`[^`\n]+`/.test(bounded.snippet);
      const hasMath = /[+\-*/=^%×÷≤≥∑∫√π]/.test(bounded.snippet);
      const hasQuestions = bounded.snippet.includes('?');

      const turn = {
        turnId: generateTurnId(normalizedRole),
        role: normalizedRole,
        timestamp,
        recordedAt: Date.now(),
        snippet: bounded.snippet,
        characterCount: bounded.characterCount,
        truncated: bounded.truncated,
        features: {
          hasCode,
          hasMath,
          hasQuestions,
          estimatedTokens: Math.ceil(bounded.characterCount / 4)
        }
      };

      this.turns.push(turn);

      // Enforce small bounded window (FIFO eviction)
      while (this.turns.length > this.maxTurns) {
        this.turns.shift();
      }

      return turn;
    }

    /**
     * Switch to a new conversation, resetting history immediately
     * @param {string|null} newConversationId
     */
    switchConversation(newConversationId) {
      this.clear();
      this.currentConversationId = newConversationId || null;
    }

    /**
     * Clear all recorded turns immediately (e.g. on navigation, unmount, new chat)
     */
    clear() {
      this.turns = [];
      this.lastRecordedTextHash = null;
      this.lastRecordedTimestamp = 0;
    }

    /**
     * Returns a copy of the current bounded turns
     * @returns {Array<object>}
     */
    getRecentTurns() {
      this.pruneExpiredTurns();
      return this.turns.slice();
    }

    /**
     * Returns the total count of currently held turns
     * @returns {number}
     */
    getTurnCount() {
      this.pruneExpiredTurns();
      return this.turns.length;
    }

    /**
     * Returns the most recent turn, if any
     * @returns {object|null}
     */
    getLastTurn() {
      this.pruneExpiredTurns();
      return this.turns.length > 0 ? this.turns[this.turns.length - 1] : null;
    }

    /**
     * Returns the most recent assistant response turn, if any
     * @returns {object|null}
     */
    getLastAssistantTurn() {
      this.pruneExpiredTurns();
      for (let i = this.turns.length - 1; i >= 0; i--) {
        if (this.turns[i].role === 'assistant') {
          return this.turns[i];
        }
      }
      return null;
    }

    /**
     * Formats minimal bounded context summary safe for local relevance analysis.
     * Guaranteed to be bounded in length and tokens.
     * @returns {{
     *   turnCount: number,
     *   conversationId: string|null,
     *   turns: Array<{ role: string, snippet: string, truncated: boolean, timestamp: number }>,
     *   hasAssistantHistory: boolean
     * }}
     */
    getRelevanceContext() {
      this.pruneExpiredTurns();
      return {
        turnCount: this.turns.length,
        conversationId: this.currentConversationId,
        turns: this.turns.map((t) => ({
          turnId: t.turnId,
          role: t.role,
          snippet: t.snippet,
          truncated: t.truncated,
          timestamp: t.timestamp
        })),
        hasAssistantHistory: this.turns.some((t) => t.role === 'assistant')
      };
    }
  }

  return {
    RecentTurnsTracker,
    createBoundedSnippet,
    DEFAULT_MAX_TURNS,
    DEFAULT_MAX_SNIPPET_CHARS
  };
});
