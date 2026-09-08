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
    let privacyModule = null;
    try {
      privacyModule = require('./privacy_config');
    } catch (_) {}
    module.exports = factory(privacyModule);
  } else {
    root.SmartQueryRouterUserSettings = factory(root.SmartQueryRouterPrivacyConfig);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (privacyConfigModule) {
  'use strict';

  const privacyModule = privacyConfigModule || (typeof globalThis !== 'undefined' && globalThis.SmartQueryRouterPrivacyConfig) || null;

  /**
   * User routing override values
   */
  const UserRoutingOverride = Object.freeze({
    AUTOMATIC: 'automatic',
    PREFER_SIMPLE: 'prefer-simple',
    PREFER_STRONG: 'prefer-strong'
  });

  const DEFAULT_PRIVACY = (privacyModule && privacyModule.DEFAULT_PRIVACY_CONFIG) ? privacyModule.DEFAULT_PRIVACY_CONFIG : Object.freeze({
    version: '1.0.0',
    telemetry: Object.freeze({
      enabled: true,
      performanceMetrics: true,
      errorMetrics: true,
      featureMetrics: true,
      allowRawConversationText: false
    }),
    diagnostics: Object.freeze({
      enabled: true,
      logQueryDetection: true,
      logRoutingDecisions: true,
      logFailures: true,
      consoleOutput: false,
      retentionTtlMs: 300_000,
      maxEntries: 50
    }),
    optimization: Object.freeze({
      localRules: true,
      promptNormalization: true,
      contextPruning: true,
      backendRouting: true,
      allowUiSubstitution: true
    }),
    retention: Object.freeze({
      maxTurnHistory: 4,
      turnSnippetMaxChars: 300,
      turnRetentionTtlMs: 900_000,
      transientEventTtlMs: 60_000
    })
  });

  /**
   * Default user settings configuration
   */
  const DEFAULT_USER_SETTINGS = Object.freeze({
    version: '1.0.0',
    routingOverride: UserRoutingOverride.AUTOMATIC,
    dryRunMode: false,
    backendEnabled: true,
    optimizationEnabled: true,
    feedbackUiEnabled: true,
    developerDiagnosticsEnabled: false,
    privacy: DEFAULT_PRIVACY
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

    if (settings.dryRunMode !== undefined && typeof settings.dryRunMode !== 'boolean') {
      return { valid: false, error: 'dryRunMode must be a boolean if provided' };
    }

    if (settings.backendEnabled !== undefined && typeof settings.backendEnabled !== 'boolean') {
      return { valid: false, error: 'backendEnabled must be a boolean if provided' };
    }

    if (settings.optimizationEnabled !== undefined && typeof settings.optimizationEnabled !== 'boolean') {
      return { valid: false, error: 'optimizationEnabled must be a boolean if provided' };
    }

    if (settings.feedbackUiEnabled !== undefined && typeof settings.feedbackUiEnabled !== 'boolean') {
      return { valid: false, error: 'feedbackUiEnabled must be a boolean if provided' };
    }

    if (settings.developerDiagnosticsEnabled !== undefined && typeof settings.developerDiagnosticsEnabled !== 'boolean') {
      return { valid: false, error: 'developerDiagnosticsEnabled must be a boolean if provided' };
    }

    if (settings.privacy !== undefined) {
      if (privacyModule && typeof privacyModule.validatePrivacyConfig === 'function') {
        const pVal = privacyModule.validatePrivacyConfig(settings.privacy);
        if (!pVal.valid) {
          return { valid: false, error: `Invalid privacy settings: ${pVal.error}` };
        }
      } else if (typeof settings.privacy !== 'object' || settings.privacy === null || Array.isArray(settings.privacy)) {
        return { valid: false, error: 'privacy must be an object' };
      }
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
        : DEFAULT_USER_SETTINGS.routingOverride,
      dryRunMode: typeof overrides.dryRunMode === 'boolean'
        ? overrides.dryRunMode
        : (DEFAULT_USER_SETTINGS.dryRunMode || false),
      backendEnabled: typeof overrides.backendEnabled === 'boolean'
        ? overrides.backendEnabled
        : (DEFAULT_USER_SETTINGS.backendEnabled !== undefined ? DEFAULT_USER_SETTINGS.backendEnabled : true),
      optimizationEnabled: typeof overrides.optimizationEnabled === 'boolean'
        ? overrides.optimizationEnabled
        : (DEFAULT_USER_SETTINGS.optimizationEnabled !== undefined ? DEFAULT_USER_SETTINGS.optimizationEnabled : true),
      feedbackUiEnabled: typeof overrides.feedbackUiEnabled === 'boolean'
        ? overrides.feedbackUiEnabled
        : (DEFAULT_USER_SETTINGS.feedbackUiEnabled !== undefined ? DEFAULT_USER_SETTINGS.feedbackUiEnabled : true),
      developerDiagnosticsEnabled: typeof overrides.developerDiagnosticsEnabled === 'boolean'
        ? overrides.developerDiagnosticsEnabled
        : (DEFAULT_USER_SETTINGS.developerDiagnosticsEnabled !== undefined ? DEFAULT_USER_SETTINGS.developerDiagnosticsEnabled : false),
      privacy: (privacyModule && typeof privacyModule.createPrivacyConfig === 'function')
        ? privacyModule.createPrivacyConfig(overrides.privacy || {})
        : (overrides.privacy && typeof overrides.privacy === 'object' ? Object.freeze({ ...DEFAULT_PRIVACY, ...overrides.privacy }) : DEFAULT_PRIVACY)
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
     * Returns whether dry-run mode is currently enabled (defaults to false)
     * @returns {boolean}
     */
    isDryRunMode() {
      return Boolean(this.currentSettings.dryRunMode);
    }

    /**
     * Toggle or set dry-run mode specifically
     * @param {boolean} enabled
     * @returns {Promise<object>}
     */
    async setDryRunMode(enabled) {
      return this.updateSettings({ dryRunMode: Boolean(enabled) });
    }

    /**
     * Returns whether backend optimization calls are enabled (defaults to true)
     * @returns {boolean}
     */
    isBackendEnabled() {
      return this.currentSettings.backendEnabled !== false;
    }

    /**
     * Toggle or set backend optimization calls specifically
     * @param {boolean} enabled
     * @returns {Promise<object>}
     */
    async setBackendEnabled(enabled) {
      return this.updateSettings({ backendEnabled: Boolean(enabled) });
    }

    /**
     * Returns whether active prompt optimization is enabled (defaults to true)
     * @returns {boolean}
     */
    isOptimizationEnabled() {
      return this.currentSettings.optimizationEnabled !== false;
    }

    /**
     * Toggle or set prompt optimization specifically
     * @param {boolean} enabled
     * @returns {Promise<object>}
     */
    async setOptimizationEnabled(enabled) {
      return this.updateSettings({ optimizationEnabled: Boolean(enabled) });
    }

    /**
     * Returns whether the optional feedback UI is enabled (defaults to true)
     * @returns {boolean}
     */
    isFeedbackUiEnabled() {
      return this.currentSettings.feedbackUiEnabled !== false;
    }

    /**
     * Toggle or set the optional feedback UI specifically
     * @param {boolean} enabled
     * @returns {Promise<object>}
     */
    async setFeedbackUiEnabled(enabled) {
      return this.updateSettings({ feedbackUiEnabled: Boolean(enabled) });
    }

    /**
     * Returns whether developer diagnostics view is enabled (defaults to false)
     * @returns {boolean}
     */
    isDeveloperDiagnosticsEnabled() {
      return Boolean(this.currentSettings.developerDiagnosticsEnabled);
    }

    /**
     * Toggle or set developer diagnostics view specifically
     * @param {boolean} enabled
     * @returns {Promise<object>}
     */
    async setDeveloperDiagnosticsEnabled(enabled) {
      return this.updateSettings({ developerDiagnosticsEnabled: Boolean(enabled) });
    }

    /**
     * Returns the active privacy configuration snapshot
     * @returns {object}
     */
    getPrivacyConfig() {
      return this.currentSettings.privacy || DEFAULT_PRIVACY;
    }

    /**
     * Updates privacy configuration specifically
     * @param {object} partialPrivacy
     * @returns {Promise<object>}
     */
    async updatePrivacyConfig(partialPrivacy = {}) {
      const currentPrivacy = this.getPrivacyConfig();
      const updatedPrivacy = {
        ...currentPrivacy,
        ...partialPrivacy,
        telemetry: { ...(currentPrivacy.telemetry || {}), ...(partialPrivacy.telemetry || {}) },
        diagnostics: { ...(currentPrivacy.diagnostics || {}), ...(partialPrivacy.diagnostics || {}) },
        optimization: { ...(currentPrivacy.optimization || {}), ...(partialPrivacy.optimization || {}) },
        retention: { ...(currentPrivacy.retention || {}), ...(partialPrivacy.retention || {}) }
      };
      return this.updateSettings({ privacy: updatedPrivacy });
    }

    /**
     * Check if a specific telemetry category is enabled
     * @param {string} [category] - 'performanceMetrics' | 'errorMetrics' | 'featureMetrics' | 'allowRawConversationText'
     * @returns {boolean}
     */
    isTelemetryEnabled(category) {
      const p = this.getPrivacyConfig();
      if (!p || !p.telemetry || p.telemetry.enabled === false) {
        return false;
      }
      if (!category) return true;
      return p.telemetry[category] !== false;
    }

    /**
     * Check if a specific diagnostics category is enabled
     * @param {string} [category] - 'logQueryDetection' | 'logRoutingDecisions' | 'logFailures' | 'consoleOutput'
     * @returns {boolean}
     */
    isDiagnosticsEnabled(category) {
      const p = this.getPrivacyConfig();
      if (!p || !p.diagnostics || p.diagnostics.enabled === false) {
        return false;
      }
      if (!category) return true;
      return Boolean(p.diagnostics[category]);
    }

    /**
     * Check if a specific optimization category is enabled
     * @param {string} category - 'localRules' | 'promptNormalization' | 'contextPruning' | 'backendRouting' | 'allowUiSubstitution'
     * @returns {boolean}
     */
    isOptimizationCategoryEnabled(category) {
      if (!this.isOptimizationEnabled()) {
        return false;
      }
      const p = this.getPrivacyConfig();
      if (!p || !p.optimization) {
        return true;
      }
      return p.optimization[category] !== false;
    }

    /**
     * Get content retention bounds
     * @returns {object}
     */
    getRetentionConfig() {
      const p = this.getPrivacyConfig();
      return (p && p.retention) ? p.retention : DEFAULT_PRIVACY.retention;
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
