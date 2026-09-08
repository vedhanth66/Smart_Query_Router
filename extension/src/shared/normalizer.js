/**
 * Smart Query Router - Semantics-Preserving Query Normalizer
 * 
 * Performs safe, non-destructive normalization for detected queries:
 * - Normalizes line endings (CRLF / CR -> LF)
 * - Trims leading and trailing prompt whitespace
 * - Collapses excessive blank lines (3+ newlines -> 2 newlines)
 * - Normalizes repeated horizontal whitespace in prose
 * 
 * STRICT INVARIANTS:
 * - NEVER removes punctuation
 * - NEVER removes emojis
 * - NEVER removes mathematical symbols (+, -, *, /, =, ^, \sum, \int, \pi, etc.)
 * - NEVER alters code syntax or indentation inside code blocks
 * - NEVER removes quoted text or whitespace inside strings
 * - NEVER removes list structure (- , * , 1. )
 * - NEVER removes filler or conversational words merely to reduce tokens
 * - Strictly preserves original raw text alongside normalized output
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterNormalizer = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Matches code fence opening or closing lines (e.g. ```python, ```, ~~~)
  const CODE_FENCE_PATTERN = /^\s*(?:```|~~~)/;

  // Matches markdown list item prefix (ordered or unordered) with leading indentation
  const LIST_ITEM_PATTERN = /^(\s*(?:[-*+]|\d+\.)\s+)(.*)$/;

  // Matches markdown table rows or ASCII box table lines
  const TABLE_ROW_PATTERN = /^\s*\|.+?\|\s*$/;
  const ASCII_TABLE_PATTERN = /^\s*[\+\|][-+=]+[\+\|]\s*$/;

  /**
   * Tokenizes an inline line of text to separate protected elements
   * (inline code, quoted strings, inline math) from normal prose text.
   * @param {string} text
   * @returns {Array<{ type: 'PROTECTED' | 'PROSE', value: string }>}
   */
  function tokenizeInline(text) {
    const tokens = [];
    let currentIndex = 0;
    const len = text.length;

    // Pattern matching:
    // 1. Inline code: `...`
    // 2. Double-quoted strings: "..."
    // 3. Single-quoted strings: '...'
    // 4. TeX display math: $$...$$
    // 5. TeX inline math: $...$
    const protectedPattern = /(`[^`\n]+`)|("([^"\\\n]|\\.)*")|('([^'\\\n]|\\.)*')|(\$\$[^\$\n]+\$\$)|(\$[^\$\n]+\$)/g;
    let match;

    while ((match = protectedPattern.exec(text)) !== null) {
      if (match.index > currentIndex) {
        tokens.push({
          type: 'PROSE',
          value: text.slice(currentIndex, match.index)
        });
      }
      tokens.push({
        type: 'PROTECTED',
        value: match[0]
      });
      currentIndex = match.index + match[0].length;
    }

    if (currentIndex < len) {
      tokens.push({
        type: 'PROSE',
        value: text.slice(currentIndex)
      });
    }

    return tokens;
  }

  /**
   * Normalizes horizontal whitespace in prose while strictly preserving
   * all punctuation, symbols, emojis, and words.
   * @param {string} proseText
   * @returns {string}
   */
  function normalizeProseWhitespace(proseText) {
    // Collapses multiple consecutive spaces and tabs down to a single space
    return proseText.replace(/[ \t]+/g, ' ');
  }

  /**
   * Normalizes a single non-fenced line of text
   * @param {string} line
   * @returns {string}
   */
  function normalizeLine(line) {
    // Empty or whitespace-only lines become empty lines
    if (!line || !line.trim()) {
      return '';
    }

    // Preserve leading indentation (e.g. 4 spaces for code or indentation for nested lists)
    const leadingIndentMatch = line.match(/^[ \t]*/);
    const leadingIndent = leadingIndentMatch ? leadingIndentMatch[0] : '';
    const remainder = line.slice(leadingIndent.length);

    // Check if the remainder is a list item
    const listMatch = remainder.match(LIST_ITEM_PATTERN);
    if (listMatch) {
      const listPrefix = listMatch[1]; // e.g. "- " or "1. "
      const listBody = listMatch[2];

      const tokens = tokenizeInline(listBody);
      const normalizedBody = tokens
        .map((t) => (t.type === 'PROTECTED' ? t.value : normalizeProseWhitespace(t.value)))
        .join('')
        .trimEnd();

      return leadingIndent + listPrefix + normalizedBody;
    }

    // Check if line is a table row (markdown table or ASCII grid)
    if (TABLE_ROW_PATTERN.test(line) || ASCII_TABLE_PATTERN.test(line)) {
      // For table rows, preserve column spacing and alignment verbatim!
      return line.trimEnd();
    }

    // If line has 4+ spaces of indentation or tabs, treat as potential indented code:
    // preserve internal indentation structure
    if (leadingIndent.startsWith('    ') || leadingIndent.startsWith('\t')) {
      // For indented code lines, do NOT collapse internal whitespace!
      return leadingIndent + remainder.trimEnd();
    }

    // Normal prose line: protect inline code, quotes, and math spans
    const tokens = tokenizeInline(remainder);
    const normalizedRemainder = tokens
      .map((t) => (t.type === 'PROTECTED' ? t.value : normalizeProseWhitespace(t.value)))
      .join('')
      .trimEnd();

    return leadingIndent + normalizedRemainder;
  }

  /**
   * Performs semantics-preserving normalization on query text.
   * @param {string} rawText
   * @returns {string}
   */
  function normalizeQueryText(rawText) {
    if (typeof rawText !== 'string' || rawText.length === 0) {
      return '';
    }

    // 1. Line ending normalization: CRLF and CR -> LF
    let text = rawText.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // 2. Strip leading and trailing blank lines
    text = text.replace(/^\n+/, '').trimEnd();
    if (text.length === 0) {
      return '';
    }

    // If line 1 does not start with code indentation (4+ spaces or tab) or a list item,
    // trim leading prose whitespace
    const firstLineIndentMatch = text.match(/^[ \t]*/);
    const firstLineIndent = firstLineIndentMatch ? firstLineIndentMatch[0] : '';
    const isIndentedCodeOrList = firstLineIndent.startsWith('    ') ||
      firstLineIndent.startsWith('\t') ||
      LIST_ITEM_PATTERN.test(text);

    if (!isIndentedCodeOrList) {
      text = text.trimStart();
    }

    // 3. Process line-by-line while tracking code fences
    const lines = text.split('\n');
    const processedLines = [];
    let inCodeFence = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (CODE_FENCE_PATTERN.test(line)) {
        // Toggle code fence state
        inCodeFence = !inCodeFence;
        // The fence line itself is trimmed of trailing whitespace
        processedLines.push(line.trimEnd());
        continue;
      }

      if (inCodeFence) {
        // Inside code block: preserve EXACT indentation and internal spacing
        // Only strip trailing carriage return if present
        processedLines.push(line);
      } else {
        // Outside code block: apply safe semantics-preserving normalization
        processedLines.push(normalizeLine(line));
      }
    }

    // Join lines back
    let result = processedLines.join('\n');

    // 4. Collapse excessive consecutive blank lines: 3+ newlines -> 2 newlines (standard paragraph break)
    // Only outside code fences (if fences exist, we must be careful)
    if (!result.includes('```') && !result.includes('~~~')) {
      result = result.replace(/\n{3,}/g, '\n\n');
    } else {
      // Safe multi-newline collapse preserving fences
      const parts = result.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~)/g);
      result = parts
        .map((part) => {
          if (part.startsWith('```') || part.startsWith('~~~')) {
            return part; // preserve code block blank lines exactly
          }
          return part.replace(/\n{3,}/g, '\n\n');
        })
        .join('');
    }

    result = result.trimEnd();
    if (!isIndentedCodeOrList) {
      result = result.trimStart();
    }
    return result;
  }

  /**
   * Full normalization object containing both raw and normalized representations
   * along with comparison metrics.
   * @param {string} rawText
   * @returns {{
   *   rawPrompt: string,
   *   normalizedPrompt: string,
   *   isChanged: boolean,
   *   savings: { characters: number, percentage: number }
   * }}
   */
  function normalizeQuery(rawText) {
    const raw = typeof rawText === 'string' ? rawText : '';
    const normalized = normalizeQueryText(raw);
    const isChanged = raw !== normalized;
    const charSaved = Math.max(0, raw.length - normalized.length);
    const pctSaved = raw.length > 0 ? Number(((charSaved / raw.length) * 100).toFixed(2)) : 0;

    return {
      rawPrompt: raw,
      normalizedPrompt: normalized,
      isChanged,
      savings: {
        characters: charSaved,
        percentage: pctSaved
      }
    };
  }

  /**
   * Compares raw prompt and normalized prompt for future decision layers.
   * @param {string} rawPrompt
   * @param {string} normalizedPrompt
   */
  function comparePrompts(rawPrompt, normalizedPrompt) {
    const raw = typeof rawPrompt === 'string' ? rawPrompt : '';
    const norm = typeof normalizedPrompt === 'string' ? normalizedPrompt : '';

    return {
      isIdentical: raw === norm,
      rawLength: raw.length,
      normalizedLength: norm.length,
      charactersSaved: Math.max(0, raw.length - norm.length),
      rawLines: raw.split(/\r\n|\r|\n/).length,
      normalizedLines: norm.split('\n').length,
      hasTrailingWhitespace: /\s+$/.test(raw),
      hasLeadingWhitespace: /^\s+/.test(raw),
      hasCrlf: /\r\n|\r/.test(raw),
      hasExcessiveBlankLines: /\n{3,}/.test(raw.replace(/\r\n|\r/g, '\n'))
    };
  }

  /**
   * Programmatic verification utility ensuring no content tokens, emojis,
   * punctuation, or mathematical symbols were lost during normalization.
   * @param {string} rawPrompt
   * @param {string} normalizedPrompt
   * @returns {{ preserved: boolean, violations: string[] }}
   */
  function verifySemanticsPreserved(rawPrompt, normalizedPrompt) {
    const violations = [];
    const raw = typeof rawPrompt === 'string' ? rawPrompt : '';
    const norm = typeof normalizedPrompt === 'string' ? normalizedPrompt : '';

    // 1. Check all non-whitespace words/characters exist in identical sequence
    const rawTokens = raw.replace(/\r\n|\r/g, '\n').split(/\s+/).filter(Boolean);
    const normTokens = norm.split(/\s+/).filter(Boolean);

    if (rawTokens.length !== normTokens.length) {
      violations.push(
        `Token count mismatch: raw has ${rawTokens.length} tokens, normalized has ${normTokens.length} tokens`
      );
    } else {
      for (let i = 0; i < rawTokens.length; i++) {
        if (rawTokens[i] !== normTokens[i]) {
          violations.push(
            `Token mismatch at index ${i}: raw="${rawTokens[i]}" vs normalized="${normTokens[i]}"`
          );
          break;
        }
      }
    }

    // 2. Check mathematical symbols preservation
    const mathSymbols = ['+', '-', '*', '/', '=', '^', '%', '×', '÷', '<', '>', '≤', '≥', '∑', '∫', 'π'];
    for (const sym of mathSymbols) {
      const rawCount = (raw.split(sym).length - 1);
      const normCount = (norm.split(sym).length - 1);
      if (rawCount !== normCount) {
        violations.push(
          `Math symbol count mismatch for "${sym}": raw=${rawCount}, normalized=${normCount}`
        );
      }
    }

    // 3. Check punctuation preservation
    const punctuationChars = ['.', ',', '!', '?', ':', ';', '(', ')', '[', ']', '{', '}', '"', "'"];
    for (const p of punctuationChars) {
      const rawCount = (raw.split(p).length - 1);
      const normCount = (norm.split(p).length - 1);
      if (rawCount !== normCount) {
        violations.push(
          `Punctuation count mismatch for "${p}": raw=${rawCount}, normalized=${normCount}`
        );
      }
    }

    return {
      preserved: violations.length === 0,
      violations
    };
  }

  return {
    normalizeQueryText,
    normalizeQuery,
    comparePrompts,
    verifySemanticsPreserved
  };
});
