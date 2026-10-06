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
    CURRENT_DATETIME: 'CURRENT_DATETIME',
    CURRENT_TIME: 'CURRENT_TIME',
    CURRENT_DATE: 'CURRENT_DATE',
    CURRENT_DAY_OF_WEEK: 'CURRENT_DAY_OF_WEEK',
    CURRENT_YEAR: 'CURRENT_YEAR',
    BROWSER_TIMEZONE: 'BROWSER_TIMEZONE',
    DATE_DIFFERENCE: 'DATE_DIFFERENCE',
    DATE_OFFSET: 'DATE_OFFSET'
  });

  // Explicitly prohibited keywords indicating external current facts or complex lookups
  const PROHIBITED_EXTERNAL_FACTS_PATTERN = /\b(weather|forecast|temperature|rain|sunny|stock|stocks|share price|crypto|bitcoin|btc|eth|news|headline|headlines|score|scores|game|flight|election|traffic)\b/i;

  // Pattern detecting remote locations/cities (requires world clock lookup or geocoding)
  const REMOTE_LOCATION_PATTERN = /\b(in|at|for)\s+([a-zA-Z]{3,}|[A-Z][a-z]+)\b/i;

  // Patterns for genuinely local deterministic date/time requests
  const LOCAL_PATTERNS = [
    // Current date and time combined (e.g. "date and time", "datetime", "date & time", "current date and time", "what is the date and time right now")
    {
      category: DateTimeCategory.CURRENT_DATETIME,
      regex: /^(?:(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+|today(?:'?s)?\s+|local\s+)?|(?:tell|show|give)\s+me\s+(?:the\s+)?(?:current\s+|today(?:'?s)?\s+|local\s+)?|current\s+|today(?:'?s)?\s+|local\s+)?(?:date\s+(?:and|&)\s+time|time\s+(?:and|&)\s+date|datetime|date\s+time))(?:\s+(?:right\s+now|now|today|please))*\s*[\?\.\!]*$/i
    },
    // Current time (e.g. "time", "what time is it", "current time", "the time", "local time", "what's the time right now")
    {
      category: DateTimeCategory.CURRENT_TIME,
      regex: /^(?:(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+|local\s+)?|(?:tell|show|give)\s+me\s+(?:the\s+)?(?:current\s+|local\s+)?|what\s+time(?:\s+is\s+it)?|current\s+|local\s+|the\s+)?(?:time|clock)|what\s+time(?:\s+is\s+it)?)(?:\s+(?:right\s+now|now|today|please))*\s*[\?\.\!]*$/i
    },
    // Current date (e.g. "date", "today", "what is today's date", "today's date", "current date", "the date", "what is the date today")
    {
      category: DateTimeCategory.CURRENT_DATE,
      regex: /^(?:(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+)?|(?:tell|show|give)\s+me\s+(?:the\s+)?(?:current\s+)?|current\s+|the\s+)?date|what(?:'s|\s+is)\s+today(?:'?s)?(?:\s+date)?|today(?:'?s)?(?:\s+date)?|today)(?:\s+(?:today|right\s+now|now|please))*\s*[\?\.\!]*$/i
    },
    // Current day of the week (e.g. "what day is today", "what day is it", "day today", "which day is today")
    {
      category: DateTimeCategory.CURRENT_DAY_OF_WEEK,
      regex: /^(?:(?:what\s+day\s+(?:is\s+it\s+|is\s+)?today|what\s+day\s+is\s+it|which\s+day\s+is\s+(?:it\s+|today)|what(?:'s|\s+is)\s+today'?s\s+day|day\s+today|which\s+day\s+is\s+it\s+today|what\s+day|day)(?:\s+(?:today|right\s+now|now|please))*)\s*[\?\.\!]*$/i
    },
    // Current year
    {
      category: DateTimeCategory.CURRENT_YEAR,
      regex: /^(?:(?:what\s+year\s+(?:is\s+it|is\s+this)|what\s+(?:is\s+the\s+|'s\s+the\s+)?current\s+year|which\s+year\s+is\s+(?:it|this)|current\s+year|year)(?:\s+(?:right\s+now|now|today|please))*)\s*[\?\.\!]*$/i
    },
    // Browser timezone
    {
      category: DateTimeCategory.BROWSER_TIMEZONE,
      regex: /^(?:(?:what\s+(?:is\s+my|'s\s+my|is\s+the|'s\s+the)\s+(?:current\s+)?timezone|what\s+timezone\s+am\s+i\s+in|current\s+timezone|my\s+timezone|timezone)(?:\s+(?:right\s+now|now|today|please))*)\s*[\?\.\!]*$/i
    },
    // Days between two dates: "days between Jan 1 and March 15", "days between 2026-01-01 and 2026-03-15"
    {
      category: DateTimeCategory.DATE_DIFFERENCE,
      regex: /^(?:(?:what\s+(?:is|'s)|how\s+many)\s+)?days\s+between\s+(.+?)\s+and\s+(.+?)\s*[\?\.\!]*$/i
    },
    // Days until milestone: "days until Christmas", "days until New Year"
    {
      category: DateTimeCategory.DATE_DIFFERENCE,
      regex: /^(?:(?:what\s+(?:is|'s)|how\s+many)\s+)?days\s+until\s+(christmas|new\s+year(?:'s)?(?:\s+day)?|halloween|valentine(?:'s)?(?:\s+day)?)\s*[\?\.\!]*$/i
    },
    // Date offset: "date in 45 days", "date 3 weeks ago", "45 days from today", "date in 3 weeks"
    {
      category: DateTimeCategory.DATE_OFFSET,
      regex: /^(?:(?:what\s+(?:is|was)\s+the\s+)?date\s+(?:in\s+)?(\d+)\s+(days?|weeks?)\s*(ago)?|(\d+)\s+(days?|weeks?)\s+from\s+(?:today|now))\s*[\?\.\!]*$/i
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

    // Strip zero-width characters, invisible markers, and normalize whitespace
    const trimmed = rawPrompt
      .replace(/[\u200B-\u200D\uFEFF\u00AD\u2060\u180E]/g, '')
      .replace(/\u00A0/g, ' ')
      .replace(/[\r\n\t]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

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
   * Helper to parse a user date string (e.g. "Jan 1", "March 15", "2026-01-01")
   * @param {string} str
   * @param {number} currentYear
   * @returns {Date|null}
   */
  function parseUserDate(str, currentYear) {
    if (!str || typeof str !== 'string') return null;
    const s = str.trim();
    if (/\b\d{4}\b/.test(s)) {
      const d = new Date(s);
      if (!isNaN(d.getTime())) return d;
    }
    const dWithYear = new Date(`${s} ${currentYear}`);
    if (!isNaN(dWithYear.getTime())) return dWithYear;

    const dDirect = new Date(s);
    if (!isNaN(dDirect.getTime())) return dDirect;

    return null;
  }

  /**
   * Evaluates date difference, milestone spans, date offsets, and current datetime
   * @param {string} rawPrompt
   * @param {Date} [referenceDate=new Date()]
   * @returns {{ success: boolean, category: string, expression: string, result: string } | null}
   */
  function evaluateDateTimeCalculation(rawPrompt, referenceDate = new Date()) {
    if (typeof rawPrompt !== 'string') return null;
    const trimmed = rawPrompt
      .replace(/[\u200B-\u200D\uFEFF\u00AD\u2060\u180E]/g, '')
      .replace(/\u00A0/g, ' ')
      .replace(/[\r\n\t]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    if (trimmed.length === 0 || trimmed.length > 80) return null;
    if (PROHIBITED_EXTERNAL_FACTS_PATTERN.test(trimmed)) return null;
    if (REMOTE_LOCATION_PATTERN.test(trimmed)) return null;

    const now = referenceDate instanceof Date && !isNaN(referenceDate.getTime()) ? referenceDate : new Date();

    // 1. Days Between Two Dates: "days between Jan 1 and March 15", "days between 2026-01-01 and 2026-03-15"
    const betweenMatch = trimmed.match(/^(?:(?:what\s+(?:is|'s)|how\s+many)\s+)?days\s+between\s+(.+?)\s+and\s+(.+?)\s*[\?\.\!]*$/i);
    if (betweenMatch) {
      const d1 = parseUserDate(betweenMatch[1], now.getFullYear());
      const d2 = parseUserDate(betweenMatch[2], now.getFullYear());
      if (!d1 || !d2) return null;

      const diffMs = Math.abs(d2.getTime() - d1.getTime());
      const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
      return {
        success: true,
        category: DateTimeCategory.DATE_DIFFERENCE,
        expression: `Days between ${betweenMatch[1].trim()} and ${betweenMatch[2].trim()}`,
        result: `${diffDays} days`
      };
    }

    // 2. Days Until Milestone: "days until Christmas", "days until New Year"
    const untilMatch = trimmed.match(/^(?:(?:what\s+(?:is|'s)|how\s+many)\s+)?days\s+until\s+(christmas|new\s+year(?:'s)?(?:\s+day)?|halloween|valentine(?:'s)?(?:\s+day)?)\s*[\?\.\!]*$/i);
    if (untilMatch) {
      const milestone = untilMatch[1].toLowerCase();
      let targetMonth = 0;
      let targetDay = 1;
      let milestoneName = '';

      if (milestone.includes('christmas')) {
        targetMonth = 11; targetDay = 25; milestoneName = 'Christmas';
      } else if (milestone.includes('new\s*year') || milestone.includes('new year')) {
        targetMonth = 0; targetDay = 1; milestoneName = 'New Year';
      } else if (milestone.includes('halloween')) {
        targetMonth = 9; targetDay = 31; milestoneName = 'Halloween';
      } else if (milestone.includes('valentine')) {
        targetMonth = 1; targetDay = 14; milestoneName = "Valentine's Day";
      }

      let target = new Date(now.getFullYear(), targetMonth, targetDay);
      if (milestoneName === 'New Year') {
        target = new Date(now.getFullYear() + 1, 0, 1);
      } else if (target.getTime() < now.getTime()) {
        target.setFullYear(now.getFullYear() + 1);
      }

      const diffMs = target.getTime() - now.getTime();
      const diffDays = Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
      return {
        success: true,
        category: DateTimeCategory.DATE_DIFFERENCE,
        expression: `Days until ${milestoneName}`,
        result: `${diffDays} days until ${milestoneName}`
      };
    }

    // 3. Date Offset: "date in 45 days", "date 3 weeks ago", "45 days from today", "date in 3 weeks"
    const offsetMatch = trimmed.match(/^(?:(?:what\s+(?:is|was)\s+the\s+)?date\s+(?:in\s+)?(\d+)\s+(days?|weeks?)\s*(ago)?|(\d+)\s+(days?|weeks?)\s+from\s+(?:today|now))\s*[\?\.\!]*$/i);
    if (offsetMatch) {
      const count = parseInt(offsetMatch[1] || offsetMatch[4], 10);
      const unit = (offsetMatch[2] || offsetMatch[5]).toLowerCase();
      const isAgo = Boolean(offsetMatch[3]);
      let offsetDays = count * (unit.startsWith('week') ? 7 : 1);
      if (isAgo) offsetDays = -offsetDays;

      const target = new Date(now.getTime() + offsetDays * 24 * 60 * 60 * 1000);
      const formatted = target.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
      return {
        success: true,
        category: DateTimeCategory.DATE_OFFSET,
        expression: isAgo ? `${count} ${unit} ago` : `${count} ${unit} from now`,
        result: formatted
      };
    }

    // 4. Fallback to standard current datetime
    for (const { category, regex } of LOCAL_PATTERNS) {
      if (regex.test(trimmed)) {
        let resStr = '';
        if (category === DateTimeCategory.CURRENT_TIME) {
          resStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } else if (category === DateTimeCategory.CURRENT_DATE) {
          resStr = now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        } else if (category === DateTimeCategory.CURRENT_DATETIME) {
          resStr = `${now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}, ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
        } else if (category === DateTimeCategory.CURRENT_DAY_OF_WEEK) {
          resStr = now.toLocaleDateString([], { weekday: 'long' });
        } else if (category === DateTimeCategory.CURRENT_YEAR) {
          resStr = String(now.getFullYear());
        } else if (category === DateTimeCategory.BROWSER_TIMEZONE) {
          resStr = getBrowserExposedTimezone();
        }

        if (resStr) {
          return {
            success: true,
            category,
            expression: trimmed,
            result: resStr
          };
        }
      }
    }

    return null;
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
      const calc = evaluateDateTimeCalculation(promptText);

      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: RULE_ID,
        reason: classification.reason,
        confidence: 1.0,
        metadata: {
          category: classification.category,
          browserTimezone: browserTz,
          expression: calc ? calc.expression : promptText,
          result: calc ? calc.result : null,
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
    evaluateDateTimeCalculation,
    getBrowserExposedTimezone,
    dateTimeRule
  };
});
