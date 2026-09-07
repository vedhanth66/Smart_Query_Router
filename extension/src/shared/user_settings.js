/**
 * Smart Query Router - User Settings & Routing Preferences
 * 
 * DESIGN PRINCIPLES:
 * - Internal concept of an optional user routing override:
 *   1. 'automatic': Standard autonomous deterministic routing (DEFAULT).
 *   2. 'prefer-simple': Expresses user preference for fast, simpler models.
 *   3. 'prefer-strong': Expresses user preference for deep, complex models.
 * - System strictly defaults to 'automatic'.
 * - Preserved in an isolated settings object so future settings pages can expose it
 *   without changing the routing core.
 * - Safe persistence: supports chrome.storage (sync/local) with synchronous in-memory fallback.
 * - Pub/sub change listeners for decoupled reactivity.
 * - Cross-environment UMD wrapper (Node.js, Service Worker, Content Scripts).
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SmartQueryRouterUserSettings = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /**
   * User routing override values
   */
  const UserRoutingOverride = Object.freeze({
    AUTOMATIC: 'automatic',
    PREFER_SIMPLE: 'prefer-simple',
    PREFER_STRONG: 'prefer-strong'
  });

  /**
   * Default user settings configuration
   */
  const DEFAULT_USER_SETTINGS = Object.freeze({
    version: '1.0.0',
    routingOverride: UserRoutingOverride.AUTOMATIC
  });

  const STORAGE_KEY = 'smart_query_router_user_settings';

  /**
   * Validates a settings object against the expected schema
   * @param {any} settings
   * @returns {{ valid: boolean, error?: string }}
   */
  function validateUserSettings(settings) {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
      return { valid: false, error: 'Settings must be a non-null object' };
    }

    if (typeof settings.version !== 'string' || !settings.version.trim()) {
      return { valid: false, error: 'Settings requires a non-empty string "version"' };
    }

    const validOverrides = Object.values(UserRoutingOverride);
    if (!validOverrides.includes(settings.routingOverride)) {
      return {
        valid: false,
        error: `Invalid routingOverride: "${settings.routingOverride}". Expected one of: ${validOverrides.join(', ')}`
      };
    }

    return { valid: true };
  }

  /**
   * Creates a valid, frozen settings object with defaults and overrides
   * @param {object} [overrides]
   * @returns {Readonly<object>}
   */
  function createUserSettings(overrides = {}) {
    if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) {
      return Object.freeze({ ...DEFAULT_USER_SETTINGS });
    }

    const merged = {
      version: typeof overrides.version === 'string' && overrides.version.trim()
        ? overrides.version.trim()
        : DEFAULT_USER_SETTINGS.version,
      routingOverride: typeof overrides.routingOverride === 'string' && overrides.routingOverride.trim()
        ? overrides.routingOverride.trim()
        : DEFAULT_USER_SETTINGS.routingOverride
    };

    const validation = validateUserSettings(merged);
    if (!validation.valid) {
      throw new Error(`Invalid user settings: ${validation.error}`);
    }

    return Object.freeze(merged);
  }

  /**
   * UserSettingsManager
   * In-memory cache with storage persistence and listener subscriptions.
   */
  class UserSettingsManager {
    /**
     * @param {object} [options]
     * @param {object} [options.storage] - Storage provider (chrome.storage.sync / chrome.storage.local)
     * @param {object} [options.initialSettings] - Initial settings overrides
     */
    constructor(options = {}) {
      this.storage = options.storage || null;
      this.listeners = new Set();
      this.currentSettings = createUserSettings(options.initialSettings || {});

      // Auto-load if storage is provided
      if (this.storage && typeof this.storage.get === 'function') {
        this.load().catch(() => {});
      }
    }

    /**
     * Returns a copy of the current settings snapshot
     * @returns {object}
     */
    getSettings() {
      return { ...this.currentSettings };
    }

    /**
     * Returns the active routing override value (defaults to 'automatic')
     * @returns {string}
     */
    getRoutingOverride() {
      return this.currentSettings.routingOverride || UserRoutingOverride.AUTOMATIC;
    }

    /**
     * Set routing override specifically
     * @param {string} override
     * @returns {Promise<object>}
     */
    async setRoutingOverride(override) {
      return this.updateSettings({ routingOverride: override });
    }

    /**
     * Updates settings with partial values, validates, and persists
     * @param {object} partial
     * @returns {Promise<object>}
     */
    async updateSettings(partial = {}) {
      const candidate = {
        ...this.currentSettings,
        ...(partial || {})
      };

      const updated = createUserSettings(candidate);
      const oldSettings = this.currentSettings;
      this.currentSettings = updated;

      // Persist to storage if available
      if (this.storage && typeof this.storage.set === 'function') {
        try {
          await new Promise((resolve, reject) => {
            this.storage.set({ [STORAGE_KEY]: updated }, () => {
              if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
              } else {
                resolve();
              }
            });
          });
        } catch (err) {
          // Log or silently keep in-memory on storage failure
        }
      }

      // Notify registered listeners
      this._notifyListeners(updated, oldSettings);
      return { ...updated };
    }

    /**
     * Loads settings from storage asynchronously
     * @returns {Promise<object>}
     */
    async load() {
      if (!this.storage || typeof this.storage.get !== 'function') {
        return this.getSettings();
      }

      return new Promise((resolve) => {
        try {
          this.storage.get([STORAGE_KEY], (items) => {
            if (items && items[STORAGE_KEY]) {
              try {
                const loaded = createUserSettings(items[STORAGE_KEY]);
                const old = this.currentSettings;
                this.currentSettings = loaded;
                this._notifyListeners(loaded, old);
              } catch (_) {
                // Keep current settings if stored data is corrupted
              }
            }
            resolve(this.getSettings());
          });
        } catch (_) {
          resolve(this.getSettings());
        }
      });
    }

    /**
     * Resets settings back to default
     * @returns {Promise<object>}
     */
    async reset() {
      return this.updateSettings(DEFAULT_USER_SETTINGS);
    }

    /**
     * Registers a listener for settings changes
     * @param {Function} listener - (newSettings, oldSettings) => void
     */
    addListener(listener) {
      if (typeof listener === 'function') {
        this.listeners.add(listener);
      }
    }

    /**
     * Unregisters a listener
     * @param {Function} listener
     */
    removeListener(listener) {
      this.listeners.delete(listener);
    }

    /**
     * Internal listener notification
     * @private
     */
    _notifyListeners(newSettings, oldSettings) {
      for (const listener of this.listeners) {
        try {
          listener({ ...newSettings }, { ...oldSettings });
        } catch (_) {}
      }
    }
  }

  // Resolve storage safely if chrome.storage is available
  let resolvedStorage = null;
  if (typeof chrome !== 'undefined' && chrome.storage) {
    resolvedStorage = chrome.storage.sync || chrome.storage.local || null;
  }

  const defaultUserSettingsManager = new UserSettingsManager({ storage: resolvedStorage });

  return {
    UserRoutingOverride,
    DEFAULT_USER_SETTINGS,
    STORAGE_KEY,
    validateUserSettings,
    createUserSettings,
    UserSettingsManager,
    defaultUserSettingsManager
  };
});
