/**
 * Smart Query Router - First-Pass Context-Dependency Detector
 * 
 * Performs conservative, deterministic, local analysis to determine whether
 * a user query is context-dependent (i.e. refers to preceding conversation history)
 * or is completely standalone.
 * 
 * DESIGN PRINCIPLES:
 * - Local signals only: zero network calls, zero LLM dependencies.
 * - Conservative detection: avoids over-triggering on standalone questions
 *   containing coincidental words like "that" (conjunction/relative pronoun),
 *   expletive "it" ("is it possible to..."), temporal "this" ("this week"),
 *   or queries with self-contained embedded code/quotes.
 * - Non-prescriptive: output is ONLY a decision about whether more context
 *   analysis is necessary. Does NOT summarize, store, or transmit conversation history.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterContextDetector = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // 1. Direct continuation triggers (elliptical, continuation commands, bare follow-ups)
  const CONTINUATION_EXACT_REGEX = /^(?:continue|go on|tell me more|more please|keep going|proceed|next|next step|what next|more)[.!?]*$/i;
  const CONTINUATION_PHRASES = [
    { label: 'CONTINUE_COMMAND', regex: /\b(?:continue|keep going|proceed|go on)\b/i },
    { label: 'TELL_ME_MORE', regex: /\b(?:tell me more|give me more|show me more|more details?)\b/i },
    { label: 'NEXT_STEP', regex: /\b(?:next step|next part|next section|what(?:'s| is) next)\b/i },
    { label: 'ANOTHER_ONE', regex: /\b(?:another (?:one|example|alternative)|one more)\b/i }
  ];

  // Elliptical follow-up starting with and/what about/how about
  const ELLIPTICAL_FOLLOW_UP_REGEX = /^(?:and|what about|how about)\s+(?:for\s+)?[\w\s\-\.]+[?.]*$/i;

  // Bare interrogatives (single word or short questions like "why?", "how so?")
  const BARE_INTERROGATIVE_REGEX = /^(?:why|how|why so|why not|how so|and then)[?!.]*$/i;

  // 2. Explicit backward references to preceding turns
  const EXPLICIT_HISTORY_REFERENCES = [
    { label: 'THE_ABOVE', regex: /\b(?:the\s+above|mentioned\s+above|from\s+above|as\s+above|in\s+the\s+above)\b/i },
    { label: 'PREVIOUS_OUTPUT', regex: /\b(?:previous|earlier|prior|last)\s+(?:response|answer|message|output|code|step|suggestion|solution|example|version|point)\b/i },
    { label: 'SPEAKER_REFERENCE', regex: /\b(?:in\s+your\s+(?:last|previous|earlier)|you\s+(?:said|mentioned|wrote|suggested|provided|showed))\b/i },
    { label: 'FORMER_OR_LATTER', regex: /\b(?:the\s+former|the\s+latter)\b/i },
    { label: 'SAME_THING', regex: /\b(?:do\s+the\s+same|the\s+same\s+(?:thing|way)|same\s+for|same\s+with)\b/i },
    { label: 'INSTEAD_OF_THAT', regex: /\b(?:instead\s+of\s+(?:that|this|it)|replace\s+(?:that|this|it)\s+with)\b/i },
    { label: 'WHAT_DID_YOU_MEAN', regex: /\bwhat\s+did\s+you\s+mean\b/i }
  ];

  // 3. Anaphoric actions: action verb directly targeting pronoun (it, that, this, them)
  const ANAPHORIC_VERB_PRONOUN = [
    { label: 'ACTION_ON_PRONOUN', regex: /\b(?:fix|debug|rewrite|refactor|optimize|explain|translate|summarize|run|test|change|modify|update|simplify|clarify|convert|rephrase)\s+(?:it|that|this|them)\b/i },
    { label: 'WHY_DID_PRONOUN', regex: /\bwhy\s+(?:did|does|is|was|will|would)\s+(?:it|that|this|they)\s+(?:happen|fail|break|work|error|occur|change)\b/i },
    { label: 'WHAT_DOES_PRONOUN_MEAN', regex: /\bwhat\s+does\s+(?:this|that|it)\s+mean\b/i },
    { label: 'MAKE_PRONOUN_ADJ', regex: /\bmake\s+(?:it|this|that|them)\s+(?:faster|cleaner|better|shorter|longer|modular|async|generic|work)\b/i },
    { label: 'ADD_TO_PRONOUN', regex: /\badd\s+[\w\s]+\s+to\s+(?:it|this|that|them)\b/i }
  ];

  // 4. Guards / False Positive Suppressors (standalone markers)

  // Temporal "this" phrases (referring to time, not conversation)
  const TEMPORAL_THIS_REGEX = /\bthis\s+(?:morning|afternoon|evening|night|week|month|year|weekend|quarter|century|decade|season|spring|summer|fall|winter|time|hour|day)\b/i;

  // Cataphoric "this" introducing an inline concept (e.g. "this concept called recursion")
  const INTRODUCTORY_THIS_REGEX = /\bthis\s+(?:thing|concept|idea|term|pattern|equation|theorem)\s+called\b/i;

  // Expletive / Dummy "it" (dummy subjects that carry no referential meaning)
  const EXPLETIVE_IT_PATTERNS = [
    /^(?:is|was|can|could|would|will)\s+it\s+(?:possible|feasible|true|necessary|safe|legal|normal|common|okay|advisable)\s+to\b/i,
    /\bit\s+is\s+(?:possible|important|common|recommended|necessary|safe|crucial|easy|hard|difficult|better|vital|essential)\s+(?:to|that)\b/i,
    /\bit\s+(?:seems|appears|looks)\s+(?:like|that|to)\b/i,
    /\bwhat\s+is\s+it\s+like\s+to\b/i
  ];

  // Self-contained reference with colon or inline specification (e.g. "Calculate this: 2 + 2")
  const INLINE_SPECIFICATION_REGEX = /\b(?:this|the\s+following)\s*:\s*\S+/i;

  /**
   * Evaluates whether a query references self-contained content embedded
   * directly within its own prompt (e.g. a code fence, long block, or equation).
   * @param {string} text
   * @param {object} [features]
   * @returns {boolean}
   */
  function isReferentInlineResolved(text, features) {
    // If prompt contains a fenced code block or indented code block
    const hasCode = features && features.code
      ? (features.code.hasCodeFence || features.code.hasIndentedCode)
      : /```|~~~|^(?: {4}|\t)[^\s]/m.test(text);

    // If prompt contains quotes or inline code
    const hasInlineDelimiters = /`[^`\n]+`|"([^"\\]|\\.)*"/.test(text);

    // Pattern matching "this code", "this script", "this error", "this function"
    const referencesInlineEntity = /\b(?:this|the\s+following)\s+(?:code|snippet|function|script|error|trace|program|file|implementation|text|quote)\b/i.test(text);

    if (referencesInlineEntity && (hasCode || hasInlineDelimiters)) {
      return true;
    }

    if (INLINE_SPECIFICATION_REGEX.test(text)) {
      return true;
    }

    return false;
  }

  /**
   * Checks if an appearance of "it" is merely an expletive (dummy) pronoun.
   * @param {string} text
   * @returns {boolean}
   */
  function isExpletiveIt(text) {
    for (const pattern of EXPLETIVE_IT_PATTERNS) {
      if (pattern.test(text)) {
        return true;
      }
    }
    return false;
  }

  /**
   * Analyzes prompt text using local signals only to determine if more
   * context analysis is necessary.
   * 
   * @param {string} promptText - The user prompt text (raw or normalized)
   * @param {object} [features] - Optional pre-extracted local feature signals
   * @returns {{
   *   requiresContextAnalysis: boolean,
   *   confidence: 'HIGH' | 'MEDIUM' | 'LOW',
   *   category: 'CONTINUATION' | 'ANAPHORIC_REFERENCE' | 'STANDALONE' | 'INLINE_RESOLVED',
   *   reason: string,
   *   matchedSignals: string[]
   * }}
   */
  function detectContextDependency(promptText, features = null) {
    const text = typeof promptText === 'string' ? promptText.trim() : '';

    if (text.length === 0) {
      return {
        requiresContextAnalysis: false,
        confidence: 'HIGH',
        category: 'STANDALONE',
        reason: 'Empty prompt requires no context analysis.',
        matchedSignals: []
      };
    }

    const matchedSignals = [];

    // 1. Check for self-contained inline referents (e.g. prompt contains code block and asks about "this code")
    if (isReferentInlineResolved(text, features)) {
      return {
        requiresContextAnalysis: false,
        confidence: 'HIGH',
        category: 'INLINE_RESOLVED',
        reason: 'Apparent demonstrative reference is resolved by self-contained content in the prompt.',
        matchedSignals: ['INLINE_RESOLVED_REFERENT']
      };
    }

    // 2. Direct Continuation Commands (High Confidence)
    if (CONTINUATION_EXACT_REGEX.test(text)) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'CONTINUATION',
        reason: 'Prompt is an explicit standalone continuation command.',
        matchedSignals: ['EXACT_CONTINUATION']
      };
    }

    if (ELLIPTICAL_FOLLOW_UP_REGEX.test(text)) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'CONTINUATION',
        reason: 'Prompt is an elliptical follow-up question requiring prior conversation context.',
        matchedSignals: ['ELLIPTICAL_FOLLOW_UP']
      };
    }

    if (BARE_INTERROGATIVE_REGEX.test(text)) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'CONTINUATION',
        reason: 'Prompt is a bare interrogative word requiring prior conversation context.',
        matchedSignals: ['BARE_INTERROGATIVE']
      };
    }

    for (const phrase of CONTINUATION_PHRASES) {
      if (phrase.regex.test(text)) {
        matchedSignals.push(phrase.label);
      }
    }

    if (matchedSignals.length > 0) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'CONTINUATION',
        reason: `Detected continuation cue: ${matchedSignals.join(', ')}.`,
        matchedSignals
      };
    }

    // 3. Explicit Backward References (High Confidence)
    for (const ref of EXPLICIT_HISTORY_REFERENCES) {
      if (ref.regex.test(text)) {
        matchedSignals.push(ref.label);
      }
    }

    if (matchedSignals.length > 0) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'ANAPHORIC_REFERENCE',
        reason: `Detected explicit conversation history reference: ${matchedSignals.join(', ')}.`,
        matchedSignals
      };
    }

    // 4. Anaphoric Action Patterns (Medium-High Confidence)
    for (const anaphor of ANAPHORIC_VERB_PRONOUN) {
      if (anaphor.regex.test(text)) {
        matchedSignals.push(anaphor.label);
      }
    }

    if (matchedSignals.length > 0) {
      return {
        requiresContextAnalysis: true,
        confidence: 'HIGH',
        category: 'ANAPHORIC_REFERENCE',
        reason: `Detected anaphoric action verb targeting pronoun: ${matchedSignals.join(', ')}.`,
        matchedSignals
      };
    }

    // 5. Conservative Guard against Over-triggering on Standalone Prompts:
    // If the prompt contains "this" but matches temporal or introductory patterns, ignore
    if (TEMPORAL_THIS_REGEX.test(text)) {
      // Ignored: temporal reference like "this morning"
    }

    if (INTRODUCTORY_THIS_REGEX.test(text)) {
      // Ignored: introductory phrase like "this concept called"
    }

    if (isExpletiveIt(text)) {
      // Ignored: expletive pronoun like "is it possible to"
    }

    // Default: Standalone query, no additional context analysis needed
    return {
      requiresContextAnalysis: false,
      confidence: 'HIGH',
      category: 'STANDALONE',
      reason: 'No anaphoric references or continuation cues detected; query is self-contained.',
      matchedSignals: []
    };
  }

  return {
    detectContextDependency,
    isReferentInlineResolved,
    isExpletiveIt
  };
});
