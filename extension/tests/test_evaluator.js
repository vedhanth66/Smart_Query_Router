/**
 * Smart Query Router - Unit Tests for Evaluator Result Structure & Pluggable Interface
 * 
 * Verifies:
 * 1. Standardized enum definitions (EscalationRecommendation, IssueSeverity, IssueCategory, EvaluatorType).
 * 2. createEvaluationIssue validation and defaults.
 * 3. createEvaluationRequest parameter validation.
 * 4. createEvaluationResult schema integrity, completeness/confidence bounds, and immutability.
 * 5. StubEvaluator baseline execution without intelligence.
 * 6. Pluggability of heuristic evaluators.
 * 7. Pluggability of classifier evaluators.
 * 8. Pluggability of model-based evaluators.
 * 9. EvaluatorRegistry management, registration, and dispatch.
 */

const assert = require('assert');
const {
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
} = require('../src/shared/evaluator');

console.log('--- Running Evaluator Interface & Result Structure Tests ---');

// Test 1: Enums
console.log('Test 1: Verifying evaluator enums...');
assert.strictEqual(EscalationRecommendation.NO_ESCALATION, 'no_escalation');
assert.strictEqual(EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL, 'escalate_to_stronger_model');
assert.strictEqual(EscalationRecommendation.ESCALATE_TO_HUMAN, 'escalate_to_human');
assert.strictEqual(EscalationRecommendation.UNCERTAIN, 'uncertain');

assert.strictEqual(IssueSeverity.LOW, 'low');
assert.strictEqual(IssueSeverity.MEDIUM, 'medium');
assert.strictEqual(IssueSeverity.HIGH, 'high');
assert.strictEqual(IssueSeverity.CRITICAL, 'critical');

assert.strictEqual(EvaluatorType.HEURISTIC, 'heuristic');
assert.strictEqual(EvaluatorType.CLASSIFIER, 'classifier');
assert.strictEqual(EvaluatorType.MODEL_BASED, 'model_based');
assert.strictEqual(EvaluatorType.STUB, 'stub');
console.log('PASS: Evaluator enums verified');

// Test 2: createEvaluationIssue
console.log('Test 2: createEvaluationIssue validation...');
const issue = createEvaluationIssue({
  issueCode: 'MISSING_EXPLANATION',
  message: 'Result missing step-by-step rationale',
  severity: IssueSeverity.MEDIUM,
  category: IssueCategory.COMPLETENESS,
  location: 'section 2'
});
assert.strictEqual(issue.issue_code, 'MISSING_EXPLANATION');
assert.strictEqual(issue.severity, 'medium');
assert.strictEqual(issue.category, 'completeness');
assert.strictEqual(issue.location, 'section 2');

// Rejects invalid severity
assert.throws(() => {
  createEvaluationIssue({ issueCode: 'E1', message: 'M1', severity: 'invalid_sev' });
}, /Invalid severity/);
console.log('PASS: createEvaluationIssue validation verified');

// Test 3: createEvaluationRequest
console.log('Test 3: createEvaluationRequest validation...');
const req = createEvaluationRequest({
  prompt: 'Solve quadratic equation 2x^2 + 4x - 6 = 0',
  candidateResponse: 'x = 1, x = -3',
  taskCategory: 'arithmetic',
  correlationId: 'corr_eval_101'
});
assert.strictEqual(req.prompt, 'Solve quadratic equation 2x^2 + 4x - 6 = 0');
assert.strictEqual(req.candidate_response, 'x = 1, x = -3');
assert.strictEqual(req.task_category, 'arithmetic');
assert.strictEqual(req.correlation_id, 'corr_eval_101');
console.log('PASS: createEvaluationRequest validation verified');

// Test 4: createEvaluationResult bounds and constraints
console.log('Test 4: createEvaluationResult bounds validation...');
const res = createEvaluationResult({
  evaluationId: 'eval_res_001',
  completeness: 0.95,
  confidence: 0.90,
  detectedIssues: [issue],
  escalationRecommendation: EscalationRecommendation.NO_ESCALATION,
  evaluatorId: 'test-eval',
  evaluatorType: EvaluatorType.HEURISTIC
});
assert.strictEqual(res.completeness, 0.95);
assert.strictEqual(res.confidence, 0.90);
assert.strictEqual(res.escalation_recommendation, 'no_escalation');
assert.strictEqual(res.detected_issues.length, 1);

// Out of bounds completeness
assert.throws(() => {
  createEvaluationResult({
    evaluationId: 'e',
    completeness: 1.1,
    confidence: 0.5,
    escalationRecommendation: EscalationRecommendation.NO_ESCALATION,
    evaluatorId: 'ev',
    evaluatorType: EvaluatorType.STUB
  });
}, /completeness must be a number between 0.0 and 1.0/);

// Out of bounds confidence
assert.throws(() => {
  createEvaluationResult({
    evaluationId: 'e',
    completeness: 0.5,
    confidence: -0.1,
    escalationRecommendation: EscalationRecommendation.NO_ESCALATION,
    evaluatorId: 'ev',
    evaluatorType: EvaluatorType.STUB
  });
}, /confidence must be a number between 0.0 and 1.0/);
console.log('PASS: createEvaluationResult bounds validation verified');

// Test 5: StubEvaluator baseline execution without intelligence
console.log('Test 5: StubEvaluator execution without intelligence...');
(async () => {
  const stub = new StubEvaluator();
  assert.strictEqual(stub.evaluatorId, 'stub-evaluator-v1');
  assert.strictEqual(stub.evaluatorType, EvaluatorType.STUB);

  const evalOutcome = await stub.evaluate(req);
  assert.strictEqual(evalOutcome.completeness, 1.0);
  assert.strictEqual(evalOutcome.confidence, 0.5);
  assert.strictEqual(evalOutcome.detected_issues.length, 0);
  assert.strictEqual(evalOutcome.escalation_recommendation, EscalationRecommendation.NO_ESCALATION);
  assert.strictEqual(evalOutcome.metadata.stub_mode, true);
  console.log('PASS: StubEvaluator execution verified');

  // Test 6: Pluggable Heuristic Evaluator
  console.log('Test 6: Pluggable heuristic evaluator...');
  class MockHeuristicEvaluator extends BaseEvaluator {
    constructor() {
      super('mock-heuristic-v1', EvaluatorType.HEURISTIC);
    }
    async evaluate(r) {
      const issues = [];
      let completeness = 1.0;
      if (r.prompt.includes('code') && !r.candidate_response.includes('```')) {
        issues.push(createEvaluationIssue({
          issueCode: 'MISSING_CODE_BLOCK',
          message: 'Code requested but not formatted in markdown fence',
          severity: IssueSeverity.HIGH,
          category: IssueCategory.COMPLETENESS
        }));
        completeness = 0.3;
      }
      return createEvaluationResult({
        evaluationId: 'eval_heur_01',
        completeness,
        confidence: 0.85,
        detectedIssues: issues,
        escalationRecommendation: issues.length > 0 ? EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL : EscalationRecommendation.NO_ESCALATION,
        evaluatorId: this.evaluatorId,
        evaluatorType: this.evaluatorType
      });
    }
  }

  const heurEval = new MockHeuristicEvaluator();
  const heurReq = createEvaluationRequest({
    prompt: 'Write python code for bubble sort',
    candidateResponse: 'Bubble sort swaps adjacent elements if they are in wrong order.'
  });
  const heurRes = await heurEval.evaluate(heurReq);
  assert.strictEqual(heurRes.completeness, 0.3);
  assert.strictEqual(heurRes.detected_issues[0].issue_code, 'MISSING_CODE_BLOCK');
  assert.strictEqual(heurRes.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  console.log('PASS: Pluggable heuristic evaluator verified');

  // Test 7: Pluggable Classifier Evaluator
  console.log('Test 7: Pluggable classifier evaluator...');
  class MockClassifierEvaluator extends BaseEvaluator {
    constructor() {
      super('mock-classifier-v1', EvaluatorType.CLASSIFIER);
    }
    async evaluate(r) {
      return createEvaluationResult({
        evaluationId: 'eval_cls_01',
        completeness: 0.92,
        confidence: 0.94,
        detectedIssues: [],
        escalationRecommendation: EscalationRecommendation.NO_ESCALATION,
        evaluatorId: this.evaluatorId,
        evaluatorType: this.evaluatorType,
        metadata: { model_accuracy: 0.96 }
      });
    }
  }
  const clsEval = new MockClassifierEvaluator();
  const clsRes = await clsEval.evaluate(req);
  assert.strictEqual(clsRes.evaluator_type, EvaluatorType.CLASSIFIER);
  assert.strictEqual(clsRes.confidence, 0.94);
  console.log('PASS: Pluggable classifier evaluator verified');

  // Test 8: Pluggable Model-Based Evaluator
  console.log('Test 8: Pluggable model-based evaluator...');
  class MockModelBasedEvaluator extends BaseEvaluator {
    constructor() {
      super('mock-llm-judge-v1', EvaluatorType.MODEL_BASED);
    }
    async evaluate(r) {
      const issue = createEvaluationIssue({
        issueCode: 'SAFETY_VIOLATION',
        message: 'Prompt requests restricted content',
        severity: IssueSeverity.CRITICAL,
        category: IssueCategory.SAFETY_POLICY
      });
      return createEvaluationResult({
        evaluationId: 'eval_llm_01',
        completeness: 0.0,
        confidence: 0.99,
        detectedIssues: [issue],
        escalationRecommendation: EscalationRecommendation.ESCALATE_TO_HUMAN,
        evaluatorId: this.evaluatorId,
        evaluatorType: this.evaluatorType
      });
    }
  }
  const judgeEval = new MockModelBasedEvaluator();
  const judgeRes = await judgeEval.evaluate(req);
  assert.strictEqual(judgeRes.evaluator_type, EvaluatorType.MODEL_BASED);
  assert.strictEqual(judgeRes.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_HUMAN);
  console.log('PASS: Pluggable model-based evaluator verified');

  // Test 9: EvaluatorRegistry management
  console.log('Test 9: EvaluatorRegistry management...');
  const registry = new EvaluatorRegistry(new StubEvaluator());
  registry.registerEvaluator(heurEval);
  registry.registerEvaluator(clsEval);

  assert.deepStrictEqual(registry.listEvaluators(), ['stub-evaluator-v1', 'mock-heuristic-v1', 'mock-classifier-v1']);
  assert.strictEqual(registry.getEvaluator().evaluatorId, 'stub-evaluator-v1');
  assert.strictEqual(registry.getEvaluator('mock-heuristic-v1').evaluatorId, 'mock-heuristic-v1');

  const regRes = await registry.evaluate(heurReq, 'mock-heuristic-v1');
  assert.strictEqual(regRes.evaluator_id, 'mock-heuristic-v1');

  assert.throws(() => {
    registry.getEvaluator('unknown_id');
  }, /No evaluator registered for ID/);

  console.log('PASS: EvaluatorRegistry management verified');

  // Test 10: HeuristicIncompletenessEvaluator failure pattern detection
  console.log('Test 10: HeuristicIncompletenessEvaluator failure pattern detection...');
  const heurIncomplete = new HeuristicIncompletenessEvaluator();

  // 10.1: Empty answer
  const resEmpty = await heurIncomplete.evaluate({ prompt: 'Explain gravity', candidate_response: '   ' });
  assert.strictEqual(resEmpty.completeness, 0.0);
  assert.strictEqual(resEmpty.detected_issues[0].issue_code, 'EMPTY_OR_TRIVIAL_RESPONSE');
  assert.strictEqual(resEmpty.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);

  // 10.2: Premature truncation via unclosed code fence
  const resTrunc = await heurIncomplete.evaluate({
    prompt: 'Write python code',
    candidate_response: 'Here is code:\n```python\nprint(123)'
  });
  assert(resTrunc.completeness <= 0.4);
  assert(resTrunc.detected_issues.some(i => i.issue_code === 'PREMATURE_TRUNCATION'));

  // 10.3: Premature truncation via trailing connector
  const resTrailing = await heurIncomplete.evaluate({
    prompt: 'Why is the sky blue?',
    candidate_response: 'The sky appears blue because Rayleigh scattering occurs with'
  });
  assert(resTrailing.detected_issues.some(i => i.issue_code === 'PREMATURE_TRUNCATION'));

  // 10.4: Requested count mismatch
  const resCount = await heurIncomplete.evaluate({
    prompt: 'List 5 reasons to drink water',
    candidate_response: '1. Hydration helps energy.\n2. Flushes out toxins.'
  });
  assert(resCount.detected_issues.some(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH'));
  assert.strictEqual(resCount.completeness, 0.4);

  // 10.5: Missing required sections
  const resSec = await heurIncomplete.evaluate({
    prompt: 'Evaluate switching to microservices. Provide Pros and Cons.',
    candidate_response: '### Pros\n- Scalable teams\n- Independent deployments'
  });
  assert(resSec.detected_issues.some(i => i.issue_code === 'MISSING_REQUIRED_SECTION'));
  console.log('PASS: Heuristic failure patterns detected verified');

  // Test 11: False-positive prevention
  console.log('Test 11: HeuristicIncompletenessEvaluator false-positive prevention...');
  // 11.1: Dates in query
  const fpDate = await heurIncomplete.evaluate({
    prompt: 'What major events happened in 1999?',
    candidate_response: 'In 1999, the Euro was officially introduced as an electronic currency.'
  });
  assert(!fpDate.detected_issues.some(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH'));
  assert.strictEqual(fpDate.completeness, 1.0);

  // 11.2: HTTP code in query
  const fpHttp = await heurIncomplete.evaluate({
    prompt: 'Explain what an HTTP 404 error means',
    candidate_response: 'An HTTP 404 error means the requested server resource was not found.'
  });
  assert(!fpHttp.detected_issues.some(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH'));
  assert.strictEqual(fpHttp.completeness, 1.0);

  // 11.3: Math expression in query
  const fpMath = await heurIncomplete.evaluate({
    prompt: 'What is 5 + 3?',
    candidate_response: '8'
  });
  assert(!fpMath.detected_issues.some(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH'));
  assert(!fpMath.detected_issues.some(i => i.issue_code === 'EMPTY_OR_TRIVIAL_RESPONSE'));

  // 11.4: Intentional ellipsis
  const fpEllipsis = await heurIncomplete.evaluate({
    prompt: 'Write a mysterious ending',
    candidate_response: 'The figure disappeared into the foggy night...'
  });
  assert(!fpEllipsis.detected_issues.some(i => i.issue_code === 'PREMATURE_TRUNCATION'));

  // 11.5: Narrative ordinals satisfying count
  const fpNarrative = await heurIncomplete.evaluate({
    prompt: 'Give 3 steps to bake bread',
    candidate_response: 'First, mix the flour and yeast. Second, knead the dough until smooth. Third, bake at 200 degrees.'
  });
  assert(!fpNarrative.detected_issues.some(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH'));
  assert.strictEqual(fpNarrative.completeness, 1.0);
  console.log('PASS: False-positive prevention verified');

  // Test 12: Strict non-factual disclaimer audit
  console.log('Test 12: Strict non-factual disclaimer audit...');
  assert.strictEqual(resEmpty.metadata.factual_correctness_verified, false);
  assert.strictEqual(fpDate.metadata.factual_correctness_verified, false);
  assert(fpDate.explanation.includes('does not verify factual accuracy'));
  console.log('PASS: Strict non-factual disclaimer audit verified');

  // Test 13: Structural consistency checks (bullets, sections, output fields)
  console.log('Test 13: Structural consistency checks...');

  // 13.1: Bullet count mismatch
  const resBulletCount = await heurIncomplete.evaluate({
    prompt: 'Provide 5 bullets explaining why unit testing is important',
    candidate_response: 'Here are the key points:\n- Catches regressions before code hits staging\n- Facilitates confident refactoring'
  });
  assert.strictEqual(resBulletCount.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  const bulletCountIssue = resBulletCount.detected_issues.find(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH');
  assert(bulletCountIssue, 'Should detect REQUESTED_COUNT_MISMATCH');
  assert.strictEqual(bulletCountIssue.metadata.expected_count, 5);
  assert.strictEqual(bulletCountIssue.metadata.actual_count, 2);
  assert.strictEqual(bulletCountIssue.metadata.structure_type, 'bullets');
  assert.strictEqual(resBulletCount.completeness, 0.40);

  // 13.2: Requested bullets returned as pure prose
  const resBulletProse = await heurIncomplete.evaluate({
    prompt: 'In 3 bullets, summarize the incident report',
    candidate_response: 'The database cluster experienced elevated connection latency due to a deadlock in the transaction manager during morning peak traffic.'
  });
  assert.strictEqual(resBulletProse.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  const bulletProseIssue = resBulletProse.detected_issues.find(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH');
  assert(bulletProseIssue, 'Should detect REQUESTED_COUNT_MISMATCH for 0 bullets');
  assert.strictEqual(bulletProseIssue.metadata.expected_count, 3);
  assert.strictEqual(bulletProseIssue.metadata.actual_count, 0);
  assert.strictEqual(bulletProseIssue.metadata.structure_type, 'bullets');
  assert.strictEqual(resBulletProse.completeness, 0.0);

  // 13.3: Bullets requested without count returned as prose
  const resBulletNoCount = await heurIncomplete.evaluate({
    prompt: 'Explain photosynthesis in bullet points',
    candidate_response: 'Photosynthesis is the biological process used by plants to convert light energy into chemical energy.'
  });
  assert.strictEqual(resBulletNoCount.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  assert(resBulletNoCount.detected_issues.some(i => i.issue_code === 'MISSING_REQUIRED_BULLETS'));
  assert.strictEqual(resBulletNoCount.completeness, 0.50);

  // 13.4: Stylistic indifference for bullets (*, -, •, and numbered items)
  const resBulletStyles = await heurIncomplete.evaluate({
    prompt: 'Provide 3 bullets on code quality',
    candidate_response: 'Quality checklist:\n* Clean naming conventions\n• Comprehensive unit tests\n- Continuous automated integration'
  });
  assert.strictEqual(resBulletStyles.detected_issues.length, 0);
  assert.strictEqual(resBulletStyles.completeness, 1.0);
  assert.strictEqual(resBulletStyles.escalation_recommendation, EscalationRecommendation.NO_ESCALATION);

  // 13.5: Section count mismatch
  const resSectionCount = await heurIncomplete.evaluate({
    prompt: 'In 3 sections, explain backend scaling: Caching, Database Sharding, and Load Balancing',
    candidate_response: '## 1. Caching\nRedis caches hot keys to reduce primary database load.'
  });
  assert.strictEqual(resSectionCount.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  const secCountIssue = resSectionCount.detected_issues.find(i => i.issue_code === 'REQUESTED_COUNT_MISMATCH');
  assert(secCountIssue, 'Should detect REQUESTED_COUNT_MISMATCH for sections');
  assert.strictEqual(secCountIssue.metadata.expected_count, 3);
  assert.strictEqual(secCountIssue.metadata.actual_count, 1);
  assert.strictEqual(secCountIssue.metadata.structure_type, 'sections');

  // 13.6: Explicit output fields missing
  const resMissingField = await heurIncomplete.evaluate({
    prompt: 'Generate a deployment status report with fields: environment, version, deployer, status',
    candidate_response: '**Environment:** production\n**Version:** v2.4.1\n**Status:** success'
  });
  assert.strictEqual(resMissingField.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  const fieldIssue = resMissingField.detected_issues.find(i => i.issue_code === 'MISSING_REQUIRED_FIELD');
  assert(fieldIssue, 'Should detect MISSING_REQUIRED_FIELD');
  assert.deepStrictEqual(fieldIssue.metadata.missing_fields, ['deployer']);
  assert.deepStrictEqual(fieldIssue.metadata.required_fields, ['environment', 'version', 'deployer', 'status']);
  assert.strictEqual(resMissingField.completeness, 0.70);

  // 13.7: JSON keys missing
  const resJsonMissing = await heurIncomplete.evaluate({
    prompt: 'Return a JSON object with fields: id, username, email, is_active',
    candidate_response: '```json\n{\n  "id": 101,\n  "username": "alice",\n  "is_active": true\n}\n```'
  });
  assert.strictEqual(resJsonMissing.escalation_recommendation, EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL);
  const jsonIssue = resJsonMissing.detected_issues.find(i => i.issue_code === 'MISSING_REQUIRED_FIELD');
  assert(jsonIssue, 'Should detect missing email in JSON');
  assert(jsonIssue.metadata.missing_fields.includes('email'));

  // 13.8: Stylistic indifference for output fields (casing, bold, equals)
  const resFieldStyles = await heurIncomplete.evaluate({
    prompt: 'Provide user info with fields: user_id, email_address, account_status',
    candidate_response: 'User Record:\n- **User Id:** 4242\n- Email Address: user@corp.internal\n- account_status = "active"'
  });
  assert.strictEqual(resFieldStyles.detected_issues.length, 0);
  assert.strictEqual(resFieldStyles.completeness, 1.0);
  assert.strictEqual(resFieldStyles.escalation_recommendation, EscalationRecommendation.NO_ESCALATION);

  // 13.9: False positives for conversational/physical field mentions
  const resFieldOfStudy = await heurIncomplete.evaluate({
    prompt: 'Describe recent breakthrough architectures in the field of natural language processing',
    candidate_response: 'Transformer architectures have largely superseded recurrent neural networks for sequence modeling.'
  });
  assert(!resFieldOfStudy.detected_issues.some(i => i.issue_code === 'MISSING_REQUIRED_FIELD'));
  assert.strictEqual(resFieldOfStudy.completeness, 1.0);

  const resSportsField = await heurIncomplete.evaluate({
    prompt: 'How many yards are marked on a standard football field?',
    candidate_response: 'A standard American football field is 100 yards long between the goal lines, with two 10-yard end zones.'
  });
  assert(!resSportsField.detected_issues.some(i => i.issue_code === 'MISSING_REQUIRED_FIELD'));
  assert.strictEqual(resSportsField.completeness, 1.0);

  console.log('PASS: Structural consistency checks verified');

  console.log('--- ALL EVALUATOR TESTS PASSED ---');
})();

