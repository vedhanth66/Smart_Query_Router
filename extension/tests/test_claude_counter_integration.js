/**
 * Integration & Integrity Tests for Claude Counter in Smart Query Router
 *
 * Verifies:
 * 1. All Claude Counter source assets exist (CSS, JS, injected bridge, vendor tokenizer).
 * 2. Manifest V3 correctly wires Claude Counter CSS, content scripts, and web_accessible_resources.
 * 3. Execution of Claude Counter constants, tokenizer, and tokens trunk metric calculations.
 * 4. Co-existence with Smart Query Router modules without namespace collisions.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');

// --- 1. File Existence & Integrity ---
const requiredFiles = [
  'src/styles.css',
  'src/content/constants.js',
  'src/content/bridge-client.js',
  'src/vendor/o200k_base.js',
  'src/content/tokens.js',
  'src/content/ui.js',
  'src/content/main.js',
  'src/injected/bridge.js'
];

for (const relPath of requiredFiles) {
  const fullPath = path.join(EXTENSION_ROOT, relPath);
  assert(fs.existsSync(fullPath), `Required Claude Counter file missing: ${relPath}`);
  const stat = fs.statSync(fullPath);
  assert(stat.size > 0, `Claude Counter file is empty: ${relPath}`);
}
console.log('PASS: All 8 Claude Counter files exist and have non-zero size');

// --- 2. Manifest V3 Declarations ---
const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
const cs = manifest.content_scripts && manifest.content_scripts[0];
assert(cs, 'Manifest must have at least one content_scripts entry');
assert(Array.isArray(cs.css) && cs.css.includes('src/styles.css'), 'manifest css must include src/styles.css');

const expectedScripts = [
  'src/content/constants.js',
  'src/content/bridge-client.js',
  'src/vendor/o200k_base.js',
  'src/content/tokens.js',
  'src/content/ui.js',
  'src/content/main.js'
];

for (const script of expectedScripts) {
  assert(cs.js.includes(script), `manifest content_scripts.js must include ${script}`);
}

const war = manifest.web_accessible_resources;
assert(Array.isArray(war) && war.length > 0, 'manifest must have web_accessible_resources');
const bridgeExposed = war.some(entry =>
  Array.isArray(entry.resources) &&
  entry.resources.includes('src/injected/bridge.js') &&
  entry.matches.includes('https://claude.ai/*')
);
assert(bridgeExposed, 'manifest web_accessible_resources must expose src/injected/bridge.js to https://claude.ai/*');
console.log('PASS: Manifest correctly declares Claude Counter CSS, scripts, and web accessible bridge');

// --- 3. Module Execution & Namespace Verification in VM ---
const mockWindow = {
  location: { pathname: '/chat/test-conv-123' },
  addEventListener: () => {},
  removeEventListener: () => {},
  postMessage: () => {},
  innerWidth: 1024,
  innerHeight: 768
};

class MockMutationObserver {
  observe() {}
  disconnect() {}
}

const mockDocument = {
  documentElement: {
    dataset: { mode: 'dark' }
  },
  contains: () => true,
  createElement: (tag) => {
    const el = {
      tagName: tag.toUpperCase(),
      className: '',
      style: {
        setProperty: () => {},
        getPropertyValue: () => ''
      },
      children: [],
      childNodes: [],
      classList: {
        add(c) { el.className += ' ' + c; },
        remove() {}
      },
      setAttribute() {},
      getAttribute() { return null; },
      hasAttribute() { return false; },
      appendChild(c) { el.children.push(c); return c; },
      addEventListener: () => {},
      removeEventListener: () => {},
      getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 30, right: 100, bottom: 30 }; },
      textContent: ''
    };
    return el;
  },
  head: {
    appendChild: () => {}
  },
  body: {
    appendChild: () => {}
  },
  cookie: 'lastActiveOrg=test-org-456'
};

const sandbox = {
  window: mockWindow,
  document: mockDocument,
  MutationObserver: MockMutationObserver,
  console,
  setTimeout,
  clearTimeout,
  Date,
  WeakSet,
  Map,
  Set,
  Array,
  Object,
  JSON,
  TextDecoder,
  TextEncoder,
  crypto: globalThis.crypto,
  chrome: {
    runtime: {
      getURL: (path) => `chrome-extension://dummy-id/${path}`
    }
  }
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);

// Load constants
const constantsCode = fs.readFileSync(path.join(EXTENSION_ROOT, 'src/content/constants.js'), 'utf8');
vm.runInContext(constantsCode, sandbox);
assert(sandbox.ClaudeCounter, 'ClaudeCounter global must be defined');
assert(sandbox.ClaudeCounter.DOM.CHAT_MENU_TRIGGER === '[data-testid="chat-menu-trigger"]');
assert(sandbox.ClaudeCounter.CONST.CACHE_WINDOW_MS === 300000);
assert(sandbox.ClaudeCounter.COLORS.RED_WARNING === '#ce2029');
console.log('PASS: ClaudeCounter constants and DOM selectors loaded correctly');

// Load vendor tokenizer
const vendorCode = fs.readFileSync(path.join(EXTENSION_ROOT, 'src/vendor/o200k_base.js'), 'utf8');
vm.runInContext(vendorCode, sandbox);
assert(sandbox.GPTTokenizer_o200k_base, 'GPTTokenizer_o200k_base should be defined');
assert(typeof sandbox.GPTTokenizer_o200k_base.countTokens === 'function', 'tokenizer countTokens should be a function');

const testTokenCount = sandbox.GPTTokenizer_o200k_base.countTokens('Hello world! How are you today?');
assert(testTokenCount > 0, `Tokenizer should count tokens: got ${testTokenCount}`);
console.log(`PASS: Vendor o200k_base tokenizer works properly (counted ${testTokenCount} tokens)`);

// Load tokens.js
const tokensCode = fs.readFileSync(path.join(EXTENSION_ROOT, 'src/content/tokens.js'), 'utf8');
vm.runInContext(tokensCode, sandbox);
assert(sandbox.ClaudeCounter.tokens, 'ClaudeCounter.tokens should be defined');
assert(typeof sandbox.ClaudeCounter.tokens.computeConversationMetrics === 'function');

// Test metrics computation on sample conversation trunk
async function runTokensTest() {
  const sampleConversation = {
    current_leaf_message_uuid: 'msg-2',
    chat_messages: [
      {
        uuid: 'msg-1',
        parent_message_uuid: '00000000-0000-4000-8000-000000000000',
        sender: 'human',
        content: [{ type: 'text', text: 'What is 25 * 4?' }],
        created_at: '2026-10-08T10:00:00Z'
      },
      {
        uuid: 'msg-2',
        parent_message_uuid: 'msg-1',
        sender: 'assistant',
        content: [{ type: 'text', text: '25 * 4 = 100.' }],
        created_at: '2026-10-08T10:00:05Z'
      }
    ]
  };

  const metrics = await sandbox.ClaudeCounter.tokens.computeConversationMetrics(sampleConversation);
  assert.strictEqual(metrics.trunkMessageCount, 2, 'Should process 2 messages in trunk');
  assert(metrics.totalTokens > 0, 'Total tokens should be > 0');
  assert(metrics.lastAssistantMs !== null, 'Last assistant timestamp should be populated');
  assert(metrics.cachedUntil === metrics.lastAssistantMs + 300000, 'Cache expiration should match 5 min window');
  console.log(`PASS: Conversation metrics calculated correctly (${metrics.totalTokens} tokens, cache until ${new Date(metrics.cachedUntil).toISOString()})`);
}

// Load ui.js
const uiCode = fs.readFileSync(path.join(EXTENSION_ROOT, 'src/content/ui.js'), 'utf8');
vm.runInContext(uiCode, sandbox);
assert(sandbox.ClaudeCounter.ui, 'ClaudeCounter.ui should be defined');
assert(typeof sandbox.ClaudeCounter.ui.CounterUI === 'function', 'CounterUI constructor should exist');

sandbox.ClaudeCounter.waitForElement = () => Promise.resolve(null);
const counterUi = new sandbox.ClaudeCounter.ui.CounterUI();
counterUi.initialize();
assert(counterUi.headerContainer, 'CounterUI should instantiate headerContainer');
assert(counterUi.usageLine, 'CounterUI should instantiate usageLine');
console.log('PASS: CounterUI component initialized cleanly without errors');

// --- 4. Verify No Collisions with Smart Query Router ---
const tokenCounterCode = fs.readFileSync(path.join(EXTENSION_ROOT, 'src/shared/token_counter.js'), 'utf8');
vm.runInContext(tokenCounterCode, sandbox);
assert(sandbox.SmartQueryRouterTokenCounter, 'SmartQueryRouterTokenCounter should load alongside ClaudeCounter');
assert(typeof sandbox.SmartQueryRouterTokenCounter.countTokens === 'function');
assert(typeof sandbox.SmartQueryRouterTokenCounter.estimateConsumedTokens === 'function');
assert(typeof sandbox.ClaudeCounter.tokens.computeConversationMetrics === 'function');
console.log('PASS: Zero namespace collisions between ClaudeCounter and SmartQueryRouter');

runTokensTest().then(() => {
  console.log('\n--- ALL CLAUDE COUNTER INTEGRATION CHECKS PASSED ---');
}).catch((err) => {
  console.error('Claude counter test error:', err);
  process.exit(1);
});
