/**
 * Smart Query Router - Deterministic Arithmetic Rule
 * Narrowly scoped, conservative local evaluation for genuinely deterministic arithmetic expressions.
 * Strictly avoids eval() and rejects:
 * - Natural language reasoning and word problems
 * - Unit conversions or currency
 * - Algebraic variables or equations
 * - Ambiguous syntax, division by zero, and non-finite outputs
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const decisionModule = require('../shared/decision_engine');
    module.exports = factory(decisionModule);
  } else {
    const decisionModule = root.SmartQueryRouterDecision;
    root.SmartQueryRouterArithmetic = factory(decisionModule);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (decisionModule) {
  'use strict';

  const { DecisionOutcome, createOptimizationDecision } = decisionModule;

  const RULE_ID = 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC';

  // Allowed framing prefixes (case-insensitive)
  const OPTIONAL_PREFIX_PATTERN = /^(?:what\s+is|calculate|calc|compute)\s+/i;
  // Allowed framing suffixes
  const OPTIONAL_SUFFIX_PATTERN = /[\s\?=]+$/;

  // Strict whitelist for permissible characters in an arithmetic expression:
  // digits, decimal point, basic operators (+, -, *, /, %, ^, ×, ÷), and parentheses
  const VALID_CHARACTERS_PATTERN = /^[0-9\.\s\+\-\*\/\%\^\(\)\×\÷]+$/;

  // Pattern detecting prohibited alphanumeric characters (words, variables, units)
  const PROHIBITED_ALPHANUM_PATTERN = /[a-zA-Z_\$]/;

  /**
   * Safe Tokenizer for basic arithmetic
   * @param {string} expr
   * @returns {Array<{ type: string, value: any }> | null}
   */
  function tokenize(expr) {
    const tokens = [];
    let i = 0;
    const len = expr.length;

    while (i < len) {
      const char = expr[i];

      if (/\s/.test(char)) {
        i++;
        continue;
      }

      if (/[0-9\.]/.test(char)) {
        let numStr = '';
        let dotCount = 0;
        while (i < len && /[0-9\.]/.test(expr[i])) {
          if (expr[i] === '.') {
            dotCount++;
            if (dotCount > 1) return null; // Invalid number like 1.2.3
          }
          numStr += expr[i];
          i++;
        }
        const parsed = Number(numStr);
        if (!Number.isFinite(parsed)) return null;
        tokens.push({ type: 'NUMBER', value: parsed });
        continue;
      }

      // Operators
      if (char === '+' || char === '-') {
        tokens.push({ type: 'OP', value: char });
        i++;
        continue;
      }

      if (char === '*' || char === '×') {
        tokens.push({ type: 'OP', value: '*' });
        i++;
        continue;
      }

      if (char === '/' || char === '÷') {
        tokens.push({ type: 'OP', value: '/' });
        i++;
        continue;
      }

      if (char === '%') {
        tokens.push({ type: 'OP', value: '%' });
        i++;
        continue;
      }

      if (char === '^') {
        tokens.push({ type: 'OP', value: '^' });
        i++;
        continue;
      }

      if (char === '(' || char === ')') {
        tokens.push({ type: 'PAREN', value: char });
        i++;
        continue;
      }

      // Any other character is invalid
      return null;
    }

    return tokens.length > 0 ? tokens : null;
  }

  /**
   * Recursive descent parser for safe arithmetic evaluation
   * Grammar:
   *   Expr   := Term (( '+' | '-' ) Term)*
   *   Term   := Factor (( '*' | '/' | '%' ) Factor)*
   *   Factor := Primary ( '^' Factor )?
   *   Primary:= NUMBER | '(' Expr ')' | '-' Primary | '+' Primary
   */
  class SafeArithmeticParser {
    constructor(tokens) {
      this.tokens = tokens;
      this.pos = 0;
    }

    peek() {
      return this.tokens[this.pos] || null;
    }

    consume() {
      return this.tokens[this.pos++] || null;
    }

    parse() {
      const result = this.parseExpr();
      if (result === null) return null;
      if (this.pos !== this.tokens.length) {
        return null; // Leftover unparsed tokens
      }
      return result;
    }

    parseExpr() {
      let left = this.parseTerm();
      if (left === null) return null;

      while (this.peek() && this.peek().type === 'OP' && (this.peek().value === '+' || this.peek().value === '-')) {
        const op = this.consume().value;
        const right = this.parseTerm();
        if (right === null) return null;

        if (op === '+') {
          left = left + right;
        } else {
          left = left - right;
        }
      }

      return Number.isFinite(left) ? left : null;
    }

    parseTerm() {
      let left = this.parseFactor();
      if (left === null) return null;

      while (this.peek() && this.peek().type === 'OP' && (this.peek().value === '*' || this.peek().value === '/' || this.peek().value === '%')) {
        const op = this.consume().value;
        const right = this.parseFactor();
        if (right === null) return null;

        if (op === '*') {
          left = left * right;
        } else if (op === '/') {
          if (right === 0) return null; // Reject division by zero conservatively
          left = left / right;
        } else if (op === '%') {
          if (right === 0) return null;
          left = left % right;
        }
      }

      return Number.isFinite(left) ? left : null;
    }

    parseFactor() {
      const base = this.parsePrimary();
      if (base === null) return null;

      if (this.peek() && this.peek().type === 'OP' && this.peek().value === '^') {
        this.consume();
        const exponent = this.parseFactor(); // Right-associative
        if (exponent === null) return null;
        // Conservative limit on exponent to prevent catastrophic calculation
        if (Math.abs(exponent) > 100) return null;
        const result = Math.pow(base, exponent);
        return Number.isFinite(result) ? result : null;
      }

      return base;
    }

    parsePrimary() {
      const token = this.peek();
      if (!token) return null;

      // Unary +/-
      if (token.type === 'OP' && (token.value === '+' || token.value === '-')) {
        this.consume();
        const next = this.parsePrimary();
        if (next === null) return null;
        return token.value === '-' ? -next : next;
      }

      // Parentheses
      if (token.type === 'PAREN' && token.value === '(') {
        this.consume();
        const subExpr = this.parseExpr();
        if (subExpr === null) return null;

        const closing = this.consume();
        if (!closing || closing.type !== 'PAREN' || closing.value !== ')') {
          return null; // Unbalanced parentheses
        }
        return subExpr;
      }

      // Number
      if (token.type === 'NUMBER') {
        this.consume();
        return token.value;
      }

      return null;
    }
  }

  /**
   * Parse and evaluate a candidate arithmetic string safely.
   * Returns evaluation result or null if the string is not a strictly deterministic expression.
   * @param {string} rawPrompt
   * @returns {{ expression: string, result: number } | null}
   */
  function evaluateDeterministicArithmetic(rawPrompt) {
    if (typeof rawPrompt !== 'string') return null;
    const trimmed = rawPrompt.trim();
    if (trimmed.length === 0 || trimmed.length > 120) return null;

    // Strip optional conversational framing: "what is 2 + 2?" -> "2 + 2"
    let stripped = trimmed.replace(OPTIONAL_PREFIX_PATTERN, '').replace(OPTIONAL_SUFFIX_PATTERN, '').trim();
    if (stripped.length === 0) return null;

    // Reject if any alphabetic, variable, or currency symbols remain
    if (PROHIBITED_ALPHANUM_PATTERN.test(stripped)) {
      return null;
    }

    // Must consist strictly of valid arithmetic characters
    if (!VALID_CHARACTERS_PATTERN.test(stripped)) {
      return null;
    }

    const tokens = tokenize(stripped);
    if (!tokens || tokens.length < 3) {
      // Must contain at least [Number, Operator, Number] to be an expression (e.g. not just "42")
      return null;
    }

    // Ensure at least one operator exists
    const hasOperator = tokens.some((t) => t.type === 'OP');
    if (!hasOperator) return null;

    const parser = new SafeArithmeticParser(tokens);
    const result = parser.parse();

    if (result === null || !Number.isFinite(result)) {
      return null;
    }

    // Round clean floating-point precision artifacts (e.g. 0.1 + 0.2 -> 0.3)
    const rounded = Number(Math.round(result * 1e10) / 1e10);

    return {
      expression: stripped,
      result: rounded
    };
  }

  /**
   * Optimization Decision Engine Rule Plugin
   */
  const arithmeticRule = {
    id: RULE_ID,
    evaluate(queryEvent) {
      if (!queryEvent || !queryEvent.content || (!queryEvent.content.rawPrompt && !queryEvent.content.normalizedPrompt)) {
        return null;
      }

      // If privacy classification detected sensitive tokens/keys, bypass local evaluation
      if (queryEvent.privacy && queryEvent.privacy.level === 'SENSITIVE') {
        return null;
      }

      const promptText = queryEvent.content.normalizedPrompt !== undefined
        ? queryEvent.content.normalizedPrompt
        : queryEvent.content.rawPrompt;

      const evaluation = evaluateDeterministicArithmetic(promptText);
      if (!evaluation) {
        return null; // Not deterministic arithmetic; fall through to next rule/baseline
      }

      return createOptimizationDecision({
        outcome: DecisionOutcome.LOCAL_ANSWER_CANDIDATE,
        ruleId: RULE_ID,
        reason: `Identified pure deterministic arithmetic expression: "${evaluation.expression}" = ${evaluation.result}. Candidate for local answer resolution.`,
        confidence: 1.0,
        metadata: {
          expression: evaluation.expression,
          calculatedResult: evaluation.result,
          isDeterministic: true,
          solver: 'SAFE_RECURSIVE_DESCENT'
        }
      });
    }
  };

  return {
    RULE_ID,
    evaluateDeterministicArithmetic,
    arithmeticRule
  };
});
