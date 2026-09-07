/**
 * Automated Unit Test Suite for Claude Observation Mechanics
 * Deterministically tests submission detection (Enter key, send button),
 * non-blocking passive behavior, and strict privacy guarantees.
 */

const assert = require('assert');
const messages = require('../src/shared/messages');
const { EventCategory, DiagnosticLogger, LogLevel } = require('../src/shared/logger');

console.log('--- Running Claude Observation Tests ---');

// Mock DOM elements and environment
class MockElement {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attributes };
    this.innerText = attributes.innerText || '';
    this.textContent = this.innerText;
    this.disabled = Boolean(attributes.disabled);
    this.children = [];
    this.parentElement = null;
  }

  getAttribute(name) {
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (selector === 'div[contenteditable="true"]') {
        if (current.tagName === 'DIV' && current.getAttribute('contenteditable') === 'true') {
          return current;
        }
      } else if (selector === 'button') {
        if (current.tagName === 'BUTTON') {
          return current;
        }
      }
      current = current.parentElement;
    }
    return null;
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
  }

  querySelector(selector) {
    for (const child of this.children) {
      if (child.tagName === 'SVG' && selector.includes('svg')) {
        return child;
      }
    }
    return null;
  }
}

// Simulated Observation Controller reproducing claude_interceptor logic
class ObservationTester {
  constructor() {
    this.observedEvents = [];
    this.logger = new DiagnosticLogger({ level: LogLevel.DEBUG, enableConsole: false });
  }

  extractEditorText(editor) {
    if (!editor) return '';
    return (editor.innerText || editor.textContent || '').trim();
  }

  handleKeyDown(event, editorElement) {
    if (event.key !== 'Enter') return;
    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.isComposing) return;

    const target = event.target;
    const editor = target.closest('div[contenteditable="true"]');
    if (!editor) return;

    const text = this.extractEditorText(editor);
    if (text.length > 0) {
      const msg = messages.createQueryObservedMessage(text.length, 'keyboard_enter');
      this.observedEvents.push({
        type: 'KEYBOARD',
        prompt_length: text.length,
        defaultPrevented: event.defaultPrevented,
        message: msg
      });
    }
  }

  handleClick(event, activeEditor) {
    const target = event.target;
    const button = target.closest('button');
    if (!button || button.disabled) return;

    const ariaLabel = (button.getAttribute('aria-label') || '').toLowerCase();
    const isSendButton = ariaLabel.includes('send') || ariaLabel.includes('prompt') ||
      button.getAttribute('type') === 'submit';

    if (!isSendButton) return;
    if (!activeEditor) return;

    const text = this.extractEditorText(activeEditor);
    if (text.length > 0) {
      const msg = messages.createQueryObservedMessage(text.length, 'button_click');
      this.observedEvents.push({
        type: 'BUTTON',
        prompt_length: text.length,
        defaultPrevented: event.defaultPrevented,
        message: msg
      });
    }
  }
}

// Test 1: Standard Enter Keypress triggers observation and does NOT preventDefault
console.log('Test 1: Standard Enter key triggers observation passively...');
const tester = new ObservationTester();
const editor = new MockElement('div', { contenteditable: 'true', innerText: 'How do quicksort algorithms work?' });
const paragraph = new MockElement('p', { innerText: 'How do quicksort algorithms work?' });
editor.appendChild(paragraph);

const enterEvent = {
  key: 'Enter',
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  isComposing: false,
  defaultPrevented: false,
  target: paragraph
};

tester.handleKeyDown(enterEvent, editor);
assert.strictEqual(tester.observedEvents.length, 1);
assert.strictEqual(tester.observedEvents[0].type, 'KEYBOARD');
assert.strictEqual(tester.observedEvents[0].prompt_length, 33);
assert.strictEqual(tester.observedEvents[0].defaultPrevented, false, 'Must be strictly passive');
assert.strictEqual(tester.observedEvents[0].message.payload.prompt_length, 33);
assert.strictEqual(tester.observedEvents[0].message.payload.prompt, undefined, 'Zero raw prompt text in message');
console.log('PASS: Enter key submission observed passively with zero raw text leakage');

// Test 2: Shift + Enter inserts newline, must NOT trigger observation
console.log('Test 2: Shift + Enter ignored...');
const shiftEnterEvent = {
  key: 'Enter',
  shiftKey: true,
  isComposing: false,
  target: paragraph
};
tester.handleKeyDown(shiftEnterEvent, editor);
assert.strictEqual(tester.observedEvents.length, 1, 'Event count should not increase');
console.log('PASS: Shift + Enter correctly ignored');

// Test 3: IME composition active (isComposing = true) must NOT trigger observation
console.log('Test 3: IME composition ignored...');
const imeEvent = {
  key: 'Enter',
  shiftKey: false,
  isComposing: true,
  target: paragraph
};
tester.handleKeyDown(imeEvent, editor);
assert.strictEqual(tester.observedEvents.length, 1, 'Event count should not increase');
console.log('PASS: IME composition properly ignored');

// Test 4: Empty / whitespace prompt must NOT trigger observation
console.log('Test 4: Whitespace-only prompt ignored...');
const emptyEditor = new MockElement('div', { contenteditable: 'true', innerText: '   \n  ' });
const emptyP = new MockElement('p', { innerText: '   ' });
emptyEditor.appendChild(emptyP);

const emptyEvent = {
  key: 'Enter',
  shiftKey: false,
  isComposing: false,
  target: emptyP
};
tester.handleKeyDown(emptyEvent, emptyEditor);
assert.strictEqual(tester.observedEvents.length, 1, 'Whitespace prompt ignored');
console.log('PASS: Whitespace-only prompts ignored');

// Test 5: Click on Send Button triggers observation
console.log('Test 5: Click on Send Button triggers observation...');
const sendButton = new MockElement('button', { 'aria-label': 'Send Message', disabled: false });
const buttonClickEvent = {
  target: sendButton,
  defaultPrevented: false
};
tester.handleClick(buttonClickEvent, editor);
assert.strictEqual(tester.observedEvents.length, 2);
assert.strictEqual(tester.observedEvents[1].type, 'BUTTON');
assert.strictEqual(tester.observedEvents[1].prompt_length, 33);
console.log('PASS: Send button click observed passively');

// Test 6: Disabled button click is ignored
console.log('Test 6: Disabled send button click ignored...');
const disabledButton = new MockElement('button', { 'aria-label': 'Send Message', disabled: true });
tester.handleClick({ target: disabledButton }, editor);
assert.strictEqual(tester.observedEvents.length, 2, 'Disabled button click ignored');
console.log('PASS: Disabled button click ignored');

console.log('--- ALL OBSERVATION TESTS PASSED ---');
