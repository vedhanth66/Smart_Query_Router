/**
 * Smart Query Router - Deterministic Date/Time Rule
 * Conservative classification for simple date/time utility requests that can be
 * answered deterministically from the browser environment (current local time, today's date).
 * Strictly rejects queries requiring external facts (weather, news, stocks, remote city time).
 * Classification only: does not display UI or alter Claude's submission.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const decisionModule = require('../shared/decision_engine');
    module.exports = factory(decisionModule);
  } else {
    const decisionModule = root.SmartQueryRouterDecision;
    root.SmartQueryRouterDateTime = factory(decisionModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (decisionModule) {
  'use strict';

  const { DecisionOutcome, createOptimizationDecision } = decisionModule;

  const RULE_ID = 'RULE_LOCAL_DETERMINISTIC_DATETIME';

  // Subtypes of local date/time requests
  const DateTimeCategory = Object.freeze({
    CURRENT_TIME: 'CURRENT_TIME',
    CURRENT_DATE: 'CURRENT_DATE',
    CURRENT_DAY_OF_WEEK: 'CURRENT_DAY_OF_WEEK',
    CURRENT_YEAR: 'CURRENT_YEAR',
    BROWSER_TIMEZONE: 'BROWSER_TIMEZONE'
  });

  // Explicitly prohibited keywords indicating external current facts or complex lookups
  const PROHIBITED_EXTERNAL_FACTS_PATTERN = /\b(weather|forecast|temperature|rain|sunny|stock|stocks|share price|crypto|bitcoin|btc|eth|news|headline|headlines|score|scores|game|flight|election|traffic)\b/i;

  // Pattern detecting remote locations/cities (requires world clock lookup or geocoding)
  const REMOTE_LOCATION_PATTERN = /\b(in|at|for)\s+([a-zA-Z]{3,}|[A-Z][a-z]+)\b/i;

  // Patterns for genuinely local deterministic date/time requests
  const LOCAL_PATTERNS = [
    // Current time
    {
      category: DateTimeCategory.CURRENT_TIME,
      regex: /^(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+|local\s+)?time(?:\s+right\s+now)?|what\s+time\s+is\s+it(?:\s+right\s+now)?|current\s+time|local\s+time|time\s+now)\s*[\?\.]*$/i
    },
    // Current date
    {
      category: DateTimeCategory.CURRENT_DATE,
      regex: /^(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+)?date(?:\s+today)?|what(?:'s|\s+is)\s+today(?:'?s)?\s+date|today(?:'?s)?\s+date|current\s+date|date\s+today)\s*[\?\.]*$/i
    },
    // Current day of the week
    {
      category: DateTimeCategory.CURRENT_DAY_OF_WEEK,
      regex: /^(?:what\s+day\s+(?:is\s+it\s+|is\s+)?today|what\s+day\s+is\s+it|what(?:'s|\s+is)\s+today'?s\s+day)\s*[\?\.]*$/i
    },
    // Current year
    {
      category: DateTimeCategory.CURRENT_YEAR,
      regex: /^(?:what\s+year\s+(?:is\s+it|is\s+this)|what\s+(?:is\s+the\s+|'s\s+the\s+)?current\s+year)\s*[\?\.]*$/i
    },
    // Browser timezone
    {
      category: DateTimeCategory.BROWSER_TIMEZONE,
      regex: /^(?:what\s+(?:is\s+my|'s\s+my|is\s+the|'s\s+the)\s+(?:current\s+)?timezone|what\s+timezone\s+am\s+i\s+in|current\s+timezone)\s*[\?\.]*$/i
    }
  ];

  /**
   * Safely obtain the browser's exposed timezone without external lookup
   * @returns {string}
   */
  function getBrowserExposedTimezone() {
    try {
      if (typeof Intl === 'object' && Intl.DateTimeFormat) {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
      }
    } catch (err) {
      // Fallback
    }
    return 'Browser-Local';
  }

  /**
   * Classify a query to determine if it is a simple local date/time request
   * @param {string} rawPrompt
   * @returns {{ eligible: boolean, category?: string, reason: string }}
   */
  function classifyDateTimeQuery(rawPrompt) {
    if (typeof rawPrompt !== 'string') {
      return { eligible: false, reason: 'Invalid non-string input.' };
    }

    const trimmed = rawPrompt.trim();
    if (trimmed.length === 0 || trimmed.length > 80) {
      return { eligible: false, reason: 'Input empty or exceeds maximum length for simple date/time query.' };
    }

    // 1. Prohibit external current facts (weather, stocks, news)
    if (PROHIBITED_EXTERNAL_FACTS_PATTERN.test(trimmed)) {
      return {
        eligible: false,
        reason: 'Ineligible: query references external dynamic facts (weather/news/stocks) requiring external API calls.'
      };
    }

    // 2. Prohibit queries asking for time/date in other geographic locations/cities
    // e.g. "what time is it in Tokyo?", "current time for London"
    if (REMOTE_LOCATION_PATTERN.test(trimmed)) {
      return {
        eligible: false,
        reason: 'Ineligible: query specifies a remote geographic location requiring external timezone resolution.'
      };
    }

    // 3. Evaluate against conservative local pattern whitelist
    for (const { category, regex } of LOCAL_PATTERNS) {
      if (regex.test(trimmed)) {
        return {
          eligible: true,
          category,
          reason: `Eligible: matched deterministic local ${category.toLowerCase().replace(/_/g, ' ')} request resolvable directly from browser environment.`
        };
      }
    }

    return {
      eligible: false,
      reason: 'Ineligible: query does not match conservative deterministic local date/time patterns.'
    };
  }

  /**
   * Optimization Decision Engine Rule Plugin
   */
  const dateTimeRule = {
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

      const classification = classifyDateTimeQuery(promptText);
      if (!classification.eligible) {
        return null; // Not eligible; pass to next rule or baseline
      }

      const browserTz = getBrowserExposedTimezone();

      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: RULE_ID,
        reason: classification.reason,
        confidence: 1.0,
        metadata: {
          category: classification.category,
          browserTimezone: browserTz,
          isEligible: true,
          resolutionSource: 'BROWSER_ENVIRONMENT',
          eligibilityReason: 'Can be deterministically resolved using local browser Date/Intl APIs without external network calls.'
        }
      });
    }
  };

  return {
    RULE_ID,
    DateTimeCategory,
    classifyDateTimeQuery,
    getBrowserExposedTimezone,
    dateTimeRule
  };
});
