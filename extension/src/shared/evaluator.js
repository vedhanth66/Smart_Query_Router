/**
 * Smart Query Router - Standardized Evaluator Result Structure & Pluggable Interface
 * 
 * DESIGN PRINCIPLES:
 * 1. Standardized evaluator result structure containing:
 *    - completeness (0.0 - 1.0)
 *    - confidence (0.0 - 1.0)
 *    - detected_issues (structured list of issues with severity and category)
 *    - escalation_recommendation (no_escalation, escalate_to_stronger_model, escalate_to_human, uncertain)
 * 2. Zero evaluator intelligence in this step: reference StubEvaluator provides baseline testing.
 * 3. Pluggable interface: BaseEvaluator and EvaluatorRegistry allow heuristic, classifier,
 *    or model-based evaluators to be plugged in later without altering callers.
 * 4. Strict decoupling: Kept separate from routing and model execution.
 * 5. Cross-environment UMD wrapper (Node.js, Service Worker, Content Scripts).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterEvaluator = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * Escalation recommendations
   */
  const EscalationRecommendation = Object.freeze({
    NO_ESCALATION: 'no_escalation',
    ESCALATE_TO_STRONGER_MODEL: 'escalate_to_stronger_model',
    ESCALATE_TO_HUMAN: 'escalate_to_human',
    UNCERTAIN: 'uncertain'
  });

  /**
   * Issue severity levels
   */
  const IssueSeverity = Object.freeze({
    LOW: 'low',
    MEDIUM: 'medium',
    HIGH: 'high',
    CRITICAL: 'critical'
  });

  /**
   * Issue categories
   */
  const IssueCategory = Object.freeze({
    COMPLETENESS: 'completeness',
    ACCURACY: 'accuracy',
    FORMATTING: 'formatting',
    SAFETY_POLICY: 'safety_policy',
    AMBIGUITY: 'ambiguity',
    OTHER: 'other'
  });

  /**
   * Evaluator implementation types
   */
  const EvaluatorType = Object.freeze({
    HEURISTIC: 'heuristic',
    CLASSIFIER: 'classifier',
    MODEL_BASED: 'model_based',
    STUB: 'stub'
  });

  /**
   * Create a standardized EvaluationIssue
   * @param {object} params
   * @param {string} params.issueCode
   * @param {string} params.message
   * @param {string} params.severity
   * @param {string} [params.category]
   * @param {string|null} [params.location]
   * @param {object} [params.metadata]
   * @returns {object}
   */
  function createEvaluationIssue({
    issueCode,
    message,
    severity,
    category = IssueCategory.OTHER,
    location = null,
    metadata = {}
  }) {
    if (!issueCode || typeof issueCode !== 'string') {
      throw new TypeError('issueCode must be a non-empty string');
    }
    if (!message || typeof message !== 'string') {
      throw new TypeError('message must be a non-empty string');
    }
    if (!Object.values(IssueSeverity).includes(severity)) {
      throw new TypeError(`Invalid severity: ${severity}`);
    }
    const validCategory = Object.values(IssueCategory).includes(category) ? category : IssueCategory.OTHER;

    return Object.freeze({
      issue_code: issueCode,
      message,
      severity,
      category: validCategory,
      location: location && typeof location === 'string' ? location : null,
      metadata: metadata && typeof metadata === 'object' ? Object.assign({}, metadata) : {}
    });
  }

  /**
   * Create a standardized EvaluationRequest
   * @param {object} params
   * @param {string} params.prompt
   * @param {string} params.candidateResponse
   * @param {string|null} [params.taskCategory]
   * @param {Array} [params.contextTurns]
   * @param {Array} [params.evaluationCriteria]
   * @param {string|null} [params.correlationId]
   * @param {object} [params.metadata]
   * @returns {object}
   */
  function createEvaluationRequest({
    prompt,
    candidateResponse,
    taskCategory = null,
    contextTurns = [],
    evaluationCriteria = [],
    correlationId = null,
    metadata = {}
  }) {
    if (!prompt || typeof prompt !== 'string') {
      throw new TypeError('prompt must be a non-empty string');
    }
    if (typeof candidateResponse !== 'string') {
      throw new TypeError('candidateResponse must be a string');
    }

    return Object.freeze({
      prompt,
      candidate_response: candidateResponse,
      task_category: taskCategory || null,
      context_turns: Array.isArray(contextTurns) ? [...contextTurns] : [],
      evaluation_criteria: Array.isArray(evaluationCriteria) ? [...evaluationCriteria] : [],
      correlation_id: correlationId || null,
      metadata: metadata && typeof metadata === 'object' ? Object.assign({}, metadata) : {}
    });
  }

  /**
   * Validate and create a standardized EvaluationResult
   * @param {object} params
   * @returns {object}
   */
  function createEvaluationResult({
    evaluationId,
    completeness,
    confidence,
    detectedIssues = [],
    escalationRecommendation,
    evaluatorId,
    evaluatorType,
    latencyMs = 0.0,
    explanation = null,
    metadata = {}
  }) {
    if (!evaluationId || typeof evaluationId !== 'string') {
      throw new TypeError('evaluationId must be a non-empty string');
    }
    const compNum = Number(completeness);
    if (isNaN(compNum) || compNum < 0.0 || compNum > 1.0) {
      throw new RangeError('completeness must be a number between 0.0 and 1.0');
    }
    const confNum = Number(confidence);
    if (isNaN(confNum) || confNum < 0.0 || confNum > 1.0) {
      throw new RangeError('confidence must be a number between 0.0 and 1.0');
    }
    if (!Object.values(EscalationRecommendation).includes(escalationRecommendation)) {
      throw new TypeError(`Invalid escalationRecommendation: ${escalationRecommendation}`);
    }
    if (!evaluatorId || typeof evaluatorId !== 'string') {
      throw new TypeError('evaluatorId must be a non-empty string');
    }
    if (!Object.values(EvaluatorType).includes(evaluatorType)) {
      throw new TypeError(`Invalid evaluatorType: ${evaluatorType}`);
    }

    return Object.freeze({
      evaluation_id: evaluationId,
      completeness: Number(compNum.toFixed(4)),
      confidence: Number(confNum.toFixed(4)),
      detected_issues: Array.isArray(detectedIssues) ? [...detectedIssues] : [],
      escalation_recommendation: escalationRecommendation,
      evaluator_id: evaluatorId,
      evaluator_type: evaluatorType,
      latency_ms: Number(Number(latencyMs || 0.0).toFixed(2)),
      explanation: explanation || null,
      metadata: metadata && typeof metadata === 'object' ? Object.assign({}, metadata) : {}
    });
  }

  /**
   * Base class defining the pluggable evaluator interface
   */
  class BaseEvaluator {
    constructor(evaluatorId, evaluatorType) {
      if (new.target === BaseEvaluator) {
        throw new TypeError('Cannot construct BaseEvaluator instances directly');
      }
      this._evaluatorId = evaluatorId;
      this._evaluatorType = evaluatorType;
    }

    get evaluatorId() {
      return this._evaluatorId;
    }

    get evaluatorType() {
      return this._evaluatorType;
    }

    async evaluate(request) {
      throw new Error('evaluate() must be implemented by subclass');
    }

    checkHealth() {
      return true;
    }
  }

  /**
   * Reference baseline evaluator validating the contract without intelligence
   */
  class StubEvaluator extends BaseEvaluator {
    constructor(options = {}) {
      super(options.evaluatorId || 'stub-evaluator-v1', EvaluatorType.STUB);
      this._defaultCompleteness = options.defaultCompleteness !== undefined ? options.defaultCompleteness : 1.0;
      this._defaultConfidence = options.defaultConfidence !== undefined ? options.defaultConfidence : 0.5;
      this._defaultRecommendation = options.defaultRecommendation || EscalationRecommendation.NO_ESCALATION;
    }

    async evaluate(request) {
      const start = Date.now();
      const evalId = request.correlationId
        ? `${request.correlationId}_eval_${start}`
        : `eval_${start}_${Math.random().toString(36).slice(2, 8)}`;

      return createEvaluationResult({
        evaluationId: evalId,
        completeness: this._defaultCompleteness,
        confidence: this._defaultConfidence,
        detectedIssues: [],
        escalationRecommendation: this._defaultRecommendation,
        evaluatorId: this.evaluatorId,
        evaluatorType: this.evaluatorType,
        latencyMs: Date.now() - start,
        explanation: 'Baseline stub evaluation completed without intelligence.',
        metadata: {
          stub_mode: true,
          prompt_length: request.prompt ? request.prompt.length : 0,
          response_length: request.candidate_response ? request.candidate_response.length : 0
        }
      });
    }
  }

  /**
   * Evaluator registry and coordinator
   */
  class EvaluatorRegistry {
    constructor(defaultEvaluator = null) {
      this._evaluators = new Map();
      this._defaultEvaluatorId = null;

      if (defaultEvaluator) {
        this.registerEvaluator(defaultEvaluator, true);
      }
    }

    registerEvaluator(evaluator, setDefault = false) {
      if (!(evaluator instanceof BaseEvaluator)) {
        throw new TypeError('Evaluator must inherit from BaseEvaluator');
      }
      this._evaluators.set(evaluator.evaluatorId, evaluator);
      if (setDefault || !this._defaultEvaluatorId) {
        this._defaultEvaluatorId = evaluator.evaluatorId;
      }
    }

    setDefaultEvaluator(evaluatorId) {
      if (!this._evaluators.has(evaluatorId)) {
        throw new Error(`Evaluator '${evaluatorId}' is not registered`);
      }
      this._defaultEvaluatorId = evaluatorId;
    }

    getEvaluator(evaluatorId = null) {
      const targetId = evaluatorId || this._defaultEvaluatorId;
      if (!targetId || !this._evaluators.has(targetId)) {
        throw new Error(`No evaluator registered for ID '${targetId}'`);
      }
      return this._evaluators.get(targetId);
    }

    listEvaluators() {
      return Array.from(this._evaluators.keys());
    }

    async evaluate(request, evaluatorId = null) {
      const evaluator = this.getEvaluator(evaluatorId);
      return await evaluator.evaluate(request);
    }
  }

  /**
   * Deterministic heuristic evaluator for obvious incompleteness
   */
  class HeuristicIncompletenessEvaluator extends BaseEvaluator {
    constructor(options = {}) {
      super(options.evaluatorId || 'heuristic-incompleteness-v1', EvaluatorType.HEURISTIC);
      this._wordToNum = {
        one: 1, two: 2, three: 3, four: 4, five: 5,
        six: 6, seven: 7, eight: 8, nine: 9, ten: 10
      };
      this._countItemsRegex = /(?:examples|reasons|options|tips|ideas|steps|ways|items|points|functions|questions|bullet points|bullets|sections|fields|keys|methods|recommendations|approaches|alternatives|benefits|features|principles|components|advantages|disadvantages|drawbacks)/i;
    }

    async evaluate(request) {
      const start = Date.now();
      const prompt = (request.prompt || '').trim();
      const response = (request.candidate_response || '').trim();
      const issues = [];

      // 1. Empty or trivial response check
      const emptyIssue = this._checkEmptyOrTrivial(response, prompt);
      if (emptyIssue) {
        issues.push(emptyIssue);
        return this._buildResult(request, 0.0, 0.99, issues, Date.now() - start, 'Candidate response is empty or contains no alphanumeric content.');
      }

      // 2. Premature truncation check
      const truncIssue = this._checkPrematureTruncation(response, prompt);
      if (truncIssue) {
        issues.push(truncIssue);
      }

      // 3. Explicit requested count mismatch check (items, bullets, sections)
      const { issue: countIssue, completeness: countCompleteness } = this._checkRequestedCountMismatch(response, prompt);
      if (countIssue) {
        issues.push(countIssue);
      }

      // 4. Bullet structure presence when bullets requested without count
      const { issue: bulletIssue, completeness: bulletCompleteness } = this._checkBulletStructurePresence(response, prompt);
      if (bulletIssue) {
        issues.push(bulletIssue);
      }

      // 5. Missing required sections check
      const { issue: secIssue, completeness: secCompleteness } = this._checkMissingRequiredSections(response, prompt);
      if (secIssue) {
        issues.push(secIssue);
      }

      // 6. Missing required output fields check
      const { issue: fieldIssue, completeness: fieldCompleteness } = this._checkMissingRequiredFields(response, prompt);
      if (fieldIssue) {
        issues.push(fieldIssue);
      }

      let completeness = 1.0;
      if (issues.length > 0) {
        if (countCompleteness !== null) completeness = Math.min(completeness, countCompleteness);
        if (bulletCompleteness !== null) completeness = Math.min(completeness, bulletCompleteness);
        if (secCompleteness !== null) completeness = Math.min(completeness, secCompleteness);
        if (fieldCompleteness !== null) completeness = Math.min(completeness, fieldCompleteness);
        if (issues.some(i => i.issue_code === 'PREMATURE_TRUNCATION')) completeness = Math.min(completeness, 0.4);
        completeness = Math.min(completeness, 0.7);
      }

      const summary = issues.length > 0
        ? `Detected ${issues.length} structural or incompleteness issue(s).`
        : 'No obvious incompleteness detected.';

      return this._buildResult(
        request,
        completeness,
        issues.length > 0 ? 0.85 : 0.80,
        issues,
        Date.now() - start,
        summary
      );
    }

    _checkEmptyOrTrivial(response, prompt) {
      if (!response) {
        return createEvaluationIssue({
          issueCode: 'EMPTY_OR_TRIVIAL_RESPONSE',
          message: 'Candidate response is completely empty or contains only whitespace.',
          severity: IssueSeverity.CRITICAL,
          category: IssueCategory.COMPLETENESS,
          metadata: { response_length: 0 }
        });
      }
      if (!/[a-zA-Z0-9]/.test(response)) {
        return createEvaluationIssue({
          issueCode: 'EMPTY_OR_TRIVIAL_RESPONSE',
          message: 'Candidate response contains no alphanumeric content (only punctuation or whitespace).',
          severity: IssueSeverity.CRITICAL,
          category: IssueCategory.COMPLETENESS,
          metadata: { response_preview: response.slice(0, 30) }
        });
      }
      return null;
    }

    _checkPrematureTruncation(response, prompt) {
      // Unclosed code fence (odd number of ```)
      const codeFenceMatches = response.match(/```/g);
      if (codeFenceMatches && codeFenceMatches.length % 2 !== 0) {
        return createEvaluationIssue({
          issueCode: 'PREMATURE_TRUNCATION',
          message: 'Candidate response ended with an unclosed markdown code fence, indicating premature cutoff.',
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: { reason: 'unclosed_code_fence', fence_count: codeFenceMatches.length }
        });
      }

      // Intentional ellipsis is not truncation
      if (response.endsWith('...') || response.endsWith('…')) {
        return null;
      }

      // Properly closed fences
      if (response.endsWith('```') || response.endsWith("'''") || response.endsWith('"""')) {
        return null;
      }

      // Trailing dangling connector words
      const trailingConnector = response.match(/\b(and|or|because|with|such as|for example|including|the|a|an|to|of|in|that|which|is|are|was|were)\s*$/i);
      if (trailingConnector) {
        return createEvaluationIssue({
          issueCode: 'PREMATURE_TRUNCATION',
          message: `Candidate response terminated abruptly with trailing connector word '${trailingConnector[1]}'.`,
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: { reason: 'trailing_connector', token: trailingConnector[1] }
        });
      }

      // Trailing comma or semicolon
      if (/[,;]\s*$/.test(response)) {
        return createEvaluationIssue({
          issueCode: 'PREMATURE_TRUNCATION',
          message: 'Candidate response terminated abruptly with trailing comma or semicolon.',
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: { reason: 'dangling_punctuation' }
        });
      }

      return null;
    }

    _checkRequestedCountMismatch(response, prompt) {
      const countInfo = this._extractRequestedStructureCount(prompt);
      if (!countInfo) {
        return { issue: null, completeness: null };
      }
      const { expectedCount, structureType } = countInfo;
      if (expectedCount < 2) {
        return { issue: null, completeness: null };
      }

      const actualCount = this._countResponseItems(response, structureType);
      if (actualCount < expectedCount) {
        const comp = expectedCount > 0 ? Number((actualCount / expectedCount).toFixed(2)) : 0.0;
        const nounDesc = structureType === 'bullets' ? 'bullet points' : (structureType === 'sections' ? 'sections' : 'items');
        const issue = createEvaluationIssue({
          issueCode: 'REQUESTED_COUNT_MISMATCH',
          message: `Prompt explicitly requested ${expectedCount} ${nounDesc}, but response only provided ${actualCount}.`,
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: {
            expected_count: expectedCount,
            actual_count: actualCount,
            structure_type: structureType
          }
        });
        return { issue, completeness: comp };
      }

      return { issue: null, completeness: null };
    }

    _extractRequestedStructureCount(prompt) {
      // Guard against math expressions: '5 + 3', '10 / 2'
      if (/\b\d+\s*[\+\-\*\/]\s*\d+\b/.test(prompt)) return null;
      // Guard against HTTP codes
      if (/\b(?:http|status|error|code)\s+\d{3}\b/i.test(prompt)) return null;

      const pattern1 = /\b(?:give|provide|list|name|write|show|suggest|generate)\s+(?:me\s+)?(?:at\s+least\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+((?:examples|reasons|options|tips|ideas|steps|ways|items|points|functions|questions|bullet points|bullets|sections|fields|keys|methods|recommendations|approaches|alternatives|benefits|features|principles|components|advantages|disadvantages|drawbacks))\b/i;
      let match = prompt.match(pattern1);
      if (!match) {
        const pattern2 = /\b(?:in|using|with|divide\s+(?:this\s+)?into)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+((?:examples|reasons|options|tips|ideas|steps|ways|items|points|functions|questions|bullet points|bullets|sections|fields|keys|methods|recommendations|approaches|alternatives|benefits|features|principles|components|advantages|disadvantages|drawbacks))\b/i;
        match = prompt.match(pattern2);
      }
      if (!match) {
        const pattern3 = /\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+((?:examples|reasons|options|tips|ideas|steps|ways|items|points|functions|questions|bullet points|bullets|sections|fields|keys|methods|recommendations|approaches|alternatives|benefits|features|principles|components|advantages|disadvantages|drawbacks))\b/i;
        match = prompt.match(pattern3);
      }

      if (match) {
        const rawNum = match[1].toLowerCase();
        const matchedNoun = (match[2] || '').toLowerCase();
        let num = null;
        if (/^\d+$/.test(rawNum)) {
          num = parseInt(rawNum, 10);
        } else if (this._wordToNum[rawNum]) {
          num = this._wordToNum[rawNum];
        }

        if (num && num >= 2 && num <= 50) {
          let structType = 'items';
          if (matchedNoun.includes('bullet')) {
            structType = 'bullets';
          } else if (matchedNoun.includes('section')) {
            structType = 'sections';
          }
          return { expectedCount: num, structureType: structType };
        }
      }
      return null;
    }

    _extractRequestedCount(prompt) {
      const res = this._extractRequestedStructureCount(prompt);
      return res ? res.expectedCount : null;
    }

    _countResponseItems(response, structureType = 'items') {
      // 1. Numbered lists: '1.', '2)', '(1)'
      const numMatches = response.match(/^\s*(?:\d+[\.\)]|\(\d+\))\s+/gm);
      const numCount = numMatches ? numMatches.length : 0;

      // 2. Bullet points: '-', '*', '•'
      const bulletMatches = response.match(/^\s*[-*•]\s+/gm);
      const bulletCount = bulletMatches ? bulletMatches.length : 0;

      // 3. Headings with items
      const headingMatches = response.match(/^\s*#{1,4}\s+(?:\d+[\.\)]|[A-Z])/gm);
      const headingCount = headingMatches ? headingMatches.length : 0;

      // All markdown headings
      const allHeadings = response.match(/^\s*#{1,4}\s+\S+/gm);
      const allHeadingCount = allHeadings ? allHeadings.length : 0;

      // Bold section markers at line start
      const boldSections = response.match(/^\s*(?:\*\*[^*]+\*\*|__[^_]+__):?/gm);
      const boldCount = boldSections ? boldSections.length : 0;

      if (structureType === 'bullets') {
        if (bulletCount > 0) return bulletCount;
        if (numCount > 0) return numCount; // stylistic leniency
        return 0;
      }

      if (structureType === 'sections') {
        return Math.max(allHeadingCount, boldCount, headingCount);
      }

      // 4. Ordinal narrative enumeration
      const ordinals = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth'];
      const found = new Set();
      for (const ord of ordinals) {
        if (new RegExp(`\\b${ord}\\b`, 'i').test(response)) {
          found.add(ord);
        }
      }
      const ordinalCount = found.size >= 2 ? found.size : 0;

      return Math.max(numCount, bulletCount, headingCount, ordinalCount);
    }

    _checkBulletStructurePresence(response, prompt) {
      const bulletRequestPattern = /\b(?:in|as|using|with)\s+bullet\s+(?:points?|items?)\b|\b(?:in|as|use)\s+bullets\b|\busing\s+bullets\b/i;
      if (!bulletRequestPattern.test(prompt)) {
        return { issue: null, completeness: null };
      }

      const hasBullets = /^\s*[-*•]\s+/m.test(response);
      const hasNumbered = /^\s*(?:\d+[\.\)]|\(\d+\))\s+/m.test(response);

      if (!hasBullets && !hasNumbered) {
        const issue = createEvaluationIssue({
          issueCode: 'MISSING_REQUIRED_BULLETS',
          message: 'Prompt explicitly requested bullet points, but candidate response did not contain any bullet points.',
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: { structure_type: 'bullets' }
        });
        return { issue, completeness: 0.5 };
      }

      return { issue: null, completeness: null };
    }

    _checkMissingRequiredSections(response, prompt) {
      const requiredSections = this._extractRequiredSections(prompt);
      if (!requiredSections || requiredSections.length === 0) {
        return { issue: null, completeness: null };
      }

      const missing = [];
      for (const sec of requiredSections) {
        const escaped = sec.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const pattern = new RegExp(`(?:^#{1,4}\\s+.*?\\b${escaped}\\b|\\*\\*${escaped}:?\\*\\*|^\\s*${escaped}:)`, 'im');
        if (!pattern.test(response)) {
          if (sec.toLowerCase() === 'code' && response.includes('```')) {
            continue;
          }
          missing.push(sec);
        }
      }

      if (missing.length > 0) {
        const comp = Number(((requiredSections.length - missing.length) / requiredSections.length).toFixed(2));
        const issue = createEvaluationIssue({
          issueCode: 'MISSING_REQUIRED_SECTION',
          message: `Prompt explicitly required structured sections (${requiredSections.join(', ')}), but response omitted: ${missing.join(', ')}.`,
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: { required_sections: requiredSections, missing_sections: missing }
        });
        return { issue, completeness: comp };
      }

      return { issue: null, completeness: null };
    }

    _extractRequiredSections(prompt) {
      if (/\bpros\s+(?:and|&)\s+cons\b/i.test(prompt)) {
        return ['Pros', 'Cons'];
      }
      if (/\b(?:both\s+)?explanation\s+(?:and|&)\s+code\b/i.test(prompt)) {
        return ['Explanation', 'Code'];
      }
      const match = prompt.match(/\b(?:include|provide|with|create)\s+(?:the\s+)?(?:following\s+)?sections?:?\s*([A-Za-z0-9,\s\-&/]+?)(?:\.|$|\n)/i);
      if (match) {
        const parts = match[1].split(/,|\band\b/i).map(s => s.trim()).filter(Boolean);
        const valid = parts.filter(p => p.length <= 30 && /^[A-Za-z0-9\s\-]+$/.test(p));
        if (valid.length >= 2) return valid;
      }
      return [];
    }

    _checkMissingRequiredFields(response, prompt) {
      const requiredFields = this._extractRequiredFields(prompt);
      if (!requiredFields || requiredFields.length === 0) {
        return { issue: null, completeness: null };
      }

      const missing = [];
      for (const field of requiredFields) {
        if (!this._isFieldPresentInResponse(field, response)) {
          missing.push(field);
        }
      }

      if (missing.length > 0) {
        const comp = Number(((requiredFields.length - missing.length) / requiredFields.length).toFixed(2));
        const issue = createEvaluationIssue({
          issueCode: 'MISSING_REQUIRED_FIELD',
          message: `Prompt explicitly requested output fields (${requiredFields.join(', ')}), but response omitted: ${missing.join(', ')}.`,
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS,
          metadata: {
            required_fields: requiredFields,
            missing_fields: missing,
            structure_type: 'output_fields'
          }
        });
        return { issue, completeness: comp };
      }

      return { issue: null, completeness: null };
    }

    _extractRequiredFields(prompt) {
      // False-positive guard 1: "in the field of ...", "field of study", "across the field"
      if (/\b(?:in|of|across|within)\s+(?:the\s+)?field\s+of\b/i.test(prompt)) {
        return [];
      }
      // False-positive guard 2: physical sports or science fields
      if (/\b(?:football|baseball|soccer|playing|sports|battle|magnetic|electric|gravitational)\s+fields?\b/i.test(prompt)) {
        return [];
      }
      if (/\bfield\s+(?:hockey|trip|goals?|work|test|guide)\b|\btrack\s+and\s+field\b/i.test(prompt)) {
        return [];
      }

      const patterns = [
        /\b(?:output\s+)?(?:fields|keys|attributes):\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)/i,
        /\b(?:with|include|containing|provide|return|format as|having)\s+(?:the\s+)?(?:following\s+)?(?:fields|keys|attributes):?\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)/i,
        /\b(?:json|yaml|object|dictionary|record)\s+(?:with|containing)\s+(?:the\s+)?(?:fields|keys|attributes):?\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)/i
      ];

      for (const pat of patterns) {
        const match = prompt.match(pat);
        if (match) {
          const raw = match[1].trim();
          const tokens = raw.split(/,|\band\b/i);
          const cleanFields = [];
          for (const t of tokens) {
            const cleaned = t.replace(/[`'\"\[\]:]/g, '').trim();
            if (cleaned && !['and', 'or', 'the', 'a', 'an', 'with', 'fields', 'keys', 'attributes'].includes(cleaned.toLowerCase())) {
              if (/^[A-Za-z0-9_]{1,32}$/.test(cleaned)) {
                cleanFields.push(cleaned);
              }
            }
          }
          if (cleanFields.length >= 2 || (cleanFields.length > 0 && match[0].includes(':'))) {
            const seen = new Set();
            const unique = [];
            for (const f of cleanFields) {
              const fl = f.toLowerCase();
              if (!seen.has(fl)) {
                seen.add(fl);
                unique.push(f);
              }
            }
            if (unique.length > 0) return unique;
          }
        }
      }
      return [];
    }

    _isFieldPresentInResponse(field, response) {
      const escaped = field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const escapedFlexible = escaped.replace(/[_\-\s]+/g, '[\\s_-]+');
      const pattern = new RegExp(
        `(?:["']${escapedFlexible}["']\\s*:|` +
        `^\\s*#{1,4}\\s+.*?\\b${escapedFlexible}\\b|` +
        `\\b${escapedFlexible}\\b\\s*:|` +
        `\\*\\*${escapedFlexible}:?\\*\\*|` +
        `^\\s*[-*•]\\s+.*?${escapedFlexible}\\b\\s*:|` +
        `\\b${escapedFlexible}\\s*=)`,
        'im'
      );
      return pattern.test(response);
    }

    _buildResult(request, completeness, confidence, issues, elapsedMs, summary) {
      const start = Date.now();
      const evalId = request.correlation_id
        ? `${request.correlation_id}_eval_${start}`
        : `eval_${start}_${Math.random().toString(36).slice(2, 8)}`;

      return createEvaluationResult({
        evaluationId: evalId,
        completeness,
        confidence,
        detectedIssues: issues,
        escalationRecommendation: issues.length > 0
          ? EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
          : EscalationRecommendation.NO_ESCALATION,
        evaluatorId: this.evaluatorId,
        evaluatorType: this.evaluatorType,
        latencyMs: elapsedMs,
        explanation: `${summary} (Heuristic incompleteness check only; does not verify factual accuracy.)`,
        metadata: {
          factual_correctness_verified: false,
          incompleteness_heuristics_applied: [
            'empty_or_trivial',
            'premature_truncation',
            'requested_count_mismatch',
            'missing_required_sections',
            'missing_required_fields',
            'bullet_structure_consistency',
            'structural_consistency_checks'
          ],
          issues_count: issues.length
        }
      });
    }
  }

  const defaultEvaluatorRegistry = new EvaluatorRegistry(new StubEvaluator());
  defaultEvaluatorRegistry.registerEvaluator(new HeuristicIncompletenessEvaluator());

  return {
    EscalationRecommendation,
    IssueSeverity,
    IssueCategory,
    EvaluatorType,
    createEvaluationIssue,
    createEvaluationRequest,
    createEvaluationResult,
    BaseEvaluator,
    StubEvaluator,
    HeuristicIncompletenessEvaluator,
    EvaluatorRegistry,
    defaultEvaluatorRegistry
  };
});

