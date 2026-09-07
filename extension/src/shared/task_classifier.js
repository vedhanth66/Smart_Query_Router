/**
 * Smart Query Router - Conservative Task Classifier
 * 
 * DESIGN PRINCIPLES:
 * - Separate, stable task classifier interface with 13 conservative task categories:
 *   1. greeting
 *   2. arithmetic
 *   3. factual question
 *   4. summarization
 *   5. rewriting
 *   6. translation
 *   7. creative writing
 *   8. coding
 *   9. debugging
 *   10. comparison
 *   11. analysis
 *   12. reasoning
 *   13. unknown
 * - Strictly deterministic & conservative: relies on explicit lexical patterns,
 *   keyword cues, and pre-extracted local features. Zero LLM inference.
 * - Non-prescriptive boundary: The task classifier DOES NOT choose a model or route;
 *   it supplies a structured task classification signal to the routing policy.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let featureExtractor = null;
    try {
      featureExtractor = require('./feature_extractor');
    } catch (_) {}
    module.exports = factory(featureExtractor);
  } else {
    const featureExtractor = root.SmartQueryRouterFeatureExtractor || null;
    root.SmartQueryRouterTaskClassifier = factory(featureExtractor);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (featureExtractorModule) {
  'use strict';

  /**
   * 13 Stable Task Categories
   */
  const TaskCategory = Object.freeze({
    GREETING: 'greeting',
    ARITHMETIC: 'arithmetic',
    FACTUAL_QUESTION: 'factual question',
    SUMMARIZATION: 'summarization',
    REWRITING: 'rewriting',
    TRANSLATION: 'translation',
    CREATIVE_WRITING: 'creative writing',
    CODING: 'coding',
    DEBUGGING: 'debugging',
    COMPARISON: 'comparison',
    ANALYSIS: 'analysis',
    REASONING: 'reasoning',
    UNKNOWN: 'unknown'
  });

  const GREETING_REGEX = /^(?:hi|hello|hey|hiya|howdy|good\s+(?:morning|afternoon|evening|day)|greetings|how\s+are\s+you(?:\s+doing)?|how's\s+it\s+going|thanks(?:\s+(?:so\s+much|a\s+lot))?|thank\s+you(?:\s+(?:very\s+much|so\s+much))?|bye|goodbye|see\s+you)(?:\s+(?:claude|there|friend|assistant|bot))?[!.]*$/i;

  // 2. Arithmetic patterns
  const ARITHMETIC_PREFIX_REGEX = /^(?:calculate|compute|solve|what\s+is)\s+[0-9\.\s\+\-\*\/\%\^\(\)\×\÷\=]+[\?\.\=\s]*$/i;
  const ARITHMETIC_EXPR_REGEX = /^[0-9\.\s\+\-\*\/\%\^\(\)\×\÷\=]+[\?\.\s]*$/;

  // 3. Debugging patterns (explicit error, stack trace, bug investigation)
  const DEBUGGING_REGEX = /\b(?:debug|fix\s+(?:this\s+)?(?:error|bug|issue|exception|crash|code)|stack\s*trace|traceback|syntaxerror|typeerror|referenceerror|nullpointerexception|uncaught\s+exception|why\s+is\s+(?:this|my)\s+(?:failing|crashing|throwing|broken|not\s+working)|segmentation\s+fault)\b/i;

  // 4. Coding patterns (programming instructions, functions, scripts, algorithms)
  const CODING_INTENT_REGEX = /\b(?:(?:write|implement|create)\s+(?:an?\s+)?(?:[\w-]+\s+){0,2}(?:function|script|code|program|class|method|query|regex|algorithm|api|endpoint|component|decorator|macro|tree|parser)|refactor\s+(?:this\s+)?code|(?:in|using)\s+(?:python|javascript|typescript|c\+\+|rust|golang|java|c#|sql|html|css|bash|ruby|php|swift))\b/i;

  // 5. Summarization patterns
  const SUMMARIZATION_REGEX = /\b(?:summarize|summary\s+of|tldr|tl;dr|key\s+takeaways|briefly\s+summarize|give\s+me\s+a\s+summary|condense\s+(?:this|the)|give\s+a\s+brief\s+overview|executive\s+summary)\b/i;

  // 6. Rewriting patterns
  const REWRITING_REGEX = /\b(?:rewrite|paraphrase|rephrase|reword|proofread|edit\s+this|make\s+it\s+(?:sound\s+)?(?:more\s+)?(?:professional|concise|formal|casual|polite|engaging)|fix\s+the\s+grammar|improve\s+the\s+wording)\b/i;

  // 7. Translation patterns
  const TRANSLATION_REGEX = /\b(?:translate\s+(?:(?:this|the|following)\s+)?(?:text|paragraph|document|sentence|phrase|message)?\s*(?:into|to|in)\b|translate\b.*?\b(?:into|to|in)\s+[a-z]+|how\s+do\s+you\s+say\s+[\w\s'"]+\s+in|in\s+(?:spanish|french|german|japanese|mandarin|chinese|italian|portuguese|russian|arabic|hindi|korean|dutch|latin))\b/i;

  // 8. Creative writing patterns
  const CREATIVE_WRITING_REGEX = /\b(?:write\s+(?:an?\s+)?(?:[\w-]+\s+){0,2}(?:story|poem|essay|lyrics|song|dialogue|fiction|haiku|limerick|narrative|scene|monologue|script)|compose\s+a\s+(?:poem|song)|create\s+a\s+(?:character|plot|fictional))\b/i;

  // 9. Comparison patterns
  const COMPARISON_REGEX = /\b(?:versus|vs\.?|compare|difference\s+between|pros\s+and\s+cons|trade[\s-]?offs?|advantages?\s+and\s+disadvantages?|which\s+(?:one\s+|[\w-]+\s+)?is\s+(?:better|best|faster|preferable|superior)|(?:better|best)\s+between)\b/i;

  // 10. Reasoning patterns
  const REASONING_REGEX = /\b(?:why\b|explain\s+why|step[\s-]by[\s-]step\s+reasoning|cause\s+and\s+effect|derive|derivation\s+of|prove\s+that|proof\s+of|logical\s+deduction|root\s+cause|first\s+principles)\b/i;

  // 11. Analysis patterns
  const ANALYSIS_REGEX = /\b(?:analyze|analysis\s+of|evaluate\s+the|dissect|breakdown\s+of|critical\s+analysis|audit|impact\s+of|implications\s+of|feasibility\s+analysis)\b/i;

  // 12. Factual question patterns
  const FACTUAL_QUESTION_PREFIX_REGEX = /^(?:what\s+(?:is|are|was|were)|who\s+(?:is|was|were)|when\s+(?:was|did|is)|where\s+(?:is|are|was)|define|what\s+does\s+[\w\s]+\s+mean|capital\s+of|distance\s+between)\b/i;

  class TaskClassifier {
    /**
     * Classifies a prompt into one of 13 stable task categories.
     * 
     * GUARANTEES:
     * - Never selects a model or route.
     * - Returns structured task category, confidence, reason, and matched signals.
     * - Fallback is strictly TaskCategory.UNKNOWN.
     * 
     * @param {string} promptText
     * @param {object|null} [localFeatures]
     * @param {object|null} [contextDependency]
     * @returns {{
     *   category: string,
     *   confidence: number,
     *   reason: string,
     *   matchedSignals: string[]
     * }}
     */
    classifyTask(promptText, localFeatures = null, contextDependency = null) {
      const text = typeof promptText === 'string' ? promptText.trim() : '';

      if (text.length === 0) {
        return {
          category: TaskCategory.UNKNOWN,
          confidence: 1.0,
          reason: 'Empty prompt cannot be assigned a task category.',
          matchedSignals: ['EMPTY_PROMPT']
        };
      }

      // 1. Greeting: Standalone greetings and pleasantries
      if (GREETING_REGEX.test(text)) {
        return {
          category: TaskCategory.GREETING,
          confidence: 0.95,
          reason: 'Prompt is a conversational greeting, pleasantry, or sign-off.',
          matchedSignals: ['GREETING_PATTERN']
        };
      }

      // 2. Arithmetic: Pure mathematical calculations
      const hasMathOp = /[\+\-\*\/\%\^\×\÷]/.test(text);
      const isPureMath = hasMathOp && (ARITHMETIC_PREFIX_REGEX.test(text) || ARITHMETIC_EXPR_REGEX.test(text)) &&
        !/[a-zA-Z]{4,}/.test(text.replace(/^(?:calculate|compute|solve|what\s+is)\s+/i, ''));
      if (isPureMath) {
        return {
          category: TaskCategory.ARITHMETIC,
          confidence: 0.95,
          reason: 'Prompt is a direct arithmetic calculation or equation.',
          matchedSignals: ['ARITHMETIC_PATTERN']
        };
      }

      // 3. Debugging: Explicit error diagnostics, exception fixing, or bug troubleshooting
      if (DEBUGGING_REGEX.test(text)) {
        return {
          category: TaskCategory.DEBUGGING,
          confidence: 0.90,
          reason: 'Prompt requests debugging, error troubleshooting, or stack trace analysis.',
          matchedSignals: ['DEBUGGING_CUES']
        };
      }

      // 4. Coding: Programming implementation, code blocks, syntax
      const codeFeats = localFeatures && localFeatures.code ? localFeatures.code : {};
      const hasCodeStructure = Boolean(
        codeFeats.hasCodeFence ||
        codeFeats.hasIndentedCode ||
        codeFeats.hasCodeSyntax ||
        (codeFeats.hasCodeKeywords && codeFeats.hasInlineCode)
      );

      if (hasCodeStructure || CODING_INTENT_REGEX.test(text)) {
        const signals = [];
        if (codeFeats.hasCodeFence) signals.push('hasCodeFence');
        if (codeFeats.hasCodeSyntax) signals.push('hasCodeSyntax');
        if (CODING_INTENT_REGEX.test(text)) signals.push('CODING_INTENT');

        return {
          category: TaskCategory.CODING,
          confidence: 0.90,
          reason: 'Prompt involves software programming, implementation, or code syntax.',
          matchedSignals: signals.length > 0 ? signals : ['CODING_SIGNALS']
        };
      }

      // 5. Comparison: Comparative analysis, trade-offs, pros & cons
      const cueFeats = localFeatures && localFeatures.cues ? localFeatures.cues : {};
      if (cueFeats.hasComparisonCue || COMPARISON_REGEX.test(text)) {
        return {
          category: TaskCategory.COMPARISON,
          confidence: 0.88,
          reason: 'Prompt requests comparative evaluation or trade-off analysis.',
          matchedSignals: cueFeats.detectedCues || ['COMPARISON_CUE']
        };
      }

      // 6. Summarization: Text condensation, takeaways, TL;DR
      if (SUMMARIZATION_REGEX.test(text)) {
        return {
          category: TaskCategory.SUMMARIZATION,
          confidence: 0.88,
          reason: 'Prompt explicitly requests text summarization or condensation.',
          matchedSignals: ['SUMMARIZATION_KEYWORD']
        };
      }

      // 7. Rewriting: Paraphrasing, grammar correction, tone editing
      if (REWRITING_REGEX.test(text)) {
        return {
          category: TaskCategory.REWRITING,
          confidence: 0.85,
          reason: 'Prompt requests rewriting, paraphrasing, or editorial polishing.',
          matchedSignals: ['REWRITING_KEYWORD']
        };
      }

      // 8. Translation: Language translation
      if (TRANSLATION_REGEX.test(text)) {
        return {
          category: TaskCategory.TRANSLATION,
          confidence: 0.90,
          reason: 'Prompt requests inter-language translation.',
          matchedSignals: ['TRANSLATION_KEYWORD']
        };
      }

      // 9. Creative Writing: Stories, poetry, essays, lyrics
      if (CREATIVE_WRITING_REGEX.test(text)) {
        return {
          category: TaskCategory.CREATIVE_WRITING,
          confidence: 0.88,
          reason: 'Prompt requests creative fiction, poetry, or story composition.',
          matchedSignals: ['CREATIVE_WRITING_KEYWORD']
        };
      }

      // 10. Reasoning: Step-by-step logic, derivations, why questions
      if (cueFeats.hasReasoningCue || REASONING_REGEX.test(text)) {
        return {
          category: TaskCategory.REASONING,
          confidence: 0.85,
          reason: 'Prompt involves multi-step logical reasoning, proof, or causal explanation.',
          matchedSignals: cueFeats.detectedCues || ['REASONING_CUE']
        };
      }

      // 11. Analysis: Detailed examination, auditing, breakdown
      if (ANALYSIS_REGEX.test(text)) {
        return {
          category: TaskCategory.ANALYSIS,
          confidence: 0.82,
          reason: 'Prompt requests analytical examination or system breakdown.',
          matchedSignals: ['ANALYSIS_KEYWORD']
        };
      }

      // 12. Factual Question: Direct questions seeking facts/definitions
      if (FACTUAL_QUESTION_PREFIX_REGEX.test(text)) {
        return {
          category: TaskCategory.FACTUAL_QUESTION,
          confidence: 0.80,
          reason: 'Prompt is an informational inquiry seeking definitions, facts, or descriptions.',
          matchedSignals: ['FACTUAL_QUESTION_PATTERN']
        };
      }

      // 13. Unknown: Fallback when conservative heuristics do not confidently match
      return {
        category: TaskCategory.UNKNOWN,
        confidence: 0.50,
        reason: 'Conservative heuristics detected no clear single task pattern.',
        matchedSignals: ['FALLTHROUGH_UNKNOWN']
      };
    }
  }

  const defaultTaskClassifier = new TaskClassifier();

  return {
    TaskCategory,
    TaskClassifier,
    defaultTaskClassifier
  };
});
