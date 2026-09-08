/**
 * Test Suite: Popup Surface Controller & Status Rendering
 * 
 * Verifies:
 * 1. Popup controller initializes and renders initial zero state.
 * 2. Enabled state toggle switch updates UserSettingsManager.
 * 3. Routing mode dropdown selector updates UserSettingsManager.
 * 4. High-level metric cards render correctly: token savings, cache hit rate, route distribution.
 * 5. Recent activity list populates without raw user prompts or conversation content.
 * 6. Empty state toggles cleanly when activity is absent vs present.
 * 7. Reset stats clears in-memory and UI counters.
 */

const assert = require('assert');
const popupModule = require('../src/popup/popup');
const metricsModule = require('../src/shared/optimizer_metrics');
const userSettingsModule = require('../src/shared/user_settings');

const { PopupController, formatRelativeTime } = popupModule;
const { OptimizerMetricsTracker } = metricsModule;
const { UserSettingsManager } = userSettingsModule;

/**
 * Lightweight mock DOM document for testing popup rendering in Node
 */
function createMockPopupDocument() {
  const elements = new Map();

  class MockElement {
    constructor(tagName, id = '') {
      this.tagName = tagName.toUpperCase();
      this.id = id;
      this.className = '';
      this.textContent = '';
      this.innerHTML = '';
      this.value = '';
      this.checked = false;
      this.style = {};
      this.listeners = new Map();
      this.children = [];
      this.parentNode = null;
      if (id) {
        elements.set(id, this);
      }
    }

    classList = {
      add: (cls) => {
        if (!this.className.includes(cls)) {
          this.className = (this.className + ' ' + cls).trim();
        }
      },
      remove: (cls) => {
        this.className = this.className.replace(cls, '').trim();
      },
      contains: (cls) => this.className.includes(cls)
    };

    addEventListener(event, fn) {
      if (!this.listeners.has(event)) {
        this.listeners.set(event, []);
      }
      this.listeners.get(event).push(fn);
    }

    dispatchEvent(evt) {
      const handlers = this.listeners.get(evt.type) || [];
      for (const h of handlers) {
        h(evt);
      }
    }

    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    }
  }

  // Pre-create elements expected in popup.html
  const doc = {
    createElement: (tag) => new MockElement(tag),
    getElementById: (id) => elements.get(id) || null
  };

  new MockElement('span', 'status-indicator');
  new MockElement('input', 'toggle-optimizer');
  new MockElement('select', 'select-routing-override');
  new MockElement('div', 'stat-tokens-saved');
  new MockElement('div', 'stat-cache-hit-rate');
  new MockElement('div', 'stat-cache-detail');
  new MockElement('span', 'stat-total-routed');
  new MockElement('div', 'bar-small');
  new MockElement('div', 'bar-strong');
  new MockElement('span', 'label-small');
  new MockElement('span', 'label-strong');
  new MockElement('span', 'activity-count');
  new MockElement('div', 'activity-empty');
  new MockElement('ul', 'activity-list');
  new MockElement('button', 'btn-reset-metrics');

  return { doc, elements };
}

console.log('--- Running Popup Surface Tests ---');

// Test 1: formatRelativeTime helper
console.log('Test 1: formatRelativeTime helper...');
assert.strictEqual(formatRelativeTime(Date.now() - 2000), 'Just now');
assert.strictEqual(formatRelativeTime(Date.now() - 35000), '35s ago');
assert.strictEqual(formatRelativeTime(Date.now() - 180000), '3m ago');
assert.strictEqual(formatRelativeTime(Date.now() - 7200000), '2h ago');
console.log('PASS: formatRelativeTime correctly formats durations');

// Test 2: Initial Render with Zero State
console.log('Test 2: Initial popup render with zero metrics...');
const { doc: doc1, elements: el1 } = createMockPopupDocument();
const settings1 = new UserSettingsManager();
const metrics1 = new OptimizerMetricsTracker({ userSettingsManager: settings1 });
const popup1 = new PopupController({
  document: doc1,
  userSettingsManager: settings1,
  metricsTracker: metrics1
});

popup1.init();

assert.strictEqual(el1.get('toggle-optimizer').checked, true);
assert.strictEqual(el1.get('status-indicator').textContent, 'Active');
assert.strictEqual(el1.get('stat-tokens-saved').textContent, '0');
assert.strictEqual(el1.get('stat-cache-hit-rate').textContent, '0.0%');
assert.strictEqual(el1.get('activity-empty').style.display, 'flex');
assert.strictEqual(el1.get('activity-list').style.display, 'none');
console.log('PASS: Zero state renders cleanly with Active status and empty activity');

// Test 3: Optimizer Toggle Interaction
console.log('Test 3: Toggling optimizer switch...');
const toggleInput = el1.get('toggle-optimizer');
toggleInput.checked = false;
toggleInput.dispatchEvent({ type: 'change', target: toggleInput });

assert.strictEqual(settings1.isOptimizationEnabled(), false);
assert.strictEqual(el1.get('status-indicator').textContent, 'Paused');
assert.strictEqual(el1.get('status-indicator').classList.contains('paused'), true);
console.log('PASS: Disabling optimizer updates settings and changes status badge to Paused');

// Test 4: Routing Mode Selector Interaction
console.log('Test 4: Changing routing mode dropdown...');
const selectMode = el1.get('select-routing-override');
selectMode.value = 'prefer-simple';
selectMode.dispatchEvent({ type: 'change', target: selectMode });

assert.strictEqual(settings1.getRoutingOverride(), 'prefer-simple');
console.log('PASS: Routing mode dropdown correctly updates UserSettingsManager');

// Test 5: Populating Activity & Metrics
console.log('Test 5: Populating metrics and recent activity list...');
// Re-enable optimizer
toggleInput.checked = true;
toggleInput.dispatchEvent({ type: 'change', target: toggleInput });

// Record some activity
metrics1.recordActivity({
  route: 'Fast Arithmetic Rule',
  modelTier: 'local',
  cacheOutcome: 'NOT_CHECKED',
  tokensSaved: 15,
  latencyMs: 5,
  status: 'APPLIED'
});

metrics1.recordActivity({
  route: 'simple-model candidate',
  modelTier: 'simple',
  cacheOutcome: 'HIT',
  tokensSaved: 120,
  latencyMs: 35,
  status: 'COMPLETED'
});

metrics1.recordActivity({
  route: 'strong-model candidate',
  modelTier: 'strong',
  cacheOutcome: 'MISS',
  tokensSaved: 0,
  latencyMs: 1450,
  status: 'COMPLETED'
});

popup1.render();

// Check metrics
assert.strictEqual(el1.get('stat-tokens-saved').textContent, '135');
assert.strictEqual(el1.get('stat-cache-hit-rate').textContent, '50.0%'); // 1 hit, 1 miss out of 2 evaluated
assert.strictEqual(el1.get('stat-total-routed').textContent, '3 queries');
assert.strictEqual(el1.get('label-small').innerHTML.includes('50.0%'), true);
assert.strictEqual(el1.get('label-strong').innerHTML.includes('50.0%'), true);

// Check activity list
assert.strictEqual(el1.get('activity-empty').style.display, 'none');
assert.strictEqual(el1.get('activity-list').style.display, 'block');
assert.strictEqual(el1.get('activity-list').children.length, 3);
assert.strictEqual(el1.get('activity-count').textContent, '3 items');

// Strict Privacy Invariant: Verify zero user prompts appear in the rendered list
const renderedHtml = el1.get('activity-list').innerHTML;
assert.strictEqual(renderedHtml.includes('raw_prompt'), false);
assert.strictEqual(renderedHtml.includes('promptText'), false);
assert.strictEqual(renderedHtml.includes('query_text'), false);
console.log('PASS: Metrics and activity list rendered with strict privacy verification');

// Test 6: Reset Stats Button
console.log('Test 6: Reset stats button...');
const resetBtn = el1.get('btn-reset-metrics');
resetBtn.dispatchEvent({ type: 'click' });

// Wait a tick for async reset
setTimeout(() => {
  popup1.render();
  assert.strictEqual(el1.get('stat-tokens-saved').textContent, '0');
  assert.strictEqual(el1.get('stat-cache-hit-rate').textContent, '0.0%');
  assert.strictEqual(el1.get('activity-empty').style.display, 'flex');
  assert.strictEqual(el1.get('activity-list').style.display, 'none');
  console.log('PASS: Reset button clears stats and restores empty state');
  console.log('--- ALL POPUP SURFACE TESTS PASSED ---');
}, 50);
