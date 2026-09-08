/**
 * Smart Query Router - Unit Tests for Rich Content & Attachment Handling
 * 
 * Verifies:
 * 1. Feature extraction recognizes DOM attachments, attachment references, images, files, code blocks, tables.
 * 2. Normalizer preserves table rows and column alignments verbatim while normalizing prose.
 * 3. UI substitution enforces conservative rich content preservation, aborting if attachment references are altered.
 * 4. Deterministic routing policy routes queries with rich content conservatively to 'complex-model candidate'.
 * 5. Arithmetic / greeting local rules do not hijack queries that have rich content or attachments.
 * 6. Explicit richContent metadata is present on all routing classifications.
 */

const assert = require('assert');
const { extractQueryFeatures } = require('../src/shared/feature_extractor');
const { normalizeQueryText, normalizeQuery } = require('../src/shared/normalizer');
const {
  SubstitutionStatus,
  SafeUiSubstitutor
} = require('../src/content/ui_substitution');
const {
  CoarseRoute,
  RoutingReasonCode,
  DeterministicRoutingPolicy
} = require('../src/shared/routing_policy');
const { createDetectedQueryEvent } = require('../src/shared/query_event');

// Mock contenteditable node for UI substitution tests
class MockContentEditableNode {
  constructor(initialText = '') {
    this.tagName = 'DIV';
    this.attributes = { contenteditable: 'true' };
    this.innerText = initialText;
    this.textContent = initialText;
    this.isContentEditable = true;
    this.focused = false;
    this.ownerDocument = {
      defaultView: {
        getSelection: () => ({
          removeAllRanges: () => {},
          addRange: () => {}
        })
      },
      createRange: () => ({
        selectNodeContents: () => {},
        deleteContents: () => {},
        insertNode: (node) => {
          this.textContent = node.textContent;
          this.innerText = node.textContent;
        }
      }),
      createTextNode: (text) => ({ textContent: text }),
      execCommand: (command, showUI, value) => {
        if (command === 'insertText') {
          this.textContent = value;
          this.innerText = value;
          return true;
        }
        return false;
      }
    };
  }
  focus() { this.focused = true; }
  dispatchEvent() { return true; }
}

console.log('--- Running Rich Content & Attachment Handling Tests ---');

// Test 1: Feature Extractor - Detection of Tables and Attachment References
console.log('Test 1: Feature extractor detects tables, attachment references, and rich content...');
const tablePrompt = `Here is the dataset:
| Product | Price | Units |
|---------|-------|-------|
| Widget  | $10   | 100   |
| Gadget  | $25   | 40    |
Please analyze total revenue.`;

const tableFeatures = extractQueryFeatures(tablePrompt);
assert.strictEqual(tableFeatures.tables.hasTable, true, 'Should detect markdown table');
assert.strictEqual(tableFeatures.tables.hasMarkdownTable, true, 'Should detect markdown table format');
assert.strictEqual(tableFeatures.richContent.hasRichInput, true, 'richContent.hasRichInput should be true');
assert.strictEqual(tableFeatures.richContent.hasTables, true, 'richContent.hasTables should be true');
assert.strictEqual(tableFeatures.richContent.preservationRequired, true, 'preservationRequired should be true');
assert(tableFeatures.richContent.types.includes('table'), 'types should include table');

// Test with attachment reference in prompt
const attachmentPrompt = 'Please summarize [Attachment #1: Q3_Financials.pdf] and [Image: architecture_diagram.png]';
const attachFeatures = extractQueryFeatures(attachmentPrompt);
assert.strictEqual(attachFeatures.attachments.hasAttachment, true, 'Should detect attachments');
assert.strictEqual(attachFeatures.attachments.hasAttachmentReference, true, 'Should detect attachment reference');
assert.strictEqual(attachFeatures.attachments.hasImageReference, true, 'Should detect image reference');
assert.strictEqual(attachFeatures.richContent.hasRichInput, true);
assert.strictEqual(attachFeatures.richContent.hasAttachments, true);
assert.strictEqual(attachFeatures.richContent.hasImages, true);
assert(attachFeatures.richContent.types.includes('attachment'));
assert(attachFeatures.richContent.types.includes('image'));

// Test with DOM attachments passed in options
const domAttachments = [
  { name: 'dataset.csv', type: 'file', isImage: false, sizeText: '4.2 MB' },
  { name: 'chart.png', type: 'image', isImage: true, sizeText: '512 KB' }
];
const domFeatures = extractQueryFeatures('What do you think of this data?', { domAttachments });
assert.strictEqual(domFeatures.attachments.hasAttachment, true, 'Should detect DOM attachments');
assert.strictEqual(domFeatures.attachments.hasDomAttachment, true, 'Should flag DOM attachments');
assert.strictEqual(domFeatures.attachments.attachmentCount, 2, 'Should count 2 DOM attachments');
assert.strictEqual(domFeatures.richContent.hasRichInput, true);
assert.strictEqual(domFeatures.richContent.hasAttachments, true);
assert.strictEqual(domFeatures.richContent.hasImages, true);
assert.strictEqual(domFeatures.richContent.hasFiles, true);
assert(domFeatures.richContent.types.includes('attachment'));
assert(domFeatures.richContent.types.includes('image'));
assert(domFeatures.richContent.types.includes('file'));
console.log('PASS: Feature extraction for tables and attachments verified');

// Test 2: Normalizer - Table Formatting Preservation
console.log('Test 2: Normalizer preserves table columns and borders intact...');
const unnormalizedTable = `Here is   the   table:

| ID   | Name       | Role       |
|:-----|:-----------|:-----------|
| 001  | Alice      | Admin      |
| 002  | Bob        | Engineer   |

Please check.`;

const normalizedText = normalizeQueryText(unnormalizedTable);
// Table rows should retain internal column spacing
assert(normalizedText.includes('| ID   | Name       | Role       |'), 'Table header spacing preserved');
assert(normalizedText.includes('| 001  | Alice      | Admin      |'), 'Table data row spacing preserved');
assert(normalizedText.includes('|:-----|:-----------|:-----------|'), 'Table separator preserved');
// Non-table prose should have spaces collapsed
assert(normalizedText.includes('Here is the table:'), 'Prose spaces collapsed');
console.log('PASS: Table column alignment preservation verified in normalizer');

// Test 3: UI Substitution - Attachment Preservation Enforcement
console.log('Test 3: UI substitution enforces conservative attachment reference preservation...');
const originalText = 'Review [Attachment #1: contract.pdf] and verify clause 4.';

// Scenario A: Optimizer that alters/strips attachment reference is rejected with NO_OP
const mockStrippingNormalizer = {
  normalizeQuery: (text) => ({
    rawPrompt: text,
    normalizedPrompt: 'Review and verify clause 4.', // stripped attachment ref
    isChanged: true,
    savings: { characters: 30, percentage: 50.0 }
  })
};
const mockNode = new MockContentEditableNode(originalText);
const strippingSubstitutor = new SafeUiSubstitutor({ normalizer: mockStrippingNormalizer });
const rejectedSub = strippingSubstitutor.applyPromptOptimization(mockNode, { rawText: originalText });
assert.strictEqual(rejectedSub.status, SubstitutionStatus.NO_OP, 'Should reject substitution when attachment ref is stripped');
assert.strictEqual(rejectedSub.reason, 'RICH_CONTENT_CONSERVATIVE_PRESERVATION', 'Reason must be conservative preservation');

// Scenario B: Normalizer that preserves attachment reference succeeds
const originalWithSpaces = 'Review [Attachment #1: contract.pdf]   and verify clause 4.';
const mockNode2 = new MockContentEditableNode(originalWithSpaces);
const realSubstitutor = new SafeUiSubstitutor();
const acceptedSub = realSubstitutor.applyPromptOptimization(mockNode2, { rawText: originalWithSpaces });
assert.strictEqual(acceptedSub.status, SubstitutionStatus.APPLIED, 'Should apply substitution when attachment ref is preserved');
assert(acceptedSub.substitutedText.includes('[Attachment #1: contract.pdf]'), 'Attachment reference must be preserved intact');
console.log('PASS: UI substitution attachment reference preservation verified');

// Test 4: Deterministic Routing Policy - Conservative Complex-Model Routing for Rich Content
console.log('Test 4: Routing policy conservatively routes rich content to complex-model candidate...');
const policy = new DeterministicRoutingPolicy();

// Table query routing
const tableEvent = createDetectedQueryEvent({
  rawPrompt: tablePrompt,
  features: tableFeatures
});
const tableRoute = policy.classify(tableEvent);
assert.strictEqual(tableRoute.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE, 'Table query must route to complex-model candidate');
assert.strictEqual(tableRoute.reasonCode, RoutingReasonCode.COMPLEX_TABLE_DATA, 'Reason code must be COMPLEX_TABLE_DATA');
assert.strictEqual(tableRoute.richContent.hasRichInput, true, 'richContent.hasRichInput must be true');
assert.strictEqual(tableRoute.richContent.preservationStrategy, 'CONSERVATIVE_PRESERVATION');
assert(tableRoute.matchedSignals.includes('table'));

// Attachment query routing
const attachEvent = createDetectedQueryEvent({
  rawPrompt: attachmentPrompt,
  features: attachFeatures
});
const attachRoute = policy.classify(attachEvent);
assert.strictEqual(attachRoute.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE, 'Attachment query must route to complex-model candidate');
assert.strictEqual(attachRoute.reasonCode, RoutingReasonCode.COMPLEX_ATTACHMENT_DEPENDENCY, 'Reason code must be COMPLEX_ATTACHMENT_DEPENDENCY');
assert.strictEqual(attachRoute.richContent.hasRichInput, true);
assert.strictEqual(attachRoute.richContent.preservationStrategy, 'CONSERVATIVE_PRESERVATION');
console.log('PASS: Conservative routing for tables and attachments verified');

// Test 5: Local Rules Bypass When Rich Content Is Present
console.log('Test 5: Arithmetic and greeting local rules bypass when rich content is present...');
// Query has arithmetic text "what is 2 + 2?" but also has an attached file in DOM
const arithmeticWithAttachFeatures = extractQueryFeatures('what is 2 + 2?', {
  domAttachments: [{ name: 'spec.pdf', type: 'file', isImage: false }]
});
const arithmeticWithAttachEvent = createDetectedQueryEvent({
  rawPrompt: 'what is 2 + 2?',
  features: arithmeticWithAttachFeatures,
  domAttachments: [{ name: 'spec.pdf', type: 'file', isImage: false }]
});
// Mock that local rule matched arithmetic
arithmeticWithAttachEvent.optimization = {
  status: 'EVALUATED',
  decision: {
    outcome: 'LOCAL_ANSWER_CANDIDATE',
    ruleId: 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC',
    reason: 'Arithmetic calculation',
    confidence: 1.0
  }
};

const localBypassRoute = policy.classify(arithmeticWithAttachEvent);
// Must NOT be LOCAL_ELIGIBLE because an attachment is present!
assert.notStrictEqual(localBypassRoute.route, CoarseRoute.LOCAL_ELIGIBLE, 'Must not route to local-eligible when attachment is present');
assert.strictEqual(localBypassRoute.route, CoarseRoute.COMPLEX_MODEL_CANDIDATE, 'Must route to complex-model candidate');
assert.strictEqual(localBypassRoute.reasonCode, RoutingReasonCode.COMPLEX_ATTACHMENT_DEPENDENCY);
assert.strictEqual(localBypassRoute.richContent.hasRichInput, true);
console.log('PASS: Local rule bypass on rich content verified');

// Test 6: Clean Standalone Queries Get Proper richContent Metadata
console.log('Test 6: Clean queries have explicit richContent metadata with hasRichInput: false...');
const cleanEvent = createDetectedQueryEvent({
  rawPrompt: 'What is the capital of France?'
});
const cleanRoute = policy.classify(cleanEvent);
assert.strictEqual(cleanRoute.route, CoarseRoute.SIMPLE_MODEL_CANDIDATE);
assert.deepStrictEqual(cleanRoute.richContent, {
  hasRichInput: false,
  detectedTypes: [],
  preservationStrategy: 'NONE'
}, 'Clean route must have explicit richContent metadata');
console.log('PASS: Clean query richContent metadata verified');

console.log('--- ALL RICH CONTENT & ATTACHMENT HANDLING TESTS PASSED ---');
