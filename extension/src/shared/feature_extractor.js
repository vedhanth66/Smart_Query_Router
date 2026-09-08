/**
 * Smart Query Router - Non-Generative Local Feature Extractor
 * 
 * Computes inexpensive, deterministic, non-generative feature signals
 * locally in the browser runtime without requiring any LLM inference:
 * - Approximate length & token estimates
 * - Code-like formatting (fences, inline backticks, indentation, syntax keywords)
 * - Lists (ordered, unordered, mixed)
 * - Number of questions
 * - Mathematical symbols & LaTeX notation
 * - URLs
 * - Reasoning & comparison cues
 * 
 * DESIGN PRINCIPLE:
 * These features are purely heuristic signals to inform downstream routing
 * and evaluation; they are NOT final truth and do not enforce routing on their own.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterFeatureExtractor = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Regular expressions for detecting code patterns
  const CODE_FENCE_REGEX = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
  const INLINE_CODE_REGEX = /`[^`\n]+`/g;
  const INDENTED_CODE_LINE_REGEX = /^(?: {4}|\t)[^\s]/m;
  const COMMON_CODE_KEYWORDS_REGEX = /\b(?:function|const|let|var|def|class|import|from|export|return|async|await|public|private|interface|struct|typedef|void|int|str|bool|nullptr|NULL)\b/;
  const CODE_SYNTAX_CHARACTERS_REGEX = /[{};][\s\n]*$|=>|->|===|!==|::/m;

  // Regular expressions for detecting list items
  const UNORDERED_LIST_REGEX = /^[ \t]*[-*+][ \t]+.+$/gm;
  const ORDERED_LIST_REGEX = /^[ \t]*\d+\.[ \t]+.+$/gm;

  // Question mark detection
  const QUESTION_MARK_REGEX = /\?/g;

  // Mathematical symbols and notations
  const MATH_SYMBOLS_REGEX = /[+\-*/=^%×÷≤≥∑∫√π∞≈≠±]/g;
  const LATEX_MATH_REGEX = /\$\$[\s\S]*?\$\$|\$[^\$\n]+\$|\\[a-zA-Z]+(?:\{[^}]*\})*|\\(?:frac|sqrt|sum|int|partial|alpha|beta|gamma|theta|mu|sigma|omega|Delta|nabla)/;

  // URL detection
  const URL_REGEX = /\b(?:https?:\/\/|www\.)[^\s<>"'{}|\\^`]+/gi;

  // Comparison cue phrases
  const COMPARISON_CUES = [
    { label: 'VERSUS', regex: /\b(?:vs\.?|versus)\b/i },
    { label: 'COMPARE', regex: /\b(?:compare|comparison|comparative)\b/i },
    { label: 'DIFFERENCE', regex: /\bdifference\s+(?:between|of)\b/i },
    { label: 'PROS_AND_CONS', regex: /\b(?:pros?\s+and\s+cons?|advantages?\s+and\s+disadvantages?)\b/i },
    { label: 'TRADEOFFS', regex: /\btrade[\s-]?offs?\b/i },
    { label: 'BETTER_THAN', regex: /\b(?:better|worse|faster|slower)\s+than\b/i },
    { label: 'WHICH_IS_BEST', regex: /\bwhich\s+(?:one\s+)?is\s+(?:better|best|faster|preferable)\b/i }
  ];

  // Reasoning cue phrases
  const REASONING_CUES = [
    { label: 'WHY', regex: /\bwhy\b/i },
    { label: 'EXPLAIN_WHY', regex: /\bexplain\s+why\b/i },
    { label: 'HOW_DOES_IT_WORK', regex: /\bhow\s+(?:does|do|can)\s+[\w\s]+\s+work\b/i },
    { label: 'STEP_BY_STEP', regex: /\bstep[\s-]by[\s-]step\b/i },
    { label: 'CAUSE_AND_EFFECT', regex: /\b(?:cause\s+of|reason\s+for|leads?\s+to)\b/i },
    { label: 'DERIVE_OR_PROVE', regex: /\b(?:derive|derivation|prove|proof)\b/i },
    { label: 'ROOT_CAUSE', regex: /\broot[\s-]cause\b/i }
  ];

  // Table patterns
  const TABLE_ROW_REGEX = /^\s*\|.+?\|\s*$/m;
  const TABLE_HEADER_SEPARATOR_REGEX = /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/m;
  const ASCII_BOX_TABLE_REGEX = /^\s*[\+\|][-+=]+[\+\|]\s*$/m;
  const TAB_SEPARATED_ROW_REGEX = /^[^\t\n]+(?:\t[^\t\n]+){2,}$/m;

  // Attachment, image, and file reference patterns
  const ATTACHMENT_REF_REGEX = /\[(?:Attachment|Image|File|Upload)(?:\s*#?\d*)?(?:\s*:\s*[^\]]+)?\]|\b(?:attached\s+(?:file|document|pdf|spreadsheet|notes|data|report|code|screenshot|csv|image)|this\s+attachment|uploaded\s+(?:file|document|csv|data|image|pdf)|see\s+(?:the\s+)?attachment|in\s+the\s+attachment|from\s+the\s+attachment)\b/i;
  const IMAGE_REF_REGEX = /\[Image(?:\s*#?\d*)?(?:\s*:\s*[^\]]+)?\]|\b(?:in\s+this\s+image|look\s+at\s+this\s+(?:image|screenshot|photo|diagram|chart)|attached\s+image|this\s+screenshot|from\s+the\s+image|analyze\s+this\s+image)\b|!\[.*?\]\(.*?\)/i;
  const FILE_REF_REGEX = /\[File(?:\s*#?\d*)?(?:\s*:\s*[^\]]+)?\]|\b(?:in\s+this\s+file|the\s+(?:csv|pdf|json|yaml|xml|xlsx|docx)\s+file|attached\s+file|uploaded\s+file)\b|\b[\w-]+\.(?:csv|pdf|json|py|js|ts|tsx|jsx|html|css|cpp|c|java|go|rs|sql|md|xlsx|docx|png|jpg|jpeg|webp|svg)\b/i;

  /**
   * Estimates token count based on typical sub-word tokenization ratios.
   * Standard English / Code heuristic: ~4 characters per token.
   * @param {number} charCount
   * @returns {number}
   */
  function estimateTokens(charCount) {
    if (!charCount || charCount <= 0) return 0;
    return Math.ceil(charCount / 4);
  }

  /**
   * Extracts inexpensive, non-generative feature signals from prompt text.
   * @param {string} promptText
   * @param {object} [options]
   * @param {object} [options.domAttachments] - Active DOM attachments detected in Claude UI
   * @returns {object} Extracted feature signals
   */
  function extractQueryFeatures(promptText, options = {}) {
    const text = typeof promptText === 'string' ? promptText : '';
    const charCount = text.length;

    // Fast-path for empty text
    if (charCount === 0) {
      return {
        length: {
          characterCount: 0,
          wordCount: 0,
          lineCount: 0,
          estimatedTokens: 0
        },
        code: {
          hasCodeFence: false,
          hasInlineCode: false,
          hasIndentedCode: false,
          hasCodeKeywords: false,
          hasCodeSyntax: false
        },
        lists: {
          hasList: false,
          listType: 'NONE',
          orderedCount: 0,
          unorderedCount: 0,
          totalCount: 0
        },
        questions: {
          questionCount: 0,
          hasMultipleQuestions: false
        },
        math: {
          hasMathSymbols: false,
          hasLatexMath: false,
          symbolCount: 0
        },
        urls: {
          hasUrl: false,
          urlCount: 0
        },
        cues: {
          hasComparisonCue: false,
          hasReasoningCue: false,
          detectedCues: []
        },
        tables: {
          hasTable: false,
          hasMarkdownTable: false,
          hasAsciiTable: false,
          hasTabularData: false
        },
        attachments: {
          hasAttachment: false,
          hasDomAttachment: false,
          hasAttachmentReference: false,
          hasImageReference: false,
          hasFileReference: false,
          attachmentCount: 0,
          types: []
        },
        richContent: {
          hasRichInput: false,
          hasAttachments: false,
          hasImages: false,
          hasFiles: false,
          hasCodeBlocks: false,
          hasTables: false,
          hasMathLatex: false,
          types: [],
          preservationRequired: false
        }
      };
    }

    // 1. Length features
    const lines = text.split(/\r\n|\r|\n/);
    const lineCount = lines.length;
    const words = text.trim().split(/\s+/).filter(Boolean);
    const wordCount = words.length;
    const estimatedTokens = estimateTokens(charCount);

    // 2. Code-like formatting features
    const hasCodeFence = CODE_FENCE_REGEX.test(text);
    CODE_FENCE_REGEX.lastIndex = 0; // reset stateful regex

    const hasInlineCode = INLINE_CODE_REGEX.test(text);
    INLINE_CODE_REGEX.lastIndex = 0;

    const hasIndentedCode = INDENTED_CODE_LINE_REGEX.test(text);
    const hasCodeKeywords = COMMON_CODE_KEYWORDS_REGEX.test(text);
    const hasCodeSyntaxCharacters = CODE_SYNTAX_CHARACTERS_REGEX.test(text);

    const hasCodeSyntax = hasCodeFence || hasInlineCode || hasIndentedCode ||
      (hasCodeKeywords && hasCodeSyntaxCharacters);

    // 3. List features
    const unorderedMatches = text.match(UNORDERED_LIST_REGEX) || [];
    const orderedMatches = text.match(ORDERED_LIST_REGEX) || [];
    const unorderedCount = unorderedMatches.length;
    const orderedCount = orderedMatches.length;
    const totalListItems = unorderedCount + orderedCount;
    const hasList = totalListItems > 0;

    let listType = 'NONE';
    if (unorderedCount > 0 && orderedCount > 0) {
      listType = 'MIXED';
    } else if (orderedCount > 0) {
      listType = 'ORDERED';
    } else if (unorderedCount > 0) {
      listType = 'UNORDERED';
    }

    // 4. Question features
    const questionMatches = text.match(QUESTION_MARK_REGEX) || [];
    const questionCount = questionMatches.length;
    const hasMultipleQuestions = questionCount > 1;

    // 5. Mathematical symbols features
    const mathSymbolMatches = text.match(MATH_SYMBOLS_REGEX) || [];
    const symbolCount = mathSymbolMatches.length;
    const hasLatexMath = LATEX_MATH_REGEX.test(text);
    const hasMathSymbols = symbolCount > 0 || hasLatexMath;

    // 6. URL features
    const urlMatches = text.match(URL_REGEX) || [];
    URL_REGEX.lastIndex = 0;
    const urlCount = urlMatches.length;
    const hasUrl = urlCount > 0;

    // 7. Reasoning and comparison cue detection
    const detectedCues = [];
    let hasComparisonCue = false;
    let hasReasoningCue = false;

    for (const cue of COMPARISON_CUES) {
      if (cue.regex.test(text)) {
        detectedCues.push(cue.label);
        hasComparisonCue = true;
      }
    }

    for (const cue of REASONING_CUES) {
      if (cue.regex.test(text)) {
        detectedCues.push(cue.label);
        hasReasoningCue = true;
      }
    }

    // 8. Table detection
    const hasMarkdownTable = TABLE_ROW_REGEX.test(text) && TABLE_HEADER_SEPARATOR_REGEX.test(text);
    const hasAsciiTable = ASCII_BOX_TABLE_REGEX.test(text);
    const hasTabularData = TAB_SEPARATED_ROW_REGEX.test(text);
    const hasTable = hasMarkdownTable || hasAsciiTable || hasTabularData || TABLE_ROW_REGEX.test(text);

    // 9. Attachment & file detection
    const domAtt = (options && options.domAttachments) || null;
    const isDomAttArray = Array.isArray(domAtt);
    const domAttCount = isDomAttArray ? domAtt.length : (domAtt && typeof domAtt.count === 'number' ? domAtt.count : 0);
    const hasDomAttachment = Boolean(
      (domAtt && domAtt.hasAttachments) ||
      (isDomAttArray && domAtt.length > 0)
    );

    const domAttTypes = [];
    if (isDomAttArray) {
      domAtt.forEach((item) => {
        if (item && (item.isImage || item.type === 'image')) {
          if (!domAttTypes.includes('image')) domAttTypes.push('image');
        } else if (item && (item.type === 'file' || item.name)) {
          if (!domAttTypes.includes('file')) domAttTypes.push('file');
        } else {
          if (!domAttTypes.includes('attachment')) domAttTypes.push('attachment');
        }
      });
    } else if (domAtt && Array.isArray(domAtt.types)) {
      domAttTypes.push(...domAtt.types);
    }

    const hasAttachmentReference = ATTACHMENT_REF_REGEX.test(text);
    const hasImageReference = IMAGE_REF_REGEX.test(text) || domAttTypes.includes('image');
    const hasFileReference = FILE_REF_REGEX.test(text) || domAttTypes.includes('file');
    const hasAttachment = hasDomAttachment || hasAttachmentReference || hasImageReference || hasFileReference;

    const attachmentTypes = [];
    for (const t of domAttTypes) {
      if (!attachmentTypes.includes(t)) attachmentTypes.push(t);
    }
    if (hasImageReference && !attachmentTypes.includes('image')) attachmentTypes.push('image');
    if (hasFileReference && !attachmentTypes.includes('file')) attachmentTypes.push('file');
    if (hasAttachmentReference && !attachmentTypes.includes('attachment')) attachmentTypes.push('attachment');

    // 10. Consolidated richContent
    const hasCodeBlocks = hasCodeFence || hasIndentedCode;
    const richTypes = [];
    if (hasAttachment) richTypes.push('attachment');
    if (hasImageReference) richTypes.push('image');
    if (hasFileReference) richTypes.push('file');
    if (hasCodeBlocks) richTypes.push('code_block');
    if (hasInlineCode) richTypes.push('inline_code');
    if (hasTable) richTypes.push('table');
    if (hasLatexMath) richTypes.push('latex_math');

    const hasRichInput = hasAttachment || hasCodeBlocks || hasTable || hasLatexMath;
    const preservationRequired = hasRichInput || hasInlineCode;

    return {
      length: {
        characterCount: charCount,
        wordCount,
        lineCount,
        estimatedTokens
      },
      code: {
        hasCodeFence,
        hasInlineCode,
        hasIndentedCode,
        hasCodeKeywords,
        hasCodeSyntax
      },
      lists: {
        hasList,
        listType,
        orderedCount,
        unorderedCount,
        totalCount: totalListItems
      },
      questions: {
        questionCount,
        hasMultipleQuestions
      },
      math: {
        hasMathSymbols,
        hasLatexMath,
        symbolCount
      },
      urls: {
        hasUrl,
        urlCount
      },
      cues: {
        hasComparisonCue,
        hasReasoningCue,
        detectedCues
      },
      tables: {
        hasTable,
        hasMarkdownTable,
        hasAsciiTable,
        hasTabularData
      },
      attachments: {
        hasAttachment,
        hasDomAttachment,
        hasAttachmentReference,
        hasImageReference,
        hasFileReference,
        attachmentCount: domAttCount || (hasAttachment ? 1 : 0),
        types: attachmentTypes
      },
      richContent: {
        hasRichInput,
        hasAttachments: hasAttachment,
        hasImages: hasImageReference,
        hasFiles: hasFileReference,
        hasCodeBlocks,
        hasTables: hasTable,
        hasMathLatex: hasLatexMath,
        types: richTypes,
        preservationRequired
      }
    };
  }

  return {
    extractQueryFeatures,
    estimateTokens
  };
});
