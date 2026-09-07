/**
 * Automated Verification Script for Smart Query Router Extension Baseline
 * Verifies Manifest V3 compliance, file structure, and syntax.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');

console.log('--- Verifying Extension Foundation ---');

// 1. Check Manifest existence and JSON validity
if (!fs.existsSync(MANIFEST_PATH)) {
  console.error('FAIL: manifest.json does not exist');
  process.exit(1);
}

let manifest;
try {
  const raw = fs.readFileSync(MANIFEST_PATH, 'utf8');
  manifest = JSON.parse(raw);
  console.log('PASS: manifest.json is valid JSON');
} catch (e) {
  console.error('FAIL: manifest.json parse error:', e.message);
  process.exit(1);
}

// 2. Validate Manifest V3 Properties
if (manifest.manifest_version !== 3) {
  console.error(`FAIL: expected manifest_version 3, found ${manifest.manifest_version}`);
  process.exit(1);
}
console.log('PASS: Manifest version is 3');

// 3. Validate minimal permissions
const expectedPermissions = ['storage'];
const permissions = manifest.permissions || [];
const hasExactPermissions = permissions.length === expectedPermissions.length &&
  permissions.every(p => expectedPermissions.includes(p));
if (!hasExactPermissions) {
  console.error('FAIL: permissions are not strictly minimal:', permissions);
  process.exit(1);
}
console.log('PASS: Permissions strictly minimal:', permissions);

// 4. Validate host permissions scope
const hostPermissions = manifest.host_permissions || [];
if (hostPermissions.length !== 1 || hostPermissions[0] !== 'https://claude.ai/*') {
  console.error('FAIL: host_permissions must be narrowly scoped to https://claude.ai/*, found:', hostPermissions);
  process.exit(1);
}
console.log('PASS: Host permissions narrowly scoped to claude.ai');

// 5. Validate background service worker
if (!manifest.background || !manifest.background.service_worker) {
  console.error('FAIL: background.service_worker missing');
  process.exit(1);
}
const swPath = path.join(EXTENSION_ROOT, manifest.background.service_worker);
if (!fs.existsSync(swPath)) {
  console.error(`FAIL: Service worker file not found at ${swPath}`);
  process.exit(1);
}
console.log(`PASS: Background service worker file exists: ${manifest.background.service_worker}`);

// 6. Validate content script declarations
if (!Array.isArray(manifest.content_scripts) || manifest.content_scripts.length === 0) {
  console.error('FAIL: content_scripts definition missing');
  process.exit(1);
}
const csDef = manifest.content_scripts[0];
if (!csDef.matches.includes('https://claude.ai/*')) {
  console.error('FAIL: content_scripts match pattern missing https://claude.ai/*');
  process.exit(1);
}
for (const jsFile of csDef.js) {
  const csPath = path.join(EXTENSION_ROOT, jsFile);
  if (!fs.existsSync(csPath)) {
    console.error(`FAIL: Content script file not found at ${csPath}`);
    process.exit(1);
  }
}
console.log('PASS: Content script targets claude.ai and script files exist');

// 7. Verify JS syntax via vm compilation
try {
  const swCode = fs.readFileSync(swPath, 'utf8');
  new vm.Script(swCode, { filename: 'service_worker.js' });
  console.log('PASS: service_worker.js compiles with zero syntax errors');
} catch (e) {
  console.error('FAIL: service_worker.js syntax error:', e.message);
  process.exit(1);
}

const healthTrackerPath = path.join(EXTENSION_ROOT, 'src/background/health_tracker.js');
try {
  const htCode = fs.readFileSync(healthTrackerPath, 'utf8');
  new vm.Script(htCode, { filename: 'health_tracker.js' });
  console.log('PASS: health_tracker.js compiles with zero syntax errors');
} catch (e) {
  console.error('FAIL: health_tracker.js syntax error:', e.message);
  process.exit(1);
}

for (const jsFile of csDef.js) {
  try {
    const filePath = path.join(EXTENSION_ROOT, jsFile);
    const code = fs.readFileSync(filePath, 'utf8');
    new vm.Script(code, { filename: jsFile });
    console.log(`PASS: ${jsFile} compiles with zero syntax errors`);
  } catch (e) {
    console.error(`FAIL: ${jsFile} syntax error:`, e.message);
    process.exit(1);
  }
}

console.log('--- ALL EXTENSION BASELINE CHECKS PASSED ---');
