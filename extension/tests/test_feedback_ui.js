/**
 * Test Suite: Optional Non-Intrusive Feedback UI Controller
 * 
 * Verifies:
 * 1. Discoverable but not constantly visible (ephemeral pill state, auto-dismiss timer).
 * 2. Marking optimization as helpful (POSITIVE) or unhelpful (NEGATIVE).
 * 3. Short list of reasons for unhelpful feedback and skip reason option.
 * 4. Never blocking: non-modal, immediate dismiss (✕).
 * 5. Complete disable guarantee: can be disabled completely via user settings or UI control.
 */

const assert = require('assert');
const feedbackUiModule = require('../src/content/feedback_ui');
const userSettingsModule = require('../src/shared/user_settings');

const {
  UiState,
  REASONS_LIST,
  FeedbackUiController
} = feedbackUiModule;

const {
  UserSettingsManager,
  createUserSettings
} = userSettingsModule;

/**
 * Lightweight mock DOM document for testing content script UI components in Node
 */
function createMockDocument() {
  const elements = new Map();

  class MockElement {
    constructor(tagName) {
      this.tagName = tagName.toUpperCase();
      this.id = '';
      this.attributes = new Map();
      this.style = {};
      this._innerHTML = '';
      this.listeners = new Map();
      this.parentNode = null;
      this.children = [];
    }

    setAttribute(k, v) {
      this.attributes.set(k, String(v));
    }

    getAttribute(k) {
      return this.attributes.get(k) || null;
    }

    get innerHTML() {
      return this._innerHTML;
    }

    set innerHTML(html) {
      this._innerHTML = html;
      this._parseAndBuildChildren(html);
    }

    _parseAndBuildChildren(html) {
      this.children = [];
      // Simple regex parser to discover buttons and IDs in mock HTML
      const idMatches = html.matchAll(/id="([^"]+)"/g);
      for (const m of idMatches) {
        const child = new MockElement('div');
        child.id = m[1];
        child.parentNode = this;
        this.children.push(child);
      }

      const container = this.children.find((c) => c.id === 'sqr-reasons-container') || this;
      const btnMatches = html.matchAll(/class="([^"]*sqr-reason-btn[^"]*)"\s+data-reason="([^"]+)"/g);
      for (const bm of btnMatches) {
        const btn = new MockElement('button');
        btn.setAttribute('data-reason', bm[2]);
        btn.setAttribute('class', bm[1]);
        btn.parentNode = container;
        container.children.push(btn);
      }
    }

    addEventListener(evt, handler) {
      if (!this.listeners.has(evt)) {
        this.listeners.set(evt, []);
      }
      this.listeners.get(evt).push(handler);
    }

    dispatchEvent(evt) {
      const handlers = this.listeners.get(evt.type) || [];
      for (const h of handlers) {
        h(evt);
      }
      if (this.parentNode && typeof this.parentNode.dispatchEvent === 'function') {
        this.parentNode.dispatchEvent(evt);
      }
    }

    click() {
      this.dispatchEvent({ type: 'click', target: this });
    }

    querySelector(selector) {
      if (selector.startsWith('#')) {
        const targetId = selector.slice(1);
        if (this.id === targetId) return this;
        for (const child of this.children) {
          const found = child.querySelector(selector);
          if (found) return found;
        }
        return null;
      }
      if (selector.includes('sqr-reason-btn') || selector.includes('data-reason')) {
        if ((this.getAttribute('class') || '').includes('sqr-reason-btn')) return this;
        for (const child of this.children) {
          const found = child.querySelector(selector);
          if (found) return found;
        }
        return null;
      }
      return this.children[0] || null;
    }

    closest(selector) {
      if (selector.includes('sqr-reason-btn')) {
        if ((this.getAttribute('class') || '').includes('sqr-reason-btn')) return this;
      }
      return this;
    }

    removeChild(child) {
      const idx = this.children.indexOf(child);
      if (idx !== -1) {
        this.children.splice(idx, 1);
        child.parentNode = null;
      }
    }

    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      if (child.id) {
        elements.set(child.id, child);
      }
      return child;
    }
  }

  const body = new MockElement('body');

  const mockDoc = {
    body,
    createElement: (tag) => new MockElement(tag),
    getElementById: (id) => elements.get(id) || null
  };

  return { mockDoc, elements };
}

console.log('--- Running Feedback UI Controller Tests ---');

// Test 1: Initialization & Enabled by default
console.log('Test 1: FeedbackUiController initialization and defaults...');
const settingsManager = new UserSettingsManager();
assert.strictEqual(settingsManager.isFeedbackUiEnabled(), true);

const { mockDoc: doc1 } = createMockDocument();
let submitted = null;

const controller = new FeedbackUiController({
  userSettingsManager: settingsManager,
  document: doc1,
  autoDismissMs: 500,
  onSubmitFeedback: (payload) => {
    submitted = payload;
  }
});

assert.strictEqual(controller.state, UiState.HIDDEN);
assert.strictEqual(controller.isEnabled(), true);
console.log('PASS: FeedbackUiController correctly initialized and enabled by default');

// Test 2: Ephemeral Pill Rendering on notifyOptimization
console.log('Test 2: Ephemeral pill appears when optimization occurs...');
controller.notifyOptimization({
  correlationId: 'corr_ui_001',
  requestId: 'req_ui_001',
  routingMetadata: { substitutionApplied: true }
});

assert.strictEqual(controller.state, UiState.PILL);
const rootEl = doc1.getElementById('smart-query-router-feedback-root');
assert.notStrictEqual(rootEl, null);
assert.strictEqual(rootEl.style.display, 'block');
assert.strictEqual(rootEl.innerHTML.includes('Optimization · Feedback?'), true);
console.log('PASS: Ephemeral pill successfully rendered upon optimization notification');

// Test 3: Close button dismisses pill immediately
console.log('Test 3: Close button dismisses pill without user interruption...');
const closeBtn = rootEl.querySelector('#sqr-pill-close');
assert.notStrictEqual(closeBtn, null);
closeBtn.click();
assert.strictEqual(controller.state, UiState.HIDDEN);
assert.strictEqual(rootEl.style.display, 'none');
console.log('PASS: Close button dismisses pill cleanly');

// Test 4: Expand pill to compact card
console.log('Test 4: Clicking pill expands to compact rating card...');
controller.notifyOptimization({ correlationId: 'corr_ui_002' });
assert.strictEqual(controller.state, UiState.PILL);

const trigger = rootEl.querySelector('#sqr-pill-trigger');
assert.notStrictEqual(trigger, null);
trigger.click();
assert.strictEqual(controller.state, UiState.EXPANDED);
assert.strictEqual(rootEl.innerHTML.includes('Was this optimization helpful?'), true);
console.log('PASS: Pill expands to rating card on click');

// Test 5: Marking helpful (POSITIVE)
console.log('Test 5: Marking optimization as helpful (POSITIVE)...');
submitted = null;
const helpfulBtn = rootEl.querySelector('#sqr-btn-helpful');
assert.notStrictEqual(helpfulBtn, null);
helpfulBtn.click();

assert.strictEqual(controller.state, UiState.THANK_YOU);
assert.notStrictEqual(submitted, null);
assert.strictEqual(submitted.correlationId, 'corr_ui_002');
assert.strictEqual(submitted.rating, 'POSITIVE');
assert.strictEqual(submitted.rejectionReason, null);
assert.strictEqual(rootEl.innerHTML.includes('Thank you for your feedback!'), true);
console.log('PASS: Helpful rating dispatches POSITIVE feedback with thank-you confirmation');

// Test 6: Marking unhelpful (NEGATIVE) and picking a reason
console.log('Test 6: Marking optimization as unhelpful and selecting reason...');
submitted = null;
controller.notifyOptimization({ correlationId: 'corr_ui_003' });
rootEl.querySelector('#sqr-pill-trigger').click();
assert.strictEqual(controller.state, UiState.EXPANDED);

const unhelpfulBtn = rootEl.querySelector('#sqr-btn-unhelpful');
assert.notStrictEqual(unhelpfulBtn, null);
unhelpfulBtn.click();

assert.strictEqual(controller.state, UiState.REASONS);
assert.strictEqual(rootEl.innerHTML.includes('What went wrong?'), true);

// Click a reason button
const reasonBtn = rootEl.querySelector('.sqr-reason-btn');
assert.notStrictEqual(reasonBtn, null);
const selectedReason = reasonBtn.getAttribute('data-reason');
reasonBtn.click();

assert.strictEqual(controller.state, UiState.THANK_YOU);
assert.notStrictEqual(submitted, null);
assert.strictEqual(submitted.correlationId, 'corr_ui_003');
assert.strictEqual(submitted.rating, 'NEGATIVE');
assert.strictEqual(submitted.rejectionReason, selectedReason);
console.log('PASS: Unhelpful feedback with reason selection verified');

// Test 7: Skip reason option
console.log('Test 7: Unhelpful feedback with skip reason...');
submitted = null;
controller.notifyOptimization({ correlationId: 'corr_ui_004' });
rootEl.querySelector('#sqr-pill-trigger').click();
rootEl.querySelector('#sqr-btn-unhelpful').click();
assert.strictEqual(controller.state, UiState.REASONS);

const skipBtn = rootEl.querySelector('#sqr-reason-skip');
assert.notStrictEqual(skipBtn, null);
skipBtn.click();

assert.strictEqual(controller.state, UiState.THANK_YOU);
assert.notStrictEqual(submitted, null);
assert.strictEqual(submitted.rating, 'NEGATIVE');
assert.strictEqual(submitted.rejectionReason, null);
console.log('PASS: Skip reason allows submitting unhelpful feedback without selecting reason');

// Test 8: Complete disable guarantee
console.log('Test 8: Complete disable guarantee (removes UI and prevents future rendering)...');
controller.notifyOptimization({ correlationId: 'corr_ui_005' });
rootEl.querySelector('#sqr-pill-trigger').click();

// Click "Don't show feedback prompts"
const disableLink = rootEl.querySelector('#sqr-disable-ui');
assert.notStrictEqual(disableLink, null);
disableLink.click();

assert.strictEqual(settingsManager.isFeedbackUiEnabled(), false);
assert.strictEqual(controller.isEnabled(), false);
assert.strictEqual(controller.state, UiState.HIDDEN);

// Verify future notifications do nothing when disabled
submitted = null;
controller.notifyOptimization({ correlationId: 'corr_ui_006' });
assert.strictEqual(controller.state, UiState.HIDDEN);
console.log('PASS: Complete disable guarantee verified (UI destroyed and muted)');

console.log('--- ALL FEEDBACK UI TESTS PASSED ---');
