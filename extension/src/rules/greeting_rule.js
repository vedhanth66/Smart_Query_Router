/**
 * Smart Query Router - Configurable Greeting & Conversational Pleasantry Rule
 * Narrowly scoped detection for trivial greetings, polite pleasantries, and sign-offs.
 * Strictly avoids capturing substantive queries, questions containing greeting words,
 * and ambiguous or multi-lingual text.
 * Strictly separates detection from response generation.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const decisionModule = require('../shared/decision_engine');
    module.exports = factory(decisionModule);
  } else {
    const decisionModule = root.SmartQueryRouterDecision;
    root.SmartQueryRouterGreeting = factory(decisionModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (decisionModule) {
  'use strict';

  const { DecisionOutcome, createOptimizationDecision } = decisionModule;

  const RULE_ID = 'RULE_LOCAL_CONVERSATIONAL_GREETING';

  const GreetingType = Object.freeze({
    GREETING: 'GREETING',
    PLEASANTRY: 'PLEASANTRY',
    SIGN_OFF: 'SIGN_OFF'
  });

  const DEFAULT_CONFIG = Object.freeze({
    maxWordCount: 5,
    maxCharCount: 40,
    standaloneGreetings: [
      'hi', 'hello', 'hey', 'hiya', 'howdy',
      'good morning', 'good afternoon', 'good evening', 'greetings'
    ],
    conversationalPleasantries: [
      'how are you', 'how are you doing', 'how is it going', "how's it going",
      'hope you are well', 'hope you are doing well', 'how have you been'
    ],
    signOffs: [
      'thanks', 'thank you', 'thanks a lot', 'thank you so much',
      'bye', 'goodbye', 'see you', 'have a good day', 'have a great day'
    ],
    allowedAddresses: [
      'claude', 'there', 'assistant', 'bot', 'friend'
    ]
  });

  // Markers indicating substantive requests, questions, or instructions
  // Any presence of these words immediately disqualifies a prompt from being a trivial greeting
  const SUBSTANTIVE_INTENT_PATTERN = /\b(write|explain|create|help|code|tell|show|give|what|why|where|when|who|which|how to|can you|could you|would you|will you|fix|debug|find|search|is it|are you able|do you|please|make|generate|build|solve)\b/i;

  /**
   * Normalizes input text for greeting inspection
   * @param {string} text
   * @returns {string}
   */
  function normalizeGreetingText(text) {
    if (typeof text !== 'string') return '';
    return text
      .trim()
      .toLowerCase()
      .replace(/[!?,;:\.\(\)]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /**
   * Classifies whether a prompt is a standalone trivial greeting or pleasantry
   * @param {string} rawPrompt
   * @param {object} [customConfig]
   * @returns {{ isGreeting: boolean, type?: string, matchedPhrase?: string, reason: string }}
   */
  function classifyGreeting(rawPrompt, customConfig = {}) {
    if (typeof rawPrompt !== 'string') {
      return { isGreeting: false, reason: 'Invalid non-string input.' };
    }

    const trimmed = rawPrompt.trim();
    if (trimmed.length === 0) {
      return { isGreeting: false, reason: 'Ineligible: empty prompt.' };
    }

    const config = { ...DEFAULT_CONFIG, ...customConfig };

    // 1. Length guard: trivial greetings are short
    if (trimmed.length > config.maxCharCount) {
      return {
        isGreeting: false,
        reason: `Ineligible: prompt length (${trimmed.length}) exceeds maximum greeting threshold (${config.maxCharCount}).`
      };
    }

    const words = trimmed.split(/\s+/).filter(Boolean);
    if (words.length > config.maxWordCount) {
      return {
        isGreeting: false,
        reason: `Ineligible: word count (${words.length}) exceeds maximum greeting threshold (${config.maxWordCount}).`
      };
    }

    // 2. Substantive intent check: if prompt contains verbs/question words, it is NOT a trivial greeting
    if (SUBSTANTIVE_INTENT_PATTERN.test(trimmed)) {
      return {
        isGreeting: false,
        reason: 'Ineligible: prompt contains substantive instructional or question intent.'
      };
    }

    const normalized = normalizeGreetingText(trimmed);

    // 3. Exact matching against sign-offs
    for (const signOff of config.signOffs) {
      if (normalized === signOff || normalized === `${signOff} claude`) {
        return {
          isGreeting: true,
          type: GreetingType.SIGN_OFF,
          matchedPhrase: normalized,
          reason: `Eligible: matched standalone sign-off "${signOff}".`
        };
      }
    }

    // 4. Exact matching against conversational pleasantries
    for (const pleasantry of config.conversationalPleasantries) {
      if (normalized === pleasantry || normalized === `${pleasantry} claude`) {
        return {
          isGreeting: true,
          type: GreetingType.PLEASANTRY,
          matchedPhrase: normalized,
          reason: `Eligible: matched trivial conversational pleasantry "${pleasantry}".`
        };
      }
    }

    // 5. Matching standalone greetings (with optional single address token like "claude", "there")
    for (const greeting of config.standaloneGreetings) {
      if (normalized === greeting) {
        return {
          isGreeting: true,
          type: GreetingType.GREETING,
          matchedPhrase: normalized,
          reason: `Eligible: matched standalone greeting "${greeting}".`
        };
      }

      // Check greeting + address: e.g. "hello claude", "hi there"
      for (const addr of config.allowedAddresses) {
        if (normalized === `${greeting} ${addr}`) {
          return {
            isGreeting: true,
            type: GreetingType.GREETING,
            matchedPhrase: normalized,
            reason: `Eligible: matched addressed greeting "${greeting} ${addr}".`
          };
        }
      }
    }

    return {
      isGreeting: false,
      reason: 'Ineligible: prompt is not a recognized standalone greeting or pleasantry.'
    };
  }

  /**
   * Factory to create an OptimizationDecisionEngine rule plugin
   * @param {object} [config]
   */
  function createGreetingRule(config = {}) {
    return {
      id: RULE_ID,
      evaluate(queryEvent) {
        if (!queryEvent || !queryEvent.content || (!queryEvent.content.rawPrompt && !queryEvent.content.normalizedPrompt)) {
          return null;
        }

        // If privacy classification detected sensitive tokens/keys, do not handle locally
        if (queryEvent.privacy && queryEvent.privacy.level === 'SENSITIVE') {
          return null;
        }

        const promptText = queryEvent.content.normalizedPrompt !== undefined
          ? queryEvent.content.normalizedPrompt
          : queryEvent.content.rawPrompt;

        const classification = classifyGreeting(promptText, config);
        if (!classification.isGreeting) {
          return null; // Fall through to next rule or baseline
        }

        return createOptimizationDecision({
          outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
          ruleId: RULE_ID,
          reason: classification.reason,
          confidence: 1.0,
          metadata: {
            greetingType: classification.type,
            matchedPhrase: classification.matchedPhrase,
            isTrivial: true,
            actionable: false // Indicates no downstream model generation is required
          }
        });
      }
    };
  }

  const defaultGreetingRule = createGreetingRule();

  return {
    RULE_ID,
    GreetingType,
    DEFAULT_CONFIG,
    classifyGreeting,
    createGreetingRule,
    greetingRule: defaultGreetingRule
  };
});
