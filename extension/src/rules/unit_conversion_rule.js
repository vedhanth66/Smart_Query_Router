/**
 * Smart Query Router - Deterministic Unit & Temperature Conversion Rule
 *
 * Conservative, on-device evaluation of standard physical & digital unit conversions:
 * - Temperature: Fahrenheit (F, °F), Celsius (C, °C), Kelvin (K)
 * - Length / Distance: mm, cm, m, km, in, ft, yd, mi, nmi
 * - Mass / Weight: mg, g, kg, tonne/t, oz, lb/lbs, stone, ton
 * - Speed: km/h, mph, m/s, knots
 * - Digital Storage: bits, bytes, KB, MB, GB, TB, PB
 * - Volume: ml, l, tsp, tbsp, fl oz, cups, pt, qt, gal
 * - Time: ms, s, min, hr, days, weeks, years
 *
 * STRICT PROHIBITIONS:
 * - Currency (USD, EUR, INR, etc.) -> requires external exchange rates
 * - Crypto (BTC, ETH, etc.) -> requires live data
 * - Complex or code questions -> passes to Claude
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const decisionModule = require('../shared/decision_engine');
    module.exports = factory(decisionModule);
  } else {
    const decisionModule = root.SmartQueryRouterDecision;
    root.SmartQueryRouterUnitConversion = factory(decisionModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (decisionModule) {
  'use strict';

  const { DecisionOutcome, createOptimizationDecision } = decisionModule;

  const RULE_ID = 'RULE_LOCAL_DETERMINISTIC_UNIT_CONVERSION';

  const UnitCategory = Object.freeze({
    TEMPERATURE: 'TEMPERATURE',
    LENGTH: 'LENGTH',
    MASS: 'MASS',
    SPEED: 'SPEED',
    DIGITAL_STORAGE: 'DIGITAL_STORAGE',
    VOLUME: 'VOLUME',
    TIME: 'TIME'
  });

  // Currency & crypto keywords that must be strictly disqualified from local conversion
  const PROHIBITED_DYNAMIC_UNITS_PATTERN = /\b(usd|eur|gbp|inr|jpy|cny|cad|aud|chf|rub|krw|brl|zar|dollars?|cents?|euros?|pounds?|rupees?|yen|pesos?|btc|eth|sol|bitcoin|crypto|shares?|stocks?)\b|[\$\€\£\¥\₹]/i;

  // Words that indicate substantive non-conversion questions
  const PROHIBITED_GENERAL_PATTERN = /\b(code|python|javascript|function|table|history|why|who|explain|how\s+do|how\s+can|how\s+to|what\s+does|meaning|definition)\b/i;

  /**
   * Unit definition table
   */
  const UNITS = {
    // 1. TEMPERATURE (custom formulas)
    temperature: {
      category: UnitCategory.TEMPERATURE,
      aliases: {
        'c': 'c',
        'celsius': 'c',
        'centigrade': 'c',
        '°c': 'c',
        'deg c': 'c',
        'degrees c': 'c',
        'degrees celsius': 'c',
        'f': 'f',
        'fahrenheit': 'f',
        '°f': 'f',
        'deg f': 'f',
        'degrees f': 'f',
        'degrees fahrenheit': 'f',
        'k': 'k',
        'kelvin': 'k',
        '°k': 'k',
        'degrees kelvin': 'k'
      },
      symbols: {
        'c': '°C',
        'f': '°F',
        'k': 'K'
      }
    },

    // 2. LENGTH (base unit: meter)
    length: {
      category: UnitCategory.LENGTH,
      base: 'm',
      rates: {
        'mm': 0.001,
        'cm': 0.01,
        'm': 1.0,
        'km': 1000.0,
        'in': 0.0254,
        'ft': 0.3048,
        'yd': 0.9144,
        'mi': 1609.344,
        'nmi': 1852.0
      },
      aliases: {
        'mm': 'mm', 'millimeter': 'mm', 'millimeters': 'mm', 'millimetre': 'mm', 'millimetres': 'mm',
        'cm': 'cm', 'centimeter': 'cm', 'centimeters': 'cm', 'centimetre': 'cm', 'centimetres': 'cm',
        'm': 'm', 'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
        'km': 'km', 'kilometer': 'km', 'kilometers': 'km', 'kilometre': 'km', 'kilometres': 'km',
        'in': 'in', 'inch': 'in', 'inches': 'in', '"': 'in',
        'ft': 'ft', 'foot': 'ft', 'feet': 'ft', "'": 'ft',
        'yd': 'yd', 'yard': 'yd', 'yards': 'yd',
        'mi': 'mi', 'mile': 'mi', 'miles': 'mi',
        'nmi': 'nmi', 'nautical mile': 'nmi', 'nautical miles': 'nmi'
      },
      symbols: {
        'mm': 'mm', 'cm': 'cm', 'm': 'm', 'km': 'km',
        'in': 'in', 'ft': 'ft', 'yd': 'yd', 'mi': 'miles', 'nmi': 'nmi'
      }
    },

    // 3. MASS / WEIGHT (base unit: gram)
    mass: {
      category: UnitCategory.MASS,
      base: 'g',
      rates: {
        'mg': 0.001,
        'g': 1.0,
        'kg': 1000.0,
        'tonne': 1000000.0,
        'oz': 28.349523125,
        'lb': 453.59237,
        'stone': 6350.29318,
        'ton': 907184.74
      },
      aliases: {
        'mg': 'mg', 'milligram': 'mg', 'milligrams': 'mg',
        'g': 'g', 'gram': 'g', 'grams': 'g',
        'kg': 'kg', 'kilogram': 'kg', 'kilograms': 'kg', 'kilo': 'kg', 'kilos': 'kg',
        'tonne': 'tonne', 'tonnes': 'tonne', 'metric ton': 'tonne', 'metric tons': 'tonne', 't': 'tonne',
        'oz': 'oz', 'ounce': 'oz', 'ounces': 'oz',
        'lb': 'lb', 'lbs': 'lb', 'pound': 'lb', 'pounds': 'lb',
        'stone': 'stone', 'stones': 'stone', 'st': 'stone',
        'ton': 'ton', 'tons': 'ton', 'short ton': 'ton', 'short tons': 'ton'
      },
      symbols: {
        'mg': 'mg', 'g': 'g', 'kg': 'kg', 'tonne': 'tonnes',
        'oz': 'oz', 'lb': 'lbs', 'stone': 'stone', 'ton': 'tons'
      }
    },

    // 4. SPEED (base unit: m/s)
    speed: {
      category: UnitCategory.SPEED,
      base: 'mps',
      rates: {
        'mps': 1.0,
        'kmh': 1 / 3.6,
        'mph': 0.44704,
        'knot': 0.514444
      },
      aliases: {
        'm/s': 'mps', 'mps': 'mps', 'meters per second': 'mps', 'meter per second': 'mps',
        'km/h': 'kmh', 'kmh': 'kmh', 'kph': 'kmh', 'km/hr': 'kmh', 'kilometers per hour': 'kmh', 'kilometer per hour': 'kmh',
        'mph': 'mph', 'mi/h': 'mph', 'miles per hour': 'mph', 'mile per hour': 'mph',
        'knot': 'knot', 'knots': 'knot', 'kt': 'knot'
      },
      symbols: {
        'mps': 'm/s', 'kmh': 'km/h', 'mph': 'mph', 'knot': 'knots'
      }
    },

    // 5. DIGITAL STORAGE (base unit: bytes)
    storage: {
      category: UnitCategory.DIGITAL_STORAGE,
      base: 'B',
      rates: {
        'b': 0.125,
        'B': 1.0,
        'KB': 1024,
        'MB': 1024 * 1024,
        'GB': 1024 * 1024 * 1024,
        'TB': 1024 * 1024 * 1024 * 1024,
        'PB': 1024 * 1024 * 1024 * 1024 * 1024
      },
      aliases: {
        'bit': 'b', 'bits': 'b', 'b': 'b',
        'byte': 'B', 'bytes': 'B',
        'kb': 'KB', 'kilobyte': 'KB', 'kilobytes': 'KB',
        'mb': 'MB', 'megabyte': 'MB', 'megabytes': 'MB',
        'gb': 'GB', 'gigabyte': 'GB', 'gigabytes': 'GB',
        'tb': 'TB', 'terabyte': 'TB', 'terabytes': 'TB',
        'pb': 'PB', 'petabyte': 'PB', 'petabytes': 'PB'
      },
      symbols: {
        'b': 'bits', 'B': 'B', 'KB': 'KB', 'MB': 'MB', 'GB': 'GB', 'TB': 'TB', 'PB': 'PB'
      }
    },

    // 6. VOLUME (base unit: liter)
    volume: {
      category: UnitCategory.VOLUME,
      base: 'l',
      rates: {
        'ml': 0.001,
        'l': 1.0,
        'tsp': 0.00492892,
        'tbsp': 0.0147868,
        'floz': 0.0295735,
        'cup': 0.236588,
        'pt': 0.473176,
        'qt': 0.946353,
        'gal': 3.78541
      },
      aliases: {
        'ml': 'ml', 'milliliter': 'ml', 'milliliters': 'ml', 'millilitre': 'ml', 'millilitres': 'ml',
        'l': 'l', 'liter': 'l', 'liters': 'l', 'litre': 'l', 'litres': 'l',
        'tsp': 'tsp', 'teaspoon': 'tsp', 'teaspoons': 'tsp',
        'tbsp': 'tbsp', 'tablespoon': 'tbsp', 'tablespoons': 'tbsp',
        'fl oz': 'floz', 'floz': 'floz', 'fluid ounce': 'floz', 'fluid ounces': 'floz',
        'cup': 'cup', 'cups': 'cup',
        'pt': 'pt', 'pint': 'pt', 'pints': 'pt',
        'qt': 'qt', 'quart': 'qt', 'quarts': 'qt',
        'gal': 'gal', 'gallon': 'gal', 'gallons': 'gal'
      },
      symbols: {
        'ml': 'ml', 'l': 'L', 'tsp': 'tsp', 'tbsp': 'tbsp', 'floz': 'fl oz',
        'cup': 'cups', 'pt': 'pints', 'qt': 'quarts', 'gal': 'gallons'
      }
    },

    // 7. TIME (base unit: second)
    time: {
      category: UnitCategory.TIME,
      base: 's',
      rates: {
        'ms': 0.001,
        's': 1.0,
        'min': 60.0,
        'hr': 3600.0,
        'day': 86400.0,
        'wk': 604800.0,
        'yr': 31536000.0
      },
      aliases: {
        'ms': 'ms', 'millisecond': 'ms', 'milliseconds': 'ms',
        's': 's', 'sec': 's', 'second': 's', 'seconds': 's',
        'min': 'min', 'mins': 'min', 'minute': 'min', 'minutes': 'min',
        'h': 'hr', 'hr': 'hr', 'hrs': 'hr', 'hour': 'hr', 'hours': 'hr',
        'd': 'day', 'day': 'day', 'days': 'day',
        'wk': 'wk', 'week': 'wk', 'weeks': 'wk',
        'yr': 'yr', 'year': 'yr', 'years': 'yr'
      },
      symbols: {
        'ms': 'ms', 's': 'seconds', 'min': 'minutes', 'hr': 'hours',
        'day': 'days', 'wk': 'weeks', 'yr': 'years'
      }
    }
  };

  /**
   * Resolves a raw unit string into its category, standard canonical ID, and display symbol
   * @param {string} rawUnitStr
   * @returns {{ category: string, id: string, symbol: string, tableKey: string } | null}
   */
  function resolveUnit(rawUnitStr) {
    if (!rawUnitStr || typeof rawUnitStr !== 'string') return null;
    const norm = rawUnitStr.trim().toLowerCase().replace(/[°]/g, '').trim();
    const rawWithDegree = rawUnitStr.trim().toLowerCase();

    for (const [tableKey, table] of Object.entries(UNITS)) {
      // Check exact alias
      let canonicalId = table.aliases[norm] || table.aliases[rawWithDegree];
      if (!canonicalId) {
        // Try trimming trailing punctuation
        const stripped = norm.replace(/[\.\,\/]+$/, '');
        canonicalId = table.aliases[stripped];
      }

      if (canonicalId) {
        return {
          category: table.category,
          tableKey,
          id: canonicalId,
          symbol: table.symbols[canonicalId] || canonicalId
        };
      }
    }

    return null;
  }

  /**
   * Smart rounding helper to avoid floating-point artifacts (e.g. 0.30000000000000004)
   * Formats cleanly to 2–4 decimal places without trailing zeros.
   * @param {number} val
   * @returns {number}
   */
  function smartRound(val) {
    if (!Number.isFinite(val)) return val;
    const absVal = Math.abs(val);

    let places = 2;
    if (absVal === 0) return 0;
    if (absVal < 0.01) places = 6;
    else if (absVal < 1) places = 4;
    else if (absVal < 100) places = 3;
    else places = 2;

    const factor = Math.pow(10, places);
    return Math.round(val * factor) / factor;
  }

  /**
   * Converts a numeric value between temperature units
   * @param {number} value
   * @param {string} fromId ('c', 'f', 'k')
   * @param {string} toId ('c', 'f', 'k')
   * @returns {number}
   */
  function convertTemperature(value, fromId, toId) {
    if (fromId === toId) return value;

    // Convert from source to Kelvin
    let kelvin = 0;
    if (fromId === 'c') {
      kelvin = value + 273.15;
    } else if (fromId === 'f') {
      kelvin = (value - 32) * (5 / 9) + 273.15;
    } else {
      kelvin = value;
    }

    // Convert Kelvin to target
    if (toId === 'c') {
      return kelvin - 273.15;
    } else if (toId === 'f') {
      return (kelvin - 273.15) * (9 / 5) + 32;
    } else {
      return kelvin;
    }
  }

  /**
   * Converts a numeric value between ratio-based units
   * @param {number} value
   * @param {object} table
   * @param {string} fromId
   * @param {string} toId
   * @returns {number}
   */
  function convertRatioUnits(value, table, fromId, toId) {
    if (fromId === toId) return value;
    const fromRate = table.rates[fromId];
    const toRate = table.rates[toId];
    if (!fromRate || !toRate) return NaN;

    // Value in base unit
    const inBase = value * fromRate;
    // Value in target unit
    return inBase / toRate;
  }

  /**
   * Sanitizes input string (strips zero-width chars, non-breaking spaces, multi-whitespace)
   * @param {string} str
   * @returns {string}
   */
  function sanitizeString(str) {
    if (typeof str !== 'string') return '';
    return str
      .replace(/[\u200B-\u200D\uFEFF\u00AD\u2060\u180E]/g, '')
      .replace(/\u00A0/g, ' ')
      .replace(/[\r\n\t]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /**
   * Main evaluation function for on-device unit conversions
   *
   * @param {string} rawPrompt
   * @returns {{
   *   success: boolean,
   *   expression: string,
   *   result: string,
   *   fromValue: number,
   *   toValue: number,
   *   fromUnit: string,
   *   toUnit: string,
   *   category: string
   * } | null}
   */
  function evaluateUnitConversion(rawPrompt) {
    if (typeof rawPrompt !== 'string') return null;
    const cleaned = sanitizeString(rawPrompt);
    if (cleaned.length === 0 || cleaned.length > 80) return null;

    // Reject currency, crypto, or programming / explanatory queries
    if (PROHIBITED_DYNAMIC_UNITS_PATTERN.test(cleaned)) return null;
    if (PROHIBITED_GENERAL_PATTERN.test(cleaned)) return null;

    let fromVal = NaN;
    let rawFromUnit = '';
    let rawToUnit = '';

    // Pattern 1: Direct conversion
    // e.g. "100 F to C", "15 km in miles", "convert 6 feet into cm", "what is 70 kg in lbs", "15km to miles", "32°F in °C"
    const directMatch = cleaned.match(/^(?:(?:what\s+(?:is|'s)|how\s+much\s+is|convert)\s+)?(-?\d+(?:\.\d+)?)\s*([a-zA-Z°\'\"\/]+(?:\s+[a-zA-Z]+)?)\s+(?:to|in|into|as)\s+([a-zA-Z°\'\"\/]+(?:\s+[a-zA-Z]+)?)\s*[\?\.\!]*$/i);

    if (directMatch) {
      fromVal = parseFloat(directMatch[1]);
      rawFromUnit = directMatch[2].trim();
      rawToUnit = directMatch[3].trim();
    } else {
      // Pattern 2: Inverted question
      // e.g. "how many miles in 15 km?", "how many cm is 6 feet?", "how many grams are in 5 lbs?"
      const invertedMatch = cleaned.match(/^(?:how\s+many)\s+(.+?)\s+(?:are\s+there\s+in|are\s+in|are\s+there|in|is|are)\s+(-?\d+(?:\.\d+)?)\s*(.+?)\s*[\?\.\!]*$/i);
      if (invertedMatch) {
        rawToUnit = invertedMatch[1].trim();
        fromVal = parseFloat(invertedMatch[2]);
        rawFromUnit = invertedMatch[3].trim();
      }
    }

    if (!Number.isFinite(fromVal) || !rawFromUnit || !rawToUnit) {
      return null;
    }

    const resolvedFrom = resolveUnit(rawFromUnit);
    const resolvedTo = resolveUnit(rawToUnit);

    if (!resolvedFrom || !resolvedTo) return null;

    // Both units must belong to the exact same physical category
    if (resolvedFrom.category !== resolvedTo.category) {
      return null;
    }

    const category = resolvedFrom.category;
    let convertedValue = NaN;

    if (category === UnitCategory.TEMPERATURE) {
      convertedValue = convertTemperature(fromVal, resolvedFrom.id, resolvedTo.id);
    } else {
      const table = UNITS[resolvedFrom.tableKey];
      if (!table) return null;
      convertedValue = convertRatioUnits(fromVal, table, resolvedFrom.id, resolvedTo.id);
    }

    if (!Number.isFinite(convertedValue)) {
      return null;
    }

    const roundedResult = smartRound(convertedValue);
    const displayFromVal = smartRound(fromVal);

    // Format clean expression & result
    const fromSymbol = resolvedFrom.symbol;
    const toSymbol = resolvedTo.symbol;

    const expressionStr = `${displayFromVal} ${fromSymbol}`.trim();
    const resultStr = `${roundedResult} ${toSymbol}`.trim();

    return {
      success: true,
      expression: expressionStr,
      result: resultStr,
      fromValue: displayFromVal,
      toValue: roundedResult,
      fromUnit: fromSymbol,
      toUnit: toSymbol,
      category
    };
  }

  /**
   * Optimization Decision Engine Rule Plugin
   */
  const unitConversionRule = {
    id: RULE_ID,
    evaluate(queryEvent) {
      if (!queryEvent || !queryEvent.content || (!queryEvent.content.rawPrompt && !queryEvent.content.normalizedPrompt)) {
        return null;
      }

      if (queryEvent.privacy && queryEvent.privacy.level === 'SENSITIVE') {
        return null;
      }

      const promptText = queryEvent.content.normalizedPrompt !== undefined
        ? queryEvent.content.normalizedPrompt
        : queryEvent.content.rawPrompt;

      const conversion = evaluateUnitConversion(promptText);
      if (!conversion || !conversion.success) {
        return null;
      }

      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: RULE_ID,
        reason: `Eligible: matched deterministic ${conversion.category.toLowerCase()} unit conversion (${conversion.expression} = ${conversion.result}) resolvable on-device.`,
        confidence: 1.0,
        metadata: {
          category: conversion.category,
          expression: conversion.expression,
          result: conversion.result,
          fromValue: conversion.fromValue,
          toValue: conversion.toValue,
          fromUnit: conversion.fromUnit,
          toUnit: conversion.toUnit,
          resolutionSource: 'ON_DEVICE_UNIT_CONVERTER'
        }
      });
    }
  };

  return {
    RULE_ID,
    UnitCategory,
    resolveUnit,
    smartRound,
    evaluateUnitConversion,
    unitConversionRule
  };
});
