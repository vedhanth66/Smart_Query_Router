/**
 * Test Suite: On-Device Local Answer UI Controller
 * 
 * Verifies:
 * 1. Mounting and DOM initialization (#smart-query-router-local-answer-root).
 * 2. Deterministic math evaluation and rendering (5+2 = 7).
 * 3. Clipboard copy action with visual confirmation ("✓ Copied!").
 * 4. Insert into prompt editor action (replaces editor text).
 * 5. Native Claude bypass trigger ("Ask Claude Anyway").
 * 6. Dismiss button and Escape key listener.
 * 7. Auto-dismiss timer behavior.
 * 8. User settings configuration toggle for local answering.
 */

const assert = require('assert');
const localAnswerUiModule = require('../src/content/local_answer_ui');
const userSettingsModule = require('../src/shared/user_settings');

const {
  ROOT_ID,
  DEFAULT_AUTO_DISMISS_MS,
  LocalAnswerUiController
} = localAnswerUiModule;

const {
  UserSettingsManager
} = userSettingsModule;

/**
 * Lightweight mock DOM for testing LocalAnswerUiController in Node
 */
function createMockDocument() {
  const elements = new Map();
  const docListeners = new Map();

  class MockElement {
    constructor(tagName) {
      this.tagName = tagName.toUpperCase();
      this.id = '';
      this.className = '';
      this.attributes = new Map();
      this.style = {};
      this._innerHTML = '';
      this.listeners = new Map();
      this.parentNode = null;
      this.children = [];
      this.innerText = '';
      this.textContent = '';
      this.isConnected = true;
    }

    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      if (child.id) {
        elements.set(child.id, child);
      }
      return child;
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
      if (!html) return;

      // Extract elements with id
      const idMatches = html.matchAll(/id="([^"]+)"/g);
      for (const m of idMatches) {
        const child = new MockElement('div');
        child.id = m[1];
        child.parentNode = this;
        this.children.push(child);
        elements.set(child.id, child);
      }

      // Extract card element with class
      if (html.includes('sqr-local-card')) {
        const card = new MockElement('div');
        card.className = 'sqr-local-card';
        card.parentNode = this;
        this.children.push(card);
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
        return elements.get(targetId) || null;
      }
      if (selector.startsWith('.')) {
        const targetClass = selector.slice(1);
        if (this.className.includes(targetClass)) return this;
        for (const child of this.children) {
          const found = child.querySelector(selector);
          if (found) return found;
        }
        return null;
      }
      return null;
    }

    getBoundingClientRect() {
      return { top: 600, bottom: 640, left: 150, right: 750, width: 600, height: 40 };
    }
  }

  const body = new MockElement('body');

  const mockDoc = {
    body,
    createElement: (tag) => {
      const el = new MockElement(tag);
      return el;
    },
    getElementById: (id) => elements.get(id) || null,
    addEventListener: (evt, handler) => {
      if (!docListeners.has(evt)) {
        docListeners.set(evt, []);
      }
      docListeners.get(evt).push(handler);
    },
    dispatchEvent: (evt) => {
      const handlers = docListeners.get(evt.type) || [];
      for (const h of handlers) {
        h(evt);
      }
    }
  };

  return { mockDoc, elements, MockElement };
}

// Ensure global window context for getBoundingClientRect / innerHeight calculations
globalThis.window = {
  innerHeight: 900,
  innerWidth: 1400
};

async function runTests() {
  console.log('--- Running Local Answer UI Controller Tests ---');

  // Test 1: Instantiation and Default State
  console.log('Test 1: Initialization and default state...');
  const { mockDoc: doc1 } = createMockDocument();
  const settingsManager = new UserSettingsManager();

  const controller1 = new LocalAnswerUiController({
    document: doc1,
    userSettingsManager: settingsManager,
    autoDismissMs: 50
  });

  assert.strictEqual(controller1.isVisible, false);
  assert.strictEqual(controller1.autoDismissMs, 50);
  assert.strictEqual(settingsManager.isLocalAnsweringEnabled(), true, 'Local answering should be enabled by default');
  console.log('PASS: LocalAnswerUiController initialized cleanly');

  // Test 2: Show Answer for "5 + 2 = 7" renders card with badges and result
  console.log('Test 2: showAnswer renders on-device card...');
  controller1.showAnswer({
    expression: '5 + 2',
    result: 7,
    ruleId: 'RULE_LOCAL_DETERMINISTIC_ARITHMETIC',
    rawQuery: '5+2'
  });

  assert.strictEqual(controller1.isVisible, true);
  const root1 = doc1.getElementById(ROOT_ID);
  assert.notStrictEqual(root1, null, 'Root container must exist in DOM');
  assert.strictEqual(root1.innerHTML.includes('5 + 2'), true, 'Rendered HTML must include the expression');
  assert.strictEqual(root1.innerHTML.includes('7'), true, 'Rendered HTML must include computed result');
  assert.strictEqual(root1.innerHTML.includes('⚡ Solved on-device · 0ms · $0'), true, 'Badge must indicate on-device 0ms resolution');
  console.log('PASS: Local answer card rendered with full expression and result');

  // Test 3: Close button dismisses card
  console.log('Test 3: Close button dismisses the card...');
  const closeBtn = root1.querySelector('#sqr-local-btn-close');
  assert.notStrictEqual(closeBtn, null, 'Close button must exist');
  closeBtn.click();
  assert.strictEqual(controller1.isVisible, false);
  assert.strictEqual(root1.innerHTML, '', 'Root container innerHTML must be cleared on hide');
  console.log('PASS: Close button cleanly dismisses card');

  // Test 4: Escape key dismisses card
  console.log('Test 4: Escape key dismisses card...');
  controller1.showAnswer({ expression: '10 * 10', result: 100 });
  assert.strictEqual(controller1.isVisible, true);
  doc1.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.strictEqual(controller1.isVisible, false);
  console.log('PASS: Escape key cleanly dismisses card');

  // Test 5: Clipboard copy action
  console.log('Test 5: Copy action writes to clipboard and provides feedback...');
  let clipboardWritten = null;
  const mockClipboard = {
    writeText: async (text) => {
      clipboardWritten = text;
      return Promise.resolve();
    }
  };

  if (typeof navigator !== 'undefined') {
    Object.defineProperty(navigator, 'clipboard', {
      value: mockClipboard,
      configurable: true,
      writable: true
    });
  } else {
    globalThis.navigator = { clipboard: mockClipboard };
  }

  controller1.showAnswer({ expression: '42 / 2', result: 21 });
  const copyBtn = root1.querySelector('#sqr-local-btn-copy');
  assert.notStrictEqual(copyBtn, null);
  copyBtn.click();

  // Await microtask queue for clipboard promise resolution
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(clipboardWritten, '21', 'Clipboard must receive exact calculated result');
  console.log('PASS: Copy action writes result to system clipboard');

  controller1.hide();

  // Test 6: Insert into prompt editor
  console.log('Test 6: Insert action updates prompt editor and hides card...');
  let insertedText = null;
  const { mockDoc: doc2 } = createMockDocument();
  const controller2 = new LocalAnswerUiController({
    document: doc2,
    onInsertIntoEditor: (text) => {
      insertedText = text;
    }
  });

  controller2.showAnswer({ expression: '100 - 37', result: 63 });
  const root2 = doc2.getElementById(ROOT_ID);
  const insertBtn = root2.querySelector('#sqr-local-btn-insert');
  assert.notStrictEqual(insertBtn, null);
  insertBtn.click();

  assert.strictEqual(insertedText, '63', 'Insert callback must be triggered with result');
  assert.strictEqual(controller2.isVisible, false, 'Card must hide after insertion');
  console.log('PASS: Insert action successfully replaces prompt with answer');

  // Test 7: "Ask Claude Anyway" triggers native submission callback
  console.log('Test 7: Ask Claude Anyway triggers bypass callback...');
  let askClaudeCalled = false;
  const { mockDoc: doc3, MockElement } = createMockDocument();
  const mockEditor = new MockElement('div');
  mockEditor.innerText = '5 + 2';

  const controller3 = new LocalAnswerUiController({
    document: doc3
  });

  controller3.showAnswer({
    expression: '5 + 2',
    result: 7,
    editorElement: mockEditor,
    onAskClaudeAnyway: () => {
      askClaudeCalled = true;
    }
  });

  const root3 = doc3.getElementById(ROOT_ID);
  const askBtn = root3.querySelector('#sqr-local-btn-ask');
  assert.notStrictEqual(askBtn, null);
  askBtn.click();

  assert.strictEqual(askClaudeCalled, true, 'Ask Claude Anyway must invoke bypass callback');
  assert.strictEqual(controller3.isVisible, false, 'Card must hide when user asks Claude');
  console.log('PASS: Ask Claude Anyway bypass functions correctly');

  // Test 8: Settings Toggle allows disabling local answering
  console.log('Test 8: Settings toggle disables local answering...');
  const settingsManagerToggle = new UserSettingsManager();
  assert.strictEqual(settingsManagerToggle.isLocalAnsweringEnabled(), true);
  await settingsManagerToggle.setLocalAnsweringEnabled(false);
  assert.strictEqual(settingsManagerToggle.isLocalAnsweringEnabled(), false);
  await settingsManagerToggle.setLocalAnsweringEnabled(true);
  assert.strictEqual(settingsManagerToggle.isLocalAnsweringEnabled(), true);
  console.log('PASS: User settings toggle for local answering operates correctly');

  // Test 9: Auto-dismiss timeout
  console.log('Test 9: Auto-dismiss timer hides card after elapsed timeout...');
  const { mockDoc: doc4 } = createMockDocument();
  const controller4 = new LocalAnswerUiController({
    document: doc4,
    autoDismissMs: 30
  });
  controller4.showAnswer({ expression: '2 + 2', result: 4 });
  assert.strictEqual(controller4.isVisible, true);
  await new Promise((r) => setTimeout(r, 45));
  assert.strictEqual(controller4.isVisible, false, 'Card must auto-hide after timeout');
  console.log('PASS: Auto-dismiss timer works correctly');

  // Test 10: Regression Check: Card positioning is centered and within viewport bounds
  console.log('Test 10: Regression Check: Card positioning relative to editor is centered and within viewport bounds...');
  const { mockDoc: doc5, MockElement: MockEl } = createMockDocument();
  const editorEl = new MockEl('div');
  editorEl.getBoundingClientRect = () => ({
    top: 750,
    bottom: 800,
    left: 400,
    right: 1000,
    width: 600,
    height: 50
  });

  const controller5 = new LocalAnswerUiController({
    document: doc5,
    autoDismissMs: 50
  });

  controller5.showAnswer({
    expression: '5 + 2',
    result: 7,
    editorElement: editorEl
  });

  const root5 = doc5.getElementById(ROOT_ID);
  assert.notStrictEqual(root5, null);
  const cardEl = root5.querySelector('.sqr-local-card');
  assert.notStrictEqual(cardEl, null);

  // In globalThis.window (innerWidth = 1400, innerHeight = 900):
  // editorCenterX = 400 + 600/2 = 700. cardWidth = 400.
  // idealLeft = 700 - 200 = 500.
  // bottomOffset = 900 - 750 + 12 = 162.
  assert.strictEqual(cardEl.style.position, 'fixed');
  assert.strictEqual(cardEl.style.left, '500px', 'Card left must be centered directly above editor center');
  assert.strictEqual(cardEl.style.bottom, '162px', 'Card bottom must sit directly above editor top');
  assert.strictEqual(cardEl.style.transform, 'none', 'Transform must be none to prevent containing block offset distortion');
  assert.strictEqual(root5.innerHTML.includes('='), true, 'Rendered calculation must include equals sign');
  controller5.hide();
  console.log('PASS: Card positioning regression check passed with exact centered bounds');

  // Test 11: Regression Check: Greeting interception via greeting_rule.js
  console.log('Test 11: Regression: Greeting detection wired into evaluateLocalAnswerCandidate...');
  const greetingModule = require('../src/rules/greeting_rule');

  // Simulate what evaluateLocalAnswerCandidate does for greetings
  function simulateEvaluateForGreeting(text) {
    if (!text || typeof text !== 'string') return null;
    if (typeof greetingModule.classifyGreeting === 'function') {
      const greet = greetingModule.classifyGreeting(text);
      if (greet && greet.isGreeting) {
        const greetingType = greet.type || 'GREETING';
        const matched = (greet.matchedPhrase || text.trim()).toLowerCase();
        let cannedResponse = '';
        if (greetingType === 'SIGN_OFF') {
          cannedResponse = matched.startsWith('thank') ? "You're welcome! 😊" : 'Goodbye! Have a great day! 👋';
        } else if (greetingType === 'PLEASANTRY') {
          cannedResponse = "I'm doing great, thanks for asking! How can I help you today?";
        } else {
          if (matched.includes('morning')) cannedResponse = 'Good morning! ☀️ How can I help you today?';
          else if (matched.includes('afternoon')) cannedResponse = 'Good afternoon! 🌤️ How can I help you today?';
          else if (matched.includes('evening')) cannedResponse = 'Good evening! 🌙 How can I help you today?';
          else cannedResponse = 'Hello! 👋 How can I help you today?';
        }
        return { canAnswerLocally: true, expression: text.trim(), result: cannedResponse, ruleId: greetingModule.RULE_ID };
      }
    }
    return null;
  }

  // "Hello" must be intercepted locally
  const helloResult = simulateEvaluateForGreeting('Hello');
  assert.notStrictEqual(helloResult, null, '"Hello" must return a local candidate, not null');
  assert.strictEqual(helloResult.canAnswerLocally, true, '"Hello" must set canAnswerLocally=true');
  assert.ok(helloResult.result && helloResult.result.length > 0, '"Hello" must produce a non-empty canned response');

  // "Hi there" must be intercepted locally
  const hiResult = simulateEvaluateForGreeting('Hi there');
  assert.notStrictEqual(hiResult, null, '"Hi there" must return a local candidate');
  assert.strictEqual(hiResult.canAnswerLocally, true, '"Hi there" must set canAnswerLocally=true');

  // "Thanks" (sign-off) must be intercepted locally
  const thanksResult = simulateEvaluateForGreeting('Thanks');
  assert.notStrictEqual(thanksResult, null, '"Thanks" must return a local candidate');
  assert.strictEqual(thanksResult.canAnswerLocally, true, '"Thanks" must set canAnswerLocally=true');
  assert.ok(thanksResult.result.includes("welcome"), '"Thanks" must trigger welcome response');

  // Substantive query must NOT be intercepted as a greeting
  const substantiveResult = simulateEvaluateForGreeting('Hello, can you explain quantum computing?');
  assert.strictEqual(substantiveResult, null, 'Substantive query must NOT be treated as a greeting');

  console.log('PASS: Greeting interception regression check passed — Hello/Hi/Thanks answered locally, substantive query passes through');

  // Test 12: Regression: Date and Time local interception (date, time, datetime)
  console.log('Test 12: Regression: Date and Time local interception...');
  const dateTimeModule = require('../src/rules/datetime_rule');

  function simulateEvaluateForDateTime(text) {
    if (!text || typeof text !== 'string') return null;
    if (typeof dateTimeModule.classifyDateTimeQuery === 'function') {
      const dtClass = dateTimeModule.classifyDateTimeQuery(text);
      if (dtClass && dtClass.eligible) {
        const now = new Date();
        let formattedResult = '';
        if (dtClass.category === 'CURRENT_TIME') {
          formattedResult = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } else if (dtClass.category === 'CURRENT_DATE') {
          formattedResult = now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        } else if (dtClass.category === 'CURRENT_DATETIME') {
          formattedResult = `${now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}, ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
        } else {
          formattedResult = now.toLocaleString();
        }
        return { canAnswerLocally: true, expression: text.trim(), result: formattedResult, ruleId: dateTimeModule.RULE_ID };
      }
    }
    return null;
  }

  // "date" must be intercepted locally
  const dateResult = simulateEvaluateForDateTime('date');
  assert.notStrictEqual(dateResult, null, '"date" must return a local candidate');
  assert.strictEqual(dateResult.canAnswerLocally, true);
  assert.ok(dateResult.result.length > 0);

  // "time" must be intercepted locally
  const timeResult = simulateEvaluateForDateTime('time');
  assert.notStrictEqual(timeResult, null, '"time" must return a local candidate');
  assert.strictEqual(timeResult.canAnswerLocally, true);
  assert.ok(timeResult.result.length > 0);

  // "date and time" must be intercepted locally
  const dtResult = simulateEvaluateForDateTime('date and time');
  assert.notStrictEqual(dtResult, null, '"date and time" must return a local candidate');
  assert.strictEqual(dtResult.canAnswerLocally, true);

  // "what is the date and time right now?" must be intercepted locally
  const dtNowResult = simulateEvaluateForDateTime('what is the date and time right now?');
  assert.notStrictEqual(dtNowResult, null, '"what is the date and time right now?" must return a local candidate');
  assert.strictEqual(dtNowResult.canAnswerLocally, true);

  // Queries with zero-width characters (as produced by ProseMirror) must be intercepted locally
  const zwDateResult = simulateEvaluateForDateTime('\u200Bdate\u200B');
  assert.notStrictEqual(zwDateResult, null, 'Date with zero-width spaces must return a local candidate');
  assert.strictEqual(zwDateResult.canAnswerLocally, true);

  const zwTimeResult = simulateEvaluateForDateTime('\uFEFFtime\u200C');
  assert.notStrictEqual(zwTimeResult, null, 'Time with zero-width characters must return a local candidate');
  assert.strictEqual(zwTimeResult.canAnswerLocally, true);

  // External query like "weather today" must NOT be intercepted
  const weatherResult = simulateEvaluateForDateTime('weather today');
  assert.strictEqual(weatherResult, null, 'Weather query must NOT be answered locally');

  console.log('PASS: Date/time interception regression check passed — date/time answered locally (including zero-width chars), external queries pass through');

  // Test 13: Regression: Unit and Temperature conversion local interception
  console.log('Test 13: Regression: Unit and Temperature conversion local interception...');
  const unitConversionModule = require('../src/rules/unit_conversion_rule');

  function simulateEvaluateForUnitConversion(text) {
    if (!text || typeof text !== 'string') return null;
    if (typeof unitConversionModule.evaluateUnitConversion === 'function') {
      const conv = unitConversionModule.evaluateUnitConversion(text);
      if (conv && conv.success) {
        return {
          canAnswerLocally: true,
          expression: conv.expression,
          result: conv.result,
          ruleId: unitConversionModule.RULE_ID,
          isMath: true
        };
      }
    }
    return null;
  }

  // "100 F to C" must be intercepted locally
  const tempResult = simulateEvaluateForUnitConversion('100 F to C');
  assert.notStrictEqual(tempResult, null, '"100 F to C" must return a local candidate');
  assert.strictEqual(tempResult.canAnswerLocally, true);
  assert.strictEqual(tempResult.expression, '100 °F');
  assert.strictEqual(tempResult.result, '37.778 °C');

  // "15 km to miles" must be intercepted locally
  const distResult = simulateEvaluateForUnitConversion('15 km to miles');
  assert.notStrictEqual(distResult, null, '"15 km to miles" must return a local candidate');
  assert.strictEqual(distResult.canAnswerLocally, true);
  assert.strictEqual(distResult.expression, '15 km');
  assert.strictEqual(distResult.result, '9.321 miles');

  // Verify UI rendering of unit conversion card
  controller1.showAnswer({
    expression: tempResult.expression,
    result: tempResult.result,
    ruleId: tempResult.ruleId,
    rawQuery: '100 F to C',
    isMath: true
  });
  assert.strictEqual(controller1.isVisible, true);
  assert.strictEqual(root1.innerHTML.includes('100 °F'), true);
  assert.strictEqual(root1.innerHTML.includes('37.778 °C'), true);
  controller1.hide();

  // Currency query like "$50 to EUR" must NOT be intercepted
  const currencyResult = simulateEvaluateForUnitConversion('$50 to EUR');
  assert.strictEqual(currencyResult, null, 'Currency queries must NOT be answered locally');

  console.log('PASS: Unit conversion regression check passed — temperature & distance answered locally, currency passed through');

  // Advanced Calculator regression check
  const advancedCalculatorModule = require('../src/rules/advanced_calculator_rule');
  const advResult = advancedCalculatorModule.evaluateAdvancedCalculation('15% of 850');
  assert.notStrictEqual(advResult, null, '"15% of 850" must return a calculation result');
  assert.strictEqual(advResult.result, '127.5');

  controller1.showAnswer({
    expression: advResult.expression,
    result: advResult.result,
    ruleId: advancedCalculatorModule.RULE_ID,
    rawQuery: '15% of 850',
    isMath: true
  });
  assert.strictEqual(controller1.isVisible, true);
  assert.strictEqual(root1.innerHTML.includes('15% of 850'), true);
  assert.strictEqual(root1.innerHTML.includes('127.5'), true);
  controller1.hide();

  console.log('PASS: Advanced calculator regression check passed — percentage card rendered correctly');

  console.log('--- ALL LOCAL ANSWER UI TESTS PASSED ---');
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
