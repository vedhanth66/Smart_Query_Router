/**
 * Smart Query Router - Detected Query Event Model
 * Defines the strict, typed internal data structure for detected query events.
 * Separates user-visible content from metadata, context, privacy classification,
 * and future optimization placeholders.
 * Strictly in-memory: content is NOT persisted.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    let normalizer = null;
    let featureExtractor = null;
    let contextDetector = null;
    try {
      normalizer = require('./normalizer');
    } catch (_) {}
    try {
      featureExtractor = require('./feature_extractor');
    } catch (_) {}
    try {
      contextDetector = require('./context_detector');
    } catch (_) {}
    module.exports = factory(normalizer, featureExtractor, contextDetector);
  } else {
    const normalizer = root.SmartQueryRouterNormalizer || null;
    const featureExtractor = root.SmartQueryRouterFeatureExtractor || null;
    const contextDetector = root.SmartQueryRouterContextDetector || null;
    root.SmartQueryRouterQueryEvent = factory(normalizer, featureExtractor, contextDetector);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (normalizerModule, featureExtractorModule, contextDetectorModule) {
  'use strict';

  // Common patterns indicating potential credentials or secrets in user input
  const SENSITIVE_PATTERNS = [
    { type: 'ANTHROPIC_KEY', regex: /sk-ant-[a-zA-Z0-9_\-]{20,}/ },
    { type: 'OPENAI_KEY', regex: /sk-[a-zA-Z0-9]{20,}/ },
    { type: 'GENERIC_BEARER', regex: /bearer\s+[a-zA-Z0-9_\-\.]{20,}/i },
    { type: 'PRIVATE_KEY', regex: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
    { type: 'AWS_KEY', regex: /AKIA[0-9A-Z]{16}/ },
    { type: 'PASSWORD_FIELD', regex: /password\s*[:=]\s*['"][^'"]+['"]/i }
  ];

  /**
   * Classify privacy of prompt text without persisting it
   * @param {string} promptText
   * @returns {{ level: 'STANDARD' | 'SENSITIVE', flags: string[] }}
   */
  function classifyPrivacy(promptText) {
    if (typeof promptText !== 'string' || promptText.length === 0) {
      return { level: 'STANDARD', flags: [] };
    }

    const flags = [];
    for (const pattern of SENSITIVE_PATTERNS) {
      if (pattern.regex.test(promptText)) {
        flags.push(pattern.type);
      }
    }

    return {
      level: flags.length > 0 ? 'SENSITIVE' : 'STANDARD',
      flags
    };
  }

  /**
   * Extract safe, non-sensitive page context from current browser location
   * @param {Location} loc
   * @param {string|null} activeModelHint
   */
  function extractSafeContext(loc, activeModelHint = null) {
    const origin = loc ? loc.origin : 'unknown';
    const pathname = loc ? loc.pathname : '';

    // Safely extract conversation UUID if on /chat/<uuid>
    let conversationId = null;
    const chatMatch = pathname.match(/^\/chat\/([a-zA-Z0-9_\-]+)/);
    if (chatMatch) {
      conversationId = chatMatch[1];
    }

    const isNewChat = pathname === '/new' || pathname.startsWith('/chat/new');

    return {
      origin,
      pathname,
      conversationId,
      isNewChat,
      activeModelHint: typeof activeModelHint === 'string' && activeModelHint.trim() ? activeModelHint.trim() : null
    };
  }

  /**
   * Factory to construct a DetectedQueryEvent
   * @param {object} params
   * @param {string} params.rawPrompt - The detected user prompt text
   * @param {string} params.triggerType - 'keyboard_enter' | 'button_click' | 'unknown'
   * @param {object} [params.context] - Safe page context
   */
  function createDetectedQueryEvent({ rawPrompt, triggerType = 'unknown', context = {} }) {
    const timestamp = Date.now();
    const randomSuffix = Math.random().toString(36).slice(2, 9);
    const eventId = `evt_${timestamp}_${randomSuffix}`;
    const raw = typeof rawPrompt === 'string' ? rawPrompt : '';

    // Apply safe semantics-preserving normalization
    const normalized = normalizerModule && normalizerModule.normalizeQueryText
      ? normalizerModule.normalizeQueryText(raw)
      : raw.trim().replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    const isNormalized = raw !== normalized;
    const charactersSaved = Math.max(0, raw.length - normalized.length);

    // Extract non-generative local features (heuristic signals, not final truth)
    const features = featureExtractorModule && featureExtractorModule.extractQueryFeatures
      ? featureExtractorModule.extractQueryFeatures(normalized || raw)
      : null;

    // First-pass context-dependency assessment (signals whether more context analysis is needed)
    const contextDependency = contextDetectorModule && contextDetectorModule.detectContextDependency
      ? contextDetectorModule.detectContextDependency(normalized || raw, features)
      : null;

    return {
      // 1. System and event metadata
      metadata: {
        eventId,
        timestamp,
        triggerType,
        schemaVersion: '1.0'
      },

      // 2. Safe page and session context (no cookies/auth tokens)
      context: {
        origin: context.origin || 'https://claude.ai',
        pathname: context.pathname || '',
        conversationId: context.conversationId || null,
        isNewChat: Boolean(context.isNewChat),
        activeModelHint: context.activeModelHint || null
      },

      // 3. User-visible content (isolated from metadata)
      // Preserves original raw text alongside semantics-preserving normalized form
      content: {
        rawPrompt: raw,
        normalizedPrompt: normalized,
        isNormalized,
        characterCount: raw.length,
        normalizedCharacterCount: normalized.length,
        charactersSaved,
        wordCount: normalized.length > 0 ? normalized.split(/\s+/).filter(Boolean).length : 0
      },

      // 4. Privacy classification (classified on normalized content)
      privacy: classifyPrivacy(normalized || raw),

      // 5. Inexpensive non-generative feature signals (not final truth; do not route on them yet)
      features,

      // 6. First-pass context-dependency assessment (decision on whether context analysis is needed)
      contextDependency,

      // 7. Placeholders for upcoming optimization decisions
      optimization: {
        status: 'PENDING',         // 'PENDING' | 'EVALUATED' | 'SKIPPED'
        decision: null,            // Future: 'PASS_THROUGH' | 'OPTIMIZE' | 'USE_CACHE'
        recommendedModel: null,    // Future: 'claude-3-5-haiku' | 'claude-3-5-sonnet' | 'claude-3-opus'
        cacheStatus: 'UNCHECKED',  // Future: 'UNCHECKED' | 'HIT' | 'MISS'
        optimizedPrompt: null,     // Future: trimmed or rewritten prompt text
        tokenSavingsEstimate: 0,   // Future: estimated tokens saved
        applied: false             // Whether an optimization was applied to the DOM
      }
    };
  }

  /**
   * Produces a sanitized summary safe for logging or telemetry.
   * STRICTLY strips rawPrompt and normalizedPrompt.
   * @param {object} queryEvent
   * @returns {object}
   */
  function toSafeSummary(queryEvent) {
    if (!queryEvent || !queryEvent.metadata) return null;

    return {
      eventId: queryEvent.metadata.eventId,
      timestamp: queryEvent.metadata.timestamp,
      triggerType: queryEvent.metadata.triggerType,
      conversationId: queryEvent.context ? queryEvent.context.conversationId : null,
      characterCount: queryEvent.content ? queryEvent.content.characterCount : 0,
      normalizedCharacterCount: queryEvent.content ? queryEvent.content.normalizedCharacterCount : 0,
      isNormalized: queryEvent.content ? Boolean(queryEvent.content.isNormalized) : false,
      charactersSaved: queryEvent.content ? queryEvent.content.charactersSaved : 0,
      wordCount: queryEvent.content ? queryEvent.content.wordCount : 0,
      privacyLevel: queryEvent.privacy ? queryEvent.privacy.level : 'UNKNOWN',
      requiresContextAnalysis: queryEvent.contextDependency ? Boolean(queryEvent.contextDependency.requiresContextAnalysis) : false,
      contextCategory: queryEvent.contextDependency ? queryEvent.contextDependency.category : 'STANDALONE',
      featuresSummary: queryEvent.features ? {
        estimatedTokens: queryEvent.features.length ? queryEvent.features.length.estimatedTokens : 0,
        hasCode: queryEvent.features.code ? queryEvent.features.code.hasCodeSyntax : false,
        hasList: queryEvent.features.lists ? queryEvent.features.lists.hasList : false,
        questionCount: queryEvent.features.questions ? queryEvent.features.questions.questionCount : 0,
        hasMath: queryEvent.features.math ? queryEvent.features.math.hasMathSymbols : false,
        hasUrl: queryEvent.features.urls ? queryEvent.features.urls.hasUrl : false,
        hasComparisonCue: queryEvent.features.cues ? queryEvent.features.cues.hasComparisonCue : false,
        hasReasoningCue: queryEvent.features.cues ? queryEvent.features.cues.hasReasoningCue : false
      } : null,
      optimizationStatus: queryEvent.optimization ? queryEvent.optimization.status : 'PENDING'
    };
  }

  /**
   * Validate that an object adheres to the DetectedQueryEvent schema
   * @param {any} evt
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateQueryEvent(evt) {
    if (!evt || typeof evt !== 'object') {
      return { valid: false, error: 'Event must be a non-null object' };
    }

    if (!evt.metadata || typeof evt.metadata.eventId !== 'string' || typeof evt.metadata.timestamp !== 'number') {
      return { valid: false, error: 'Invalid or missing metadata' };
    }

    if (!evt.context || typeof evt.context.origin !== 'string') {
      return { valid: false, error: 'Invalid or missing context' };
    }

    if (!evt.content || typeof evt.content.rawPrompt !== 'string' || typeof evt.content.characterCount !== 'number') {
      return { valid: false, error: 'Invalid or missing content' };
    }

    if (typeof evt.content.normalizedPrompt !== 'string') {
      return { valid: false, error: 'Invalid or missing normalizedPrompt' };
    }

    if (evt.features !== undefined && evt.features !== null && typeof evt.features !== 'object') {
      return { valid: false, error: 'Features partition must be an object' };
    }

    if (evt.contextDependency !== undefined && evt.contextDependency !== null && typeof evt.contextDependency !== 'object') {
      return { valid: false, error: 'contextDependency partition must be an object' };
    }

    if (!evt.privacy || typeof evt.privacy.level !== 'string' || !Array.isArray(evt.privacy.flags)) {
      return { valid: false, error: 'Invalid or missing privacy classification' };
    }

    if (!evt.optimization || typeof evt.optimization.status !== 'string') {
      return { valid: false, error: 'Invalid or missing optimization placeholders' };
    }

    return { valid: true };
  }

  return {
    createDetectedQueryEvent,
    classifyPrivacy,
    extractSafeContext,
    toSafeSummary,
    validateQueryEvent
  };
});
