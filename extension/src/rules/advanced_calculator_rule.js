/**
 * Smart Query Router - Deterministic Advanced Calculator Rule
 *
 * High-performance, on-device evaluation of 5 computational domains:
 * 1. Percentages & Financial Math (percent of, percentage change, tax/discount, tip, split bill)
 * 2. Scientific & Combinatoric Math (sqrt, cbrt, factorial, nCr, nPr, log/ln, sin/cos/tan, gcd, lcm)
 * 3. Programmer Bases, Bitwise & CIDR Subnetting (hex/dec/bin/oct conversions, bitwise &, |, ^, <<, >>, CIDR masks)
 * 4. Summary Statistics on Number Lists (mean/average, median, min/max/range, standard deviation)
 * 5. Geometry & Mensuration (circle area/circumference, hypotenuse, rectangle area/perimeter)
 *
 * STRICT PROHIBITIONS:
 * - Live currency conversions (e.g. "USD to EUR") -> REJECTED (requires external live rates)
 * - Crypto & stock price queries -> REJECTED (requires external live data)
 * - Programming code implementation requests ("how to calculate in python") -> REJECTED
 * - General conversational/educational essays ("why is...") -> REJECTED
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const decisionModule = require('../shared/decision_engine');
    module.exports = factory(decisionModule);
  } else {
    const decisionModule = root.SmartQueryRouterDecision;
    root.SmartQueryRouterAdvancedCalculator = factory(decisionModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (decisionModule) {
  'use strict';

  const { DecisionOutcome, createOptimizationDecision } = decisionModule || {};

  const RULE_ID = 'RULE_LOCAL_DETERMINISTIC_ADVANCED_CALCULATOR';

  const AdvancedCalcCategory = Object.freeze({
    PERCENTAGE: 'PERCENTAGE',
    SCIENTIFIC: 'SCIENTIFIC',
    PROGRAMMER: 'PROGRAMMER',
    STATISTICS: 'STATISTICS',
    GEOMETRY: 'GEOMETRY'
  });

  // Rejections for queries requiring live market APIs
  const PROHIBITED_DYNAMIC_CURRENCY_PATTERN = /\b(usd|eur|gbp|inr|jpy|cny|cad|aud|chf|rub|krw|brl|zar|dollars?|cents?|euros?|pounds?|rupees?|yen|pesos?|btc|eth|sol|bitcoin|crypto|shares?|stocks?)\s+(?:to|in|into)\s+(usd|eur|gbp|inr|jpy|cny|cad|aud|chf|rub|krw|brl|zar|dollars?|cents?|euros?|pounds?|rupees?|yen|pesos?|btc|eth|sol|bitcoin|crypto)\b/i;
  const PROHIBITED_MARKET_DATA_PATTERN = /\b(crypto|bitcoin|btc|eth|solana|stock|stocks|share price|market cap|exchange rate)\b/i;

  // Rejections for programming code requests and explanatory essays
  const PROHIBITED_GENERAL_PATTERN = /\b(code|python|javascript|typescript|java|c\+\+|rust|golang|ruby|php|sql|class|def|function|algorithm|history|why|who|explain|proof|prove|theorem)\b/i;

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
   * Smart rounding helper to avoid floating point artifacts (e.g. 0.30000000000000004)
   * @param {number} val
   * @param {number} [maxDecimals=4]
   * @returns {number}
   */
  function smartRound(val, maxDecimals = 4) {
    if (!Number.isFinite(val)) return val;
    const factor = Math.pow(10, maxDecimals);
    return Math.round((val + Number.EPSILON) * factor) / factor;
  }

  /**
   * Safe integer factorial helper with upper bound 170
   * @param {number} n
   * @returns {number}
   */
  function factorial(n) {
    if (n < 0 || n > 170 || !Number.isInteger(n)) return NaN;
    if (n === 0 || n === 1) return 1;
    let res = 1;
    for (let i = 2; i <= n; i++) {
      res *= i;
    }
    return res;
  }

  /**
   * Greatest Common Divisor (Euclidean algorithm)
   * @param {number} a
   * @param {number} b
   * @returns {number}
   */
  function gcd(a, b) {
    a = Math.abs(a);
    b = Math.abs(b);
    while (b) {
      const temp = b;
      b = a % b;
      a = temp;
    }
    return a;
  }

  /**
   * Least Common Multiple
   * @param {number} a
   * @param {number} b
   * @returns {number}
   */
  function lcm(a, b) {
    if (a === 0 || b === 0) return 0;
    return Math.abs(a * b) / gcd(a, b);
  }

  /**
   * Converts CIDR prefix to dotted decimal subnet mask and IP counts
   * @param {number} prefix
   * @returns {{ mask: string, totalIps: number, usableIps: number }}
   */
  function cidrToMask(prefix) {
    const maskInt = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
    const octets = [
      (maskInt >>> 24) & 255,
      (maskInt >>> 16) & 255,
      (maskInt >>> 8) & 255,
      maskInt & 255
    ];
    const totalIps = Math.pow(2, 32 - prefix);
    const usableIps = prefix >= 31 ? (prefix === 31 ? 2 : 1) : Math.max(0, totalIps - 2);
    return {
      mask: octets.join('.'),
      totalIps,
      usableIps
    };
  }

  /**
   * Domain 1: Evaluate Percentages & Financial Math
   * @param {string} text
   * @returns {object|null}
   */
  function evaluatePercentages(text) {
    // 1A: Percentage of a Value: "15% of 850", "what is 20% of 1500?", "how much is 12.5% of $240"
    const pctOfMatch = text.match(/^(?:(?:what\s+(?:is|'s)|how\s+much\s+is)\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\$?)(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (pctOfMatch) {
      const pct = parseFloat(pctOfMatch[1]);
      const isDollar = pctOfMatch[2] === '$';
      const val = parseFloat(pctOfMatch[3]);
      const ans = smartRound((pct / 100) * val);
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'PERCENT_OF',
        expression: `${pct}% of ${isDollar ? '$' : ''}${val}`,
        result: `${isDollar ? '$' : ''}${ans}`
      };
    }

    // 1B: Percentage of Total: "what percentage is 45 of 200?", "what percent is 45 of 200", "45 as a percent of 200"
    const pctTotalMatch = text.match(/^(?:(?:what\s+(?:percentage|percent|%)\s+is)\s+(\$?)(\d+(?:\.\d+)?)\s+of\s+(\$?)(\d+(?:\.\d+)?)|(\$?)(\d+(?:\.\d+)?)\s+as\s+a\s+(?:percent|percentage|%)\s+of\s+(\$?)(\d+(?:\.\d+)?))\s*[\?\.\!]*$/i);
    if (pctTotalMatch) {
      const part = parseFloat(pctTotalMatch[2] || pctTotalMatch[6]);
      const total = parseFloat(pctTotalMatch[4] || pctTotalMatch[8]);
      if (total === 0) return null;
      const ans = smartRound((part / total) * 100);
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'PERCENT_OF_TOTAL',
        expression: `${part} of ${total}`,
        result: `${ans}%`
      };
    }

    // 1C: Percentage Increase / Decrease / Change: "percentage increase from 80 to 120", "percent decrease from 150 to 90"
    const pctChangeMatch = text.match(/^(?:what\s+(?:is|'s)\s+the\s+)?(?:percentage|percent|%)\s+(increase|decrease|change)\s+from\s+(\$?)(\d+(?:\.\d+)?)\s+to\s+(\$?)(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (pctChangeMatch) {
      const type = pctChangeMatch[1].toLowerCase();
      const from = parseFloat(pctChangeMatch[3]);
      const to = parseFloat(pctChangeMatch[5]);
      if (from === 0) return null;
      const diff = to - from;
      const pct = smartRound((diff / from) * 100);
      let formattedResult = '';
      if (type === 'increase') {
        formattedResult = `${pct >= 0 ? '+' : ''}${pct}%`;
      } else if (type === 'decrease') {
        formattedResult = `-${Math.abs(pct)}%`;
      } else {
        formattedResult = `${pct >= 0 ? '+' : ''}${pct}%`;
      }
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'PERCENT_CHANGE',
        expression: `${type.charAt(0).toUpperCase() + type.slice(1)} from ${from} to ${to}`,
        result: formattedResult
      };
    }

    // 1D: Tax / Discount Add & Subtract: "250 + 18%", "250 plus 18%", "120 - 20%", "120 minus 20%"
    const taxDiscountMatch = text.match(/^(?:what\s+(?:is|'s)\s+)?(\$?)(\d+(?:\.\d+)?)\s*(\+|\-|plus|minus)\s*(\d+(?:\.\d+)?)\s*%\s*[\?\.\!]*$/i);
    if (taxDiscountMatch) {
      const isDollar = taxDiscountMatch[1] === '$';
      const base = parseFloat(taxDiscountMatch[2]);
      const op = taxDiscountMatch[3].toLowerCase();
      const pct = parseFloat(taxDiscountMatch[4]);
      const isPlus = (op === '+' || op === 'plus');
      const factor = isPlus ? (1 + pct / 100) : (1 - pct / 100);
      const ans = smartRound(base * factor);
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'TAX_DISCOUNT',
        expression: `${isDollar ? '$' : ''}${base} ${isPlus ? '+' : '-'} ${pct}%`,
        result: `${isDollar ? '$' : ''}${ans}`
      };
    }

    // 1E: Tip calculation: "tip on $85 at 18%", "18% tip on $85", "tip on 85 with 18%"
    const tipMatch1 = text.match(/^(?:what\s+(?:is|'s)\s+(?:a|an)?\s*)?tip\s+on\s+(\$?)(\d+(?:\.\d+)?)\s+(?:at|with)\s+(\d+(?:\.\d+)?)\s*%\s*[\?\.\!]*$/i);
    const tipMatch2 = text.match(/^(?:what\s+(?:is|'s)\s+(?:a|an)?\s*)?(\d+(?:\.\d+)?)\s*%\s+tip\s+on\s+(\$?)(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (tipMatch1 || tipMatch2) {
      const bill = parseFloat(tipMatch1 ? tipMatch1[2] : tipMatch2[3]);
      const pct = parseFloat(tipMatch1 ? tipMatch1[3] : tipMatch2[1]);
      const tipAmount = bill * (pct / 100);
      const totalAmount = bill + tipAmount;
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'TIP',
        expression: `${pct}% tip on $${bill.toFixed(2)}`,
        result: `Tip: $${tipAmount.toFixed(2)} | Total: $${totalAmount.toFixed(2)}`
      };
    }

    // 1F: Split Bill: "split $180 by 4", "split 180 among 4", "split 180 between 4", "split 180 4 ways"
    const splitMatch = text.match(/^(?:how\s+to\s+)?split\s+(\$?)(\d+(?:\.\d+)?)\s+(?:by|among|between|into)\s+(\d+)(?:\s+(?:ways|people))?\s*[\?\.\!]*$/i) ||
      text.match(/^(?:how\s+to\s+)?split\s+(\$?)(\d+(?:\.\d+)?)\s+(\d+)\s+ways\s*[\?\.\!]*$/i);
    if (splitMatch) {
      const isDollar = splitMatch[1] === '$';
      const bill = parseFloat(splitMatch[2]);
      const people = parseInt(splitMatch[3], 10);
      if (people <= 0) return null;
      const perPerson = bill / people;
      const currency = isDollar ? '$' : '';
      return {
        success: true,
        category: AdvancedCalcCategory.PERCENTAGE,
        subCategory: 'SPLIT_BILL',
        expression: `Split ${currency}${bill} between ${people} people`,
        result: `${currency}${smartRound(perPerson).toFixed(2)} per person`
      };
    }

    return null;
  }

  /**
   * Domain 2: Evaluate Scientific & Combinatoric Math
   * @param {string} text
   * @returns {object|null}
   */
  function evaluateScientific(text) {
    // 2A: Roots: "sqrt(144)", "sqrt 144", "square root of 144", "cbrt(125)", "cube root of 125"
    const rootMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?(?:(sqrt|square\s+root(?:\s+of)?)\s*(?:\(\s*(\d+(?:\.\d+)?)\s*\)|\s+(\d+(?:\.\d+)?))|(cbrt|cube\s+root(?:\s+of)?)\s*(?:\(\s*(-?\d+(?:\.\d+)?)\s*\)|\s+(-?\d+(?:\.\d+)?)))\s*[\?\.\!]*$/i);
    if (rootMatch) {
      const isCube = Boolean(rootMatch[4]);
      if (isCube) {
        const val = parseFloat(rootMatch[5] || rootMatch[6]);
        const ans = smartRound(Math.cbrt(val));
        return {
          success: true,
          category: AdvancedCalcCategory.SCIENTIFIC,
          subCategory: 'CUBE_ROOT',
          expression: `cbrt(${val})`,
          result: `${ans}`
        };
      } else {
        const val = parseFloat(rootMatch[2] || rootMatch[3]);
        if (val < 0) return null;
        const ans = smartRound(Math.sqrt(val));
        return {
          success: true,
          category: AdvancedCalcCategory.SCIENTIFIC,
          subCategory: 'SQUARE_ROOT',
          expression: `sqrt(${val})`,
          result: `${ans}`
        };
      }
    }

    // 2B: Factorials: "5!", "factorial of 5", "5 factorial", "factorial(5)"
    const factMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?(?:(\d+)\s*!|factorial\s*(?:\(\s*(\d+)\s*\)|\s+of\s+(\d+)|\s+(\d+))|(\d+)\s+factorial)\s*[\?\.\!]*$/i);
    if (factMatch) {
      const nStr = factMatch[1] || factMatch[2] || factMatch[3] || factMatch[4] || factMatch[5];
      const n = parseInt(nStr, 10);
      if (n < 0 || n > 170) return null;
      const ans = factorial(n);
      return {
        success: true,
        category: AdvancedCalcCategory.SCIENTIFIC,
        subCategory: 'FACTORIAL',
        expression: `${n}!`,
        result: `${ans.toLocaleString('en-US')}`
      };
    }

    // 2C: Combinatorics: "nCr(5, 2)", "5 choose 2", "nPr(5, 2)"
    const combMatch = text.match(/^(?:what\s+(?:is|'s)\s+)?(?:(ncr|npr)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)|(\d+)\s*(choose|c)\s*(\d+)|(\d+)\s*p\s*(\d+))\s*[\?\.\!]*$/i);
    if (combMatch) {
      let isPerm = false;
      let n = 0;
      let r = 0;
      if (combMatch[1]) {
        isPerm = combMatch[1].toLowerCase() === 'npr';
        n = parseInt(combMatch[2], 10);
        r = parseInt(combMatch[3], 10);
      } else if (combMatch[4]) {
        n = parseInt(combMatch[4], 10);
        r = parseInt(combMatch[6], 10);
      } else if (combMatch[7]) {
        isPerm = true;
        n = parseInt(combMatch[7], 10);
        r = parseInt(combMatch[8], 10);
      }

      if (n < 0 || r < 0 || r > n || n > 170) return null;

      let ans = 0;
      if (isPerm) {
        ans = factorial(n) / factorial(n - r);
      } else {
        ans = factorial(n) / (factorial(r) * factorial(n - r));
      }

      const fnName = isPerm ? 'nPr' : 'nCr';
      return {
        success: true,
        category: AdvancedCalcCategory.SCIENTIFIC,
        subCategory: isPerm ? 'PERMUTATION' : 'COMBINATION',
        expression: `${fnName}(${n}, ${r})`,
        result: `${Math.round(ans).toLocaleString('en-US')}`
      };
    }

    // 2D: Logarithms: "log(100)", "log2(256)", "ln(e)", "log base 2 of 256"
    const logMatch = text.match(/^(?:what\s+(?:is|'s)\s+)?(?:(log10|log2|ln|log)\s*(?:\(\s*([a-zA-Z0-9\.]+)\s*\)|\s+([a-zA-Z0-9\.]+))|log\s+base\s+(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?))\s*[\?\.\!]*$/i);
    if (logMatch) {
      let fn = '';
      let argStr = '';
      let baseVal = 0;
      let argVal = 0;

      if (logMatch[1]) {
        fn = logMatch[1].toLowerCase();
        argStr = (logMatch[2] || logMatch[3]).trim().toLowerCase();
        argVal = (argStr === 'e') ? Math.E : parseFloat(argStr);
      } else if (logMatch[4]) {
        fn = 'custom';
        baseVal = parseFloat(logMatch[4]);
        argVal = parseFloat(logMatch[5]);
      }

      if (!Number.isFinite(argVal) || argVal <= 0) return null;

      let ans = 0;
      let expr = '';
      if (fn === 'log' || fn === 'log10') {
        ans = Math.log10(argVal);
        expr = `log(${argStr})`;
      } else if (fn === 'log2') {
        ans = Math.log2(argVal);
        expr = `log2(${argStr})`;
      } else if (fn === 'ln') {
        ans = Math.log(argVal);
        expr = `ln(${argStr})`;
      } else if (fn === 'custom') {
        if (baseVal <= 0 || baseVal === 1) return null;
        ans = Math.log(argVal) / Math.log(baseVal);
        expr = `log_${baseVal}(${argVal})`;
      }

      return {
        success: true,
        category: AdvancedCalcCategory.SCIENTIFIC,
        subCategory: 'LOGARITHM',
        expression: expr,
        result: `${smartRound(ans)}`
      };
    }

    // 2E: Trigonometry: "sin(90 deg)", "cos(0)", "tan(45 deg)"
    const trigMatch = text.match(/^(?:what\s+(?:is|'s)\s+)?(sin|cos|tan)\s*(?:\(\s*(-?\d+(?:\.\d+)?)\s*(deg|degrees|rad|radians|°)?\s*\)|\s+(-?\d+(?:\.\d+)?)\s*(deg|degrees|rad|radians|°)?)\s*[\?\.\!]*$/i);
    if (trigMatch) {
      const fn = trigMatch[1].toLowerCase();
      const val = parseFloat(trigMatch[2] || trigMatch[4]);
      const unit = (trigMatch[3] || trigMatch[5] || '').toLowerCase();
      const isDeg = unit === 'deg' || unit === 'degrees' || unit === '°' ||
        (unit === '' && (Math.abs(val) === 30 || Math.abs(val) === 45 || Math.abs(val) === 60 || Math.abs(val) === 90 || Math.abs(val) === 180 || Math.abs(val) === 270 || Math.abs(val) === 360));

      const rad = isDeg ? (val * Math.PI / 180) : val;
      let raw = 0;
      if (fn === 'sin') raw = Math.sin(rad);
      else if (fn === 'cos') raw = Math.cos(rad);
      else if (fn === 'tan') raw = Math.tan(rad);

      // Clean trigonometric floating artifacts (e.g. sin(180 deg) -> 0)
      if (Math.abs(raw) < 1e-12) raw = 0;
      else if (Math.abs(raw - 1) < 1e-12) raw = 1;
      else if (Math.abs(raw + 1) < 1e-12) raw = -1;

      return {
        success: true,
        category: AdvancedCalcCategory.SCIENTIFIC,
        subCategory: 'TRIGONOMETRY',
        expression: `${fn}(${val}${isDeg ? '°' : ' rad'})`,
        result: `${smartRound(raw)}`
      };
    }

    // 2F: Number Theory: "gcd(24, 36)", "gcd of 24 and 36", "lcm(12, 18)"
    const numTheoryMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?(?:(gcd|greatest\s+common\s+divisor)(?:\s+of)?\s*(?:\(\s*(\d+)\s*,\s*(\d+)\s*\)|\s+(\d+)\s+(?:and|,)\s+(\d+))|(lcm|least\s+common\s+multiple)(?:\s+of)?\s*(?:\(\s*(\d+)\s*,\s*(\d+)\s*\)|\s+(\d+)\s+(?:and|,)\s+(\d+)))\s*[\?\.\!]*$/i);
    if (numTheoryMatch) {
      const isGcd = Boolean(numTheoryMatch[1]);
      const a = parseInt(numTheoryMatch[2] || numTheoryMatch[4] || numTheoryMatch[7] || numTheoryMatch[9], 10);
      const b = parseInt(numTheoryMatch[3] || numTheoryMatch[5] || numTheoryMatch[8] || numTheoryMatch[10], 10);
      if (!Number.isFinite(a) || !Number.isFinite(b)) return null;

      const ans = isGcd ? gcd(a, b) : lcm(a, b);
      const fnName = isGcd ? 'gcd' : 'lcm';
      return {
        success: true,
        category: AdvancedCalcCategory.SCIENTIFIC,
        subCategory: isGcd ? 'GCD' : 'LCM',
        expression: `${fnName}(${a}, ${b})`,
        result: `${ans}`
      };
    }

    return null;
  }

  /**
   * Domain 3: Evaluate Programmer Base Conversions, Bitwise & CIDR
   * @param {string} text
   * @returns {object|null}
   */
  function evaluateProgrammer(text) {
    // 3A: CIDR Subnet Masks: "/24 subnet mask", "subnet mask for /24", "/16 mask"
    const cidrMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?(?:subnet\s+mask\s+(?:for\s+)?\/(\d{1,2})|\/(\d{1,2})\s+(?:subnet\s+mask|mask))\s*[\?\.\!]*$/i);
    if (cidrMatch) {
      const prefix = parseInt(cidrMatch[1] || cidrMatch[2], 10);
      if (prefix < 0 || prefix > 32) return null;
      const maskInfo = cidrToMask(prefix);
      return {
        success: true,
        category: AdvancedCalcCategory.PROGRAMMER,
        subCategory: 'CIDR_SUBNET',
        expression: `/${prefix} Subnet Mask`,
        result: `${maskInfo.mask} (${maskInfo.totalIps.toLocaleString('en-US')} IPs, ${maskInfo.usableIps.toLocaleString('en-US')} usable)`
      };
    }

    // 3B: Base Conversions:
    // "hex to dec 0xFF", "0xFF to decimal", "42 to binary", "bin to dec 1101", "dec to hex 255"
    const baseConvertMatch1 = text.match(/^(?:convert\s+)?(hex|bin|oct|dec)\s+to\s+(hex|bin|oct|dec)\s+([0-9a-fA-FxXbBoO]+)\s*[\?\.\!]*$/i);
    const baseConvertMatch2 = text.match(/^(?:convert\s+)?([0-9a-fA-FxXbBoO]+)\s+(?:to|in)\s+(hex|bin|oct|dec|decimal|binary|hexadecimal|octal)\s*[\?\.\!]*$/i);
    if (baseConvertMatch1 || baseConvertMatch2) {
      let fromBase = 10;
      let toBase = 10;
      let rawVal = '';

      if (baseConvertMatch1) {
        const fromStr = baseConvertMatch1[1].toLowerCase();
        const toStr = baseConvertMatch1[2].toLowerCase();
        rawVal = baseConvertMatch1[3];
        fromBase = fromStr === 'hex' ? 16 : (fromStr === 'bin' ? 2 : (fromStr === 'oct' ? 8 : 10));
        toBase = toStr === 'hex' ? 16 : (toStr === 'bin' ? 2 : (toStr === 'oct' ? 8 : 10));
      } else {
        rawVal = baseConvertMatch2[1];
        const targetStr = baseConvertMatch2[2].toLowerCase();
        if (rawVal.toLowerCase().startsWith('0x')) fromBase = 16;
        else if (rawVal.toLowerCase().startsWith('0b')) fromBase = 2;
        else if (rawVal.toLowerCase().startsWith('0o')) fromBase = 8;
        else fromBase = 10;

        if (targetStr.startsWith('hex')) toBase = 16;
        else if (targetStr.startsWith('bin')) toBase = 2;
        else if (targetStr.startsWith('oct')) toBase = 8;
        else toBase = 10;
      }

      // Strip prefix if necessary for parseInt
      let cleanVal = rawVal.toLowerCase();
      if (cleanVal.startsWith('0x')) cleanVal = cleanVal.slice(2);
      else if (cleanVal.startsWith('0b')) cleanVal = cleanVal.slice(2);
      else if (cleanVal.startsWith('0o')) cleanVal = cleanVal.slice(2);

      const parsedNum = parseInt(cleanVal, fromBase);
      if (!Number.isFinite(parsedNum) || isNaN(parsedNum)) return null;

      let formattedAns = '';
      if (toBase === 16) formattedAns = `0x${parsedNum.toString(16).toUpperCase()}`;
      else if (toBase === 2) formattedAns = `0b${parsedNum.toString(2)}`;
      else if (toBase === 8) formattedAns = `0o${parsedNum.toString(8)}`;
      else formattedAns = `${parsedNum}`;

      return {
        success: true,
        category: AdvancedCalcCategory.PROGRAMMER,
        subCategory: 'BASE_CONVERSION',
        expression: `${rawVal}`,
        result: `${formattedAns}`
      };
    }

    // 3C: Bitwise Logic: "0xFF & 0x0F", "1 << 10", "1024 >> 2", "0xF0 | 0x0F", "0xFF ^ 0x0F"
    const bitwiseMatch = text.match(/^((?:0x[0-9a-fA-F]+|0b[01]+|\d+))\s*(&|\||\^|<<|>>)\s*((?:0x[0-9a-fA-F]+|0b[01]+|\d+))\s*[\?\.\!]*$/);
    if (bitwiseMatch) {
      const op1Str = bitwiseMatch[1];
      const opStr = bitwiseMatch[2];
      const op2Str = bitwiseMatch[3];

      const op1 = parseInt(op1Str, op1Str.startsWith('0x') ? 16 : (op1Str.startsWith('0b') ? 2 : 10));
      const op2 = parseInt(op2Str, op2Str.startsWith('0x') ? 16 : (op2Str.startsWith('0b') ? 2 : 10));

      if (isNaN(op1) || isNaN(op2)) return null;

      let res = 0;
      if (opStr === '&') res = op1 & op2;
      else if (opStr === '|') res = op1 | op2;
      else if (opStr === '^') res = op1 ^ op2;
      else if (opStr === '<<') res = op1 << op2;
      else if (opStr === '>>') res = op1 >> op2;

      const isHex = op1Str.toLowerCase().startsWith('0x') || op2Str.toLowerCase().startsWith('0x');
      let hexStr = (res >>> 0).toString(16).toUpperCase();
      if (hexStr.length % 2 !== 0 && (op1Str.length >= 4 || op2Str.length >= 4)) {
        hexStr = '0' + hexStr;
      }
      const formattedRes = isHex
        ? `${res} (0x${hexStr})`
        : `${res}`;

      return {
        success: true,
        category: AdvancedCalcCategory.PROGRAMMER,
        subCategory: 'BITWISE',
        expression: `${op1Str} ${opStr} ${op2Str}`,
        result: formattedRes
      };
    }

    return null;
  }

  /**
   * Domain 4: Evaluate Summary Statistics on Lists
   * @param {string} text
   * @returns {object|null}
   */
  function evaluateStatistics(text) {
    // "mean of 12, 18, 24, 30", "average of 10, 20, 30", "median of 5, 2, 9, 1, 7", "min and max of 45, 12, 89, 3", "std dev of 4, 8, 6, 5, 3"
    const statMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?(mean|average|median|min\s+and\s+max|range|standard\s+deviation|std\s+dev|stdev)\s+of\s+([-\d\s\.,]+)\s*[\?\.\!]*$/i);
    if (!statMatch) return null;

    const statType = statMatch[1].toLowerCase();
    const rawList = statMatch[2].trim();

    // Extract numbers safely
    const tokens = rawList.split(/[,\s]+/).filter(t => t.length > 0);
    if (tokens.length < 2) return null;

    const nums = [];
    for (const tok of tokens) {
      const n = parseFloat(tok);
      if (!Number.isFinite(n)) return null;
      nums.push(n);
    }

    const nLen = nums.length;

    // Mean / Average
    if (statType === 'mean' || statType === 'average') {
      const sum = nums.reduce((a, b) => a + b, 0);
      const mean = smartRound(sum / nLen);
      return {
        success: true,
        category: AdvancedCalcCategory.STATISTICS,
        subCategory: 'MEAN',
        expression: `Average of [${nums.join(', ')}]`,
        result: `${mean}`
      };
    }

    // Median
    if (statType === 'median') {
      const sorted = [...nums].sort((a, b) => a - b);
      const mid = Math.floor(nLen / 2);
      const med = nLen % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
      return {
        success: true,
        category: AdvancedCalcCategory.STATISTICS,
        subCategory: 'MEDIAN',
        expression: `Median of [${nums.join(', ')}]`,
        result: `${smartRound(med)}`
      };
    }

    // Min and Max / Range
    if (statType === 'min and max' || statType === 'range') {
      const min = Math.min(...nums);
      const max = Math.max(...nums);
      const range = smartRound(max - min);
      return {
        success: true,
        category: AdvancedCalcCategory.STATISTICS,
        subCategory: 'MIN_MAX',
        expression: `Range of [${nums.join(', ')}]`,
        result: `Min: ${min} | Max: ${max} | Range: ${range}`
      };
    }

    // Standard Deviation
    if (statType === 'standard deviation' || statType === 'std dev' || statType === 'stdev') {
      const mean = nums.reduce((a, b) => a + b, 0) / nLen;
      const variance = nums.reduce((sum, x) => sum + Math.pow(x - mean, 2), 0) / (nLen - 1);
      const std = smartRound(Math.sqrt(variance));
      return {
        success: true,
        category: AdvancedCalcCategory.STATISTICS,
        subCategory: 'STANDARD_DEVIATION',
        expression: `Sample Std Dev of [${nums.join(', ')}]`,
        result: `${std}`
      };
    }

    return null;
  }

  /**
   * Domain 5: Evaluate Geometry & Mensuration
   * @param {string} text
   * @returns {object|null}
   */
  function evaluateGeometry(text) {
    // 5A: Circle Area: "area of circle radius 5", "area of a circle with radius 5", "area of circle r=5"
    const circleAreaMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?area\s+of\s+(?:a\s+)?circle\s+(?:with\s+)?(?:radius|r\s*=)\s*(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (circleAreaMatch) {
      const r = parseFloat(circleAreaMatch[1]);
      const area = smartRound(Math.PI * r * r);
      return {
        success: true,
        category: AdvancedCalcCategory.GEOMETRY,
        subCategory: 'CIRCLE_AREA',
        expression: `Area of circle (r = ${r})`,
        result: `${area}`
      };
    }

    // Circle Circumference: "circumference of circle radius 10", "circumference of a circle with radius 10"
    const circleCircMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?circumference\s+of\s+(?:a\s+)?circle\s+(?:with\s+)?(?:radius|r\s*=)\s*(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (circleCircMatch) {
      const r = parseFloat(circleCircMatch[1]);
      const circ = smartRound(2 * Math.PI * r);
      return {
        success: true,
        category: AdvancedCalcCategory.GEOMETRY,
        subCategory: 'CIRCLE_CIRCUMFERENCE',
        expression: `Circumference of circle (r = ${r})`,
        result: `${circ}`
      };
    }

    // 5B: Hypotenuse: "hypotenuse of 3 and 4", "hypotenuse with legs 3 and 4"
    const hypMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?hypotenuse\s+(?:of|with\s+legs)?\s*(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s*[\?\.\!]*$/i);
    if (hypMatch) {
      const a = parseFloat(hypMatch[1]);
      const b = parseFloat(hypMatch[2]);
      const hyp = smartRound(Math.hypot(a, b));
      return {
        success: true,
        category: AdvancedCalcCategory.GEOMETRY,
        subCategory: 'HYPOTENUSE',
        expression: `Hypotenuse of sides ${a} and ${b}`,
        result: `${hyp}`
      };
    }

    // 5C: Rectangle Area & Perimeter: "area of rectangle 12 by 8", "area of rectangle 12 x 8"
    const rectAreaMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?area\s+of\s+(?:a\s+)?rectangle\s+(?:with\s+width\s+(\d+(?:\.\d+)?)\s+and\s+height\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:by|x|and)\s*(\d+(?:\.\d+)?))\s*[\?\.\!]*$/i);
    if (rectAreaMatch) {
      const w = parseFloat(rectAreaMatch[1] || rectAreaMatch[3]);
      const h = parseFloat(rectAreaMatch[2] || rectAreaMatch[4]);
      const area = smartRound(w * h);
      return {
        success: true,
        category: AdvancedCalcCategory.GEOMETRY,
        subCategory: 'RECTANGLE_AREA',
        expression: `Area of rectangle (${w} × ${h})`,
        result: `${area}`
      };
    }

    const rectPerimMatch = text.match(/^(?:what\s+(?:is|'s)\s+(?:the\s+)?)?perimeter\s+of\s+(?:a\s+)?rectangle\s+(?:with\s+width\s+(\d+(?:\.\d+)?)\s+and\s+height\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:by|x|and)\s*(\d+(?:\.\d+)?))\s*[\?\.\!]*$/i);
    if (rectPerimMatch) {
      const w = parseFloat(rectPerimMatch[1] || rectPerimMatch[3]);
      const h = parseFloat(rectPerimMatch[2] || rectPerimMatch[4]);
      const perim = smartRound(2 * (w + h));
      return {
        success: true,
        category: AdvancedCalcCategory.GEOMETRY,
        subCategory: 'RECTANGLE_PERIMETER',
        expression: `Perimeter of rectangle (${w} × ${h})`,
        result: `${perim}`
      };
    }

    return null;
  }

  /**
   * Main evaluation function for on-device advanced calculations
   * @param {string} rawPrompt
   * @returns {{
   *   success: boolean,
   *   category: string,
   *   subCategory: string,
   *   expression: string,
   *   result: string
   * } | null}
   */
  function evaluateAdvancedCalculation(rawPrompt) {
    if (typeof rawPrompt !== 'string') return null;
    const cleaned = sanitizeString(rawPrompt);
    if (cleaned.length === 0 || cleaned.length > 100) return null;

    // Strict boundary checks: reject currency conversions, crypto/stocks, or coding/essays
    if (PROHIBITED_DYNAMIC_CURRENCY_PATTERN.test(cleaned)) return null;
    if (PROHIBITED_MARKET_DATA_PATTERN.test(cleaned)) return null;
    if (PROHIBITED_GENERAL_PATTERN.test(cleaned)) return null;

    // 1. Percentages & Financial Math
    const pctRes = evaluatePercentages(cleaned);
    if (pctRes) return pctRes;

    // 2. Scientific & Combinatorics
    const sciRes = evaluateScientific(cleaned);
    if (sciRes) return sciRes;

    // 3. Programmer Bases, Bitwise & CIDR
    const progRes = evaluateProgrammer(cleaned);
    if (progRes) return progRes;

    // 4. Statistics
    const statRes = evaluateStatistics(cleaned);
    if (statRes) return statRes;

    // 5. Geometry
    const geomRes = evaluateGeometry(cleaned);
    if (geomRes) return geomRes;

    return null;
  }

  /**
   * Decision Engine Rule Plugin
   */
  const advancedCalculatorRule = {
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

      const calc = evaluateAdvancedCalculation(promptText);
      if (!calc || !calc.success) {
        return null;
      }

      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: RULE_ID,
        reason: `Eligible: matched deterministic ${calc.category.toLowerCase()} calculation (${calc.expression} = ${calc.result}) resolvable on-device.`,
        confidence: 1.0,
        metadata: {
          category: calc.category,
          subCategory: calc.subCategory,
          expression: calc.expression,
          result: calc.result,
          resolutionSource: 'ON_DEVICE_ADVANCED_CALCULATOR'
        }
      });
    }
  };

  return {
    RULE_ID,
    AdvancedCalcCategory,
    smartRound,
    factorial,
    gcd,
    lcm,
    cidrToMask,
    evaluatePercentages,
    evaluateScientific,
    evaluateProgrammer,
    evaluateStatistics,
    evaluateGeometry,
    evaluateAdvancedCalculation,
    advancedCalculatorRule
  };
});
