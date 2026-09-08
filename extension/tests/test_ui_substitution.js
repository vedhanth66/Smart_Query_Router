/**
 * Smart Query Router - Safe UI-Level Substitution Tests
 * 
 * Verifies:
 * 1. SubstitutionStatus enum values (APPLIED, NO_OP, BYPASSED, FAILED).
 * 2. Successful UI-level substitution on contenteditable ProseMirror element.
 * 3. Preservation of code blocks, LaTeX math, and list indentation during substitution.
 * 4. No-op behavior when prompt is already normalized and optimal.
 * 5. Immediate manual user bypass path (Alt key / options.bypass).
 * 6. Immediate user settings bypass path (optimizationEnabled: false).
 * 7. Strict immediate fail-open recovery when DOM or selection methods throw.
 * 8. Privacy audit: zero raw prompt text in log events or summary outputs.
 */

'use strict';

const assert = require('assert');
const {
  SubstitutionStatus,
  SafeUiSubstitutor,
  defaultUiSubstitutor
} = require('../src/content/ui_substitution');

const normalizer = require('../src/shared/normalizer');
const { UserSettingsManager } = require('../src/shared/user_settings');
const { DiagnosticLogger, LogLevel } = require('../src/shared/logger');

console.log('--- Running Safe UI-Level Substitution Tests ---');

// Mock DOM Node & Element for testing contenteditable behavior
class MockContentEditableNode {
  constructor(initialText = '') {
    this.tagName = 'DIV';
    this.attributes = { contenteditable: 'true' };
    this.innerText = initialText;
    this.textContent = initialText;
    this.isContentEditable = true;
    this.focused = false;
    this.dispatchedEvents = [];
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

  getAttribute(attr) {
    return this.attributes[attr] || null;
  }

  focus() {
    this.focused = true;
  }

  dispatchEvent(event) {
    this.dispatchedEvents.push(event);
    return true;
  }
}

// Test 1: SubstitutionStatus Enum
console.log('Test 1: Verifying SubstitutionStatus enum values...');
assert.strictEqual(SubstitutionStatus.APPLIED, 'APPLIED');
assert.strictEqual(SubstitutionStatus.NO_OP, 'NO_OP');
assert.strictEqual(SubstitutionStatus.BYPASSED, 'BYPASSED');
assert.strictEqual(SubstitutionStatus.FAILED, 'FAILED');
console.log('PASS: SubstitutionStatus enum verified');

// Test 2: Successful safe UI substitution with normalization
console.log('Test 2: Successful safe UI substitution on contenteditable element...');
const logger = new DiagnosticLogger({ level: LogLevel.DEBUG, enableConsole: false });
const substitutor = new SafeUiSubstitutor({ normalizer, logger });

const rawInput = '   What is the capital of France?   \n\n\n\nHow large is Paris?   ';
const mockEditor = new MockContentEditableNode(rawInput);

const result = substitutor.applyPromptOptimization(mockEditor);
assert.strictEqual(result.status, SubstitutionStatus.APPLIED);
assert.strictEqual(result.reason, 'NORMALIZATION_APPLIED');
assert.strictEqual(result.originalText, rawInput.trim());
assert.ok(mockEditor.textContent.includes('What is the capital of France?'));
assert.ok(!mockEditor.textContent.includes('\n\n\n\n'), '4 blank lines collapsed');
assert.strictEqual(mockEditor.focused, true);
assert.ok(result.savings.characters > 0);
console.log('PASS: Successful safe UI substitution applied without syntax or DOM errors');

// Test 3: Code and Math Preservation during substitution
console.log('Test 3: Preserving code blocks, math, and list indentation...');
const codeMathInput = '   Calculate $E = mc^2$:\n\n\n```python\ndef solve():\n    return 42\n```\n\n\n';
const mockCodeEditor = new MockContentEditableNode(codeMathInput);

const codeResult = substitutor.applyPromptOptimization(mockCodeEditor);
assert.strictEqual(codeResult.status, SubstitutionStatus.APPLIED);
assert.ok(mockCodeEditor.textContent.includes('$E = mc^2$'), 'Math symbols strictly preserved');
assert.ok(mockCodeEditor.textContent.includes('    return 42'), 'Code indentation strictly preserved');
assert.ok(mockCodeEditor.textContent.includes('```python'), 'Code block fences strictly preserved');
console.log('PASS: Critical structures (code, math, indentation) preserved during substitution');

// Test 4: No-op when prompt is already optimal
console.log('Test 4: No-op verification when prompt is already clean...');
const cleanPrompt = 'Explain the difference between synchronous and asynchronous execution.';
const mockCleanEditor = new MockContentEditableNode(cleanPrompt);

const noopResult = substitutor.applyPromptOptimization(mockCleanEditor);
assert.strictEqual(noopResult.status, SubstitutionStatus.NO_OP);
assert.strictEqual(noopResult.reason, 'ALREADY_OPTIMAL');
assert.strictEqual(mockCleanEditor.textContent, cleanPrompt);
console.log('PASS: No-op triggered cleanly when no optimization is needed');

// Test 5: Immediate manual user bypass (e.g. Alt key held)
console.log('Test 5: Immediate manual user bypass via Alt key...');
const unoptimizedInput = '   Text that should NOT be normalized   \n\n\n\n';
const mockBypassEditor = new MockContentEditableNode(unoptimizedInput);

// Test via options.bypass
const bypassResult = substitutor.applyPromptOptimization(mockBypassEditor, {
  rawText: unoptimizedInput,
  bypass: true
});
assert.strictEqual(bypassResult.status, SubstitutionStatus.BYPASSED);
assert.strictEqual(bypassResult.reason, 'USER_HOTKEY_BYPASS');
assert.strictEqual(mockBypassEditor.textContent, unoptimizedInput, 'Text left untouched on bypass');

// Test isBypassTrigger helper with simulated event
assert.strictEqual(substitutor.isBypassTrigger({ altKey: true }), true);
assert.strictEqual(substitutor.isBypassTrigger({ altKey: false }), false);
assert.strictEqual(substitutor.isBypassTrigger(null), false);
console.log('PASS: Immediate manual user bypass cleanly leaves prompt untouched');

// Test 6: Settings toggle bypass (optimizationEnabled: false)
console.log('Test 6: Settings toggle bypass...');
const settingsManager = new UserSettingsManager();
settingsManager.setOptimizationEnabled(false);
assert.strictEqual(settingsManager.isOptimizationEnabled(), false);

const disabledSubstitutor = new SafeUiSubstitutor({
  normalizer,
  logger,
  userSettingsManager: settingsManager
});

const mockSettingsEditor = new MockContentEditableNode(unoptimizedInput);
const settingsBypassResult = disabledSubstitutor.applyPromptOptimization(mockSettingsEditor);
assert.strictEqual(settingsBypassResult.status, SubstitutionStatus.BYPASSED);
assert.strictEqual(settingsBypassResult.reason, 'SETTINGS_DISABLED');
assert.strictEqual(mockSettingsEditor.textContent, unoptimizedInput);
console.log('PASS: Settings toggle bypass immediately leaves prompt untouched');

// Test 7: Immediate fail-open on DOM or Selection exceptions
console.log('Test 7: Strict immediate fail-open on DOM exception...');
const faultyEditor = new MockContentEditableNode('   Some   prompt   with   excessive   spaces   \n\n\n\n   Extra   ');
faultyEditor.ownerDocument.execCommand = () => { throw new Error('DOMException: The element is not focused or supported'); };
faultyEditor.ownerDocument.createRange = () => { throw new Error('Range error: node not in document'); };
Object.defineProperty(faultyEditor, 'textContent', {
  get: () => '   Some   prompt   with   excessive   spaces   \n\n\n\n   Extra   ',
  set: () => { throw new Error('DOMException: node is locked or read-only'); }
});

const failOpenResult = substitutor.applyPromptOptimization(faultyEditor);
assert.strictEqual(failOpenResult.status, SubstitutionStatus.FAILED);
assert.strictEqual(failOpenResult.failOpen, true);
assert.ok(failOpenResult.reason.includes('DOM_SUBSTITUTION_ERROR'));
console.log('PASS: Immediate fail-open guarantee verified (user never blocked)');

// Test 8: Privacy audit
console.log('Test 8: Privacy audit on logs and diagnostics...');
const logs = logger.getRecentLogs();
const sensitiveSnippet = 'SuperSecretConfidentialQueryText123';
const privateEditor = new MockContentEditableNode(`   ${sensitiveSnippet}   \n\n\n   Extra   `);
substitutor.applyPromptOptimization(privateEditor);

const allLogText = JSON.stringify(logger.getRecentLogs());
assert.strictEqual(allLogText.includes(sensitiveSnippet), false, 'Raw prompt must never appear in logger output');
console.log('PASS: Privacy audit confirmed zero user prompt text in diagnostic logs');

console.log('--- ALL SAFE UI-LEVEL SUBSTITUTION TESTS PASSED ---');
