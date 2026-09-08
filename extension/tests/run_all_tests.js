/**
 * Master Test Runner & Build Integrity Verification for Smart Query Router Extension.
 *
 * Enforces:
 * 1. Manifest V3 & Build Integrity verification (verify_extension.js)
 * 2. Sequential execution of all 34 extension unit test suites
 * 3. Strict fail-fast semantics: aborts immediately on the first broken test
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const TESTS_DIR = __dirname;
const VERIFY_SCRIPT = path.join(TESTS_DIR, 'verify_extension.js');

console.log('======================================================================');
console.log('EXTENSION CI SUITE: Build Integrity & Automated Tests');
console.log('======================================================================');

const startTime = Date.now();

// 1. Run Foundation & Manifest Verification
console.log('\n[Stage 1/2] Verifying Manifest V3 & Extension Code Compilation...');
const verifyResult = spawnSync(process.execPath, [VERIFY_SCRIPT], {
  cwd: path.resolve(__dirname, '..'),
  stdio: 'inherit'
});

if (verifyResult.status !== 0) {
  console.error('\n[FAIL-FAST] Extension build integrity check failed! Halting CI immediately.');
  process.exit(1);
}
console.log('--> Extension build integrity verified successfully.\n');

// 2. Discover all unit test suites
const testFiles = fs.readdirSync(TESTS_DIR)
  .filter(f => f.startsWith('test_') && f.endsWith('.js'))
  .sort();

console.log(`[Stage 2/2] Running ${testFiles.length} Extension Unit Test Suites...`);
console.log('----------------------------------------------------------------------');

let passedCount = 0;
let failedCount = 0;

for (let i = 0; i < testFiles.length; i++) {
  const file = testFiles[i];
  const testPath = path.join(TESTS_DIR, file);
  const suiteStartTime = Date.now();
  
  process.stdout.write(`[${i + 1}/${testFiles.length}] Running ${file}... `);

  const result = spawnSync(process.execPath, [testPath], {
    cwd: path.resolve(__dirname, '..'),
    encoding: 'utf8'
  });

  const durationMs = Date.now() - suiteStartTime;

  if (result.status === 0) {
    passedCount++;
    console.log(`PASSED (${durationMs}ms)`);
  } else {
    failedCount++;
    console.log(`FAILED (${durationMs}ms)`);
    console.error('\n' + '='.repeat(70));
    console.error(`FAILURE DETAILS: ${file}`);
    console.error('='.repeat(70));
    if (result.stdout) console.log(result.stdout);
    if (result.stderr) console.error(result.stderr);
    console.error('='.repeat(70));
    console.error(`\n[FAIL-FAST] Test suite ${file} failed! Halting extension CI immediately.`);
    process.exit(1);
  }
}

const totalDurationSec = ((Date.now() - startTime) / 1000).toFixed(2);
console.log('----------------------------------------------------------------------');
console.log(`ALL ${passedCount} EXTENSION TEST SUITES PASSED in ${totalDurationSec}s`);
console.log('======================================================================\n');
process.exit(0);
