/**
 * Smart Query Router - User Settings & Routing Preference Tests
 * Verifies:
 * 1. Default settings structure and default to 'automatic'.
 * 2. UserRoutingOverride enum values ('automatic', 'prefer-simple', 'prefer-strong').
 * 3. Validation helper rejecting invalid values and accepting valid overrides.
 * 4. createUserSettings factory merging and immutability.
 * 5. UserSettingsManager in-memory methods (getSettings, getRoutingOverride, setRoutingOverride, reset).
 * 6. Storage persistence and async load with simulated chrome.storage.
 * 7. Change listener subscriptions and notification callbacks.
 * 8. Extensibility: future settings preserved alongside routingOverride.
 */

'use strict';

const assert = require('assert');
const {
  UserRoutingOverride,
  DEFAULT_USER_SETTINGS,
  STORAGE_KEY,
  validateUserSettings,
  createUserSettings,
  UserSettingsManager,
  defaultUserSettingsManager
} = require('../src/shared/user_settings');

console.log('--- Running User Settings & Routing Preferences Tests ---');

// Test 1: Verifying default settings
console.log('Test 1: Verifying default settings and automatic fallback...');
assert.strictEqual(DEFAULT_USER_SETTINGS.version, '1.0.0');
assert.strictEqual(DEFAULT_USER_SETTINGS.routingOverride, UserRoutingOverride.AUTOMATIC);
assert.strictEqual(DEFAULT_USER_SETTINGS.routingOverride, 'automatic');
assert.strictEqual(defaultUserSettingsManager.getRoutingOverride(), 'automatic');
console.log('PASS: Default settings default to automatic routing verified');

// Test 2: Verifying UserRoutingOverride enum
console.log('Test 2: Verifying UserRoutingOverride enum values...');
assert.strictEqual(UserRoutingOverride.AUTOMATIC, 'automatic');
assert.strictEqual(UserRoutingOverride.PREFER_SIMPLE, 'prefer-simple');
assert.strictEqual(UserRoutingOverride.PREFER_STRONG, 'prefer-strong');
assert.strictEqual(Object.keys(UserRoutingOverride).length, 3);
console.log('PASS: Exactly 3 user routing override values verified');

// Test 3: Validation helper
console.log('Test 3: Validation logic for user settings...');
const valid1 = validateUserSettings({ version: '1.0.0', routingOverride: 'automatic' });
assert.strictEqual(valid1.valid, true);

const valid2 = validateUserSettings({ version: '1.0.0', routingOverride: 'prefer-simple' });
assert.strictEqual(valid2.valid, true);

const valid3 = validateUserSettings({ version: '1.0.0', routingOverride: 'prefer-strong' });
assert.strictEqual(valid3.valid, true);

const invalid1 = validateUserSettings(null);
assert.strictEqual(invalid1.valid, false);

const invalid2 = validateUserSettings({ version: '', routingOverride: 'automatic' });
assert.strictEqual(invalid2.valid, false);

const invalid3 = validateUserSettings({ version: '1.0.0', routingOverride: 'prefer-fastest' });
assert.strictEqual(invalid3.valid, false);
assert.ok(invalid3.error.includes('prefer-fastest'));

const invalid4 = validateUserSettings({ version: '1.0.0', routingOverride: 123 });
assert.strictEqual(invalid4.valid, false);
console.log('PASS: Schema validation correctly handles valid and invalid settings');

// Test 4: createUserSettings factory
console.log('Test 4: createUserSettings factory and immutability...');
const defaultSettings = createUserSettings();
assert.strictEqual(defaultSettings.routingOverride, 'automatic');
assert.throws(() => { defaultSettings.routingOverride = 'prefer-simple'; }, TypeError);

const customized = createUserSettings({ routingOverride: 'prefer-simple' });
assert.strictEqual(customized.routingOverride, 'prefer-simple');
assert.strictEqual(customized.version, '1.0.0');

assert.throws(() => {
  createUserSettings({ routingOverride: 'invalid-choice' });
}, /Invalid user settings/);
console.log('PASS: Settings factory and immutability verified');

// Test 5: UserSettingsManager in-memory behavior
console.log('Test 5: UserSettingsManager in-memory CRUD operations...');
const manager = new UserSettingsManager();
assert.strictEqual(manager.getRoutingOverride(), 'automatic');

manager.updateSettings({ routingOverride: 'prefer-strong' });
assert.strictEqual(manager.getRoutingOverride(), 'prefer-strong');

manager.setRoutingOverride('prefer-simple');
assert.strictEqual(manager.getRoutingOverride(), 'prefer-simple');

manager.reset();
assert.strictEqual(manager.getRoutingOverride(), 'automatic');
console.log('PASS: In-memory CRUD operations verified');

// Test 6: Storage persistence and async load
console.log('Test 6: Simulated storage persistence and async load...');
(async () => {
  const store = {};
  const mockStorage = {
    get: (keys, callback) => {
      const result = {};
      for (const k of keys) {
        if (store[k] !== undefined) result[k] = store[k];
      }
      callback(result);
    },
    set: (items, callback) => {
      Object.assign(store, items);
      if (callback) callback();
    }
  };

  const persistentManager = new UserSettingsManager({ storage: mockStorage });
  assert.strictEqual(persistentManager.getRoutingOverride(), 'automatic');

  await persistentManager.setRoutingOverride('prefer-strong');
  assert.strictEqual(persistentManager.getRoutingOverride(), 'prefer-strong');
  assert.ok(store[STORAGE_KEY]);
  assert.strictEqual(store[STORAGE_KEY].routingOverride, 'prefer-strong');

  // Create another manager pointing to the same storage and verify load
  const secondManager = new UserSettingsManager({ storage: mockStorage });
  await secondManager.load();
  assert.strictEqual(secondManager.getRoutingOverride(), 'prefer-strong');
  console.log('PASS: Storage persistence and loading verified');

  // Test 7: Change listeners
  console.log('Test 7: Listener subscriptions and notification callbacks...');
  const listenerEvents = [];
  const listener = (newSettings, oldSettings) => {
    listenerEvents.push({ from: oldSettings.routingOverride, to: newSettings.routingOverride });
  };

  secondManager.addListener(listener);
  await secondManager.setRoutingOverride('prefer-simple');
  assert.strictEqual(listenerEvents.length, 1);
  assert.strictEqual(listenerEvents[0].from, 'prefer-strong');
  assert.strictEqual(listenerEvents[0].to, 'prefer-simple');

  secondManager.removeListener(listener);
  await secondManager.setRoutingOverride('automatic');
  assert.strictEqual(listenerEvents.length, 1); // Not called after removal
  console.log('PASS: Listener subscription and removal verified');

  // Test 8: Non-breaking coexistence with future settings
  console.log('Test 8: Extensibility for future settings page...');
  const extendedManager = new UserSettingsManager();
  await extendedManager.updateSettings({ routingOverride: 'prefer-strong' });
  const snapshot = extendedManager.getSettings();
  assert.strictEqual(snapshot.routingOverride, 'prefer-strong');
  assert.strictEqual(snapshot.version, '1.0.0');
  console.log('PASS: Settings object ready for future settings page exposure without altering core');

  console.log('--- ALL USER SETTINGS TESTS PASSED ---');
})();
