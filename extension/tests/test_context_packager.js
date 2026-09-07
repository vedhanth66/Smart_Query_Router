/**
 * Smart Query Router - Unit Tests for Conservative Candidate Context Packager
 * 
 * Verifies:
 * 1. Conservative uncertainty fallback (strategy: FULL_WINDOW when relevance is uncertain)
 * 2. Strict non-deletion of verbose code blocks
 * 3. Strict preservation of cited material (blockquotes, source links)
 * 4. Identification and protection of explicit user constraints ("must", "only", "never")
 * 5. Extraction and preservation of named entities and tech identifiers
 * 6. High-confidence filtered relevance with reciprocal dialogue pair retention
 * 7. Confident standalone query exemption (strategy: NONE)
 * 8. Chronological reconstruction and original index preservation
 * 9. Edge case handling (empty turn buffer, empty query, null inputs)
 */

const assert = require('assert');
const packager = require('../src/shared/context_packager');
const ranker = require('../src/shared/relevance_ranker');

console.log('--- Running Conservative Candidate Context Packager Tests ---');

// Test 1: Conservative Uncertainty Fallback
console.log('Test 1: Conservative uncertainty fallback...');
const mockTurnsUncertain = [
  {
    turnId: 'turn_1',
    role: 'user',
    originalIndex: 0,
    timestamp: Date.now() - 40000,
    snippet: 'We are designing a distributed cache system using Redis clustering.',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_2',
    role: 'assistant',
    originalIndex: 1,
    timestamp: Date.now() - 30000,
    snippet: 'Redis Cluster provides data sharding across multiple nodes with automatic failover.',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_3',
    role: 'user',
    originalIndex: 2,
    timestamp: Date.now() - 20000,
    snippet: 'Can you tell me about the replication factor?',
    features: { hasCode: false, hasMath: false, hasQuestions: true }
  }
];

// Ambiguous query with medium confidence relevance
const queryUncertain = 'What about the cluster topology?';
const relevanceUncertain = {
  rankedCandidates: [
    { turnId: 'turn_2', relevanceScore: 0.35 },
    { turnId: 'turn_1', relevanceScore: 0.25 }
  ],
  contextConfidence: 'MEDIUM',
  needsContext: true,
  topScore: 0.35
};

const pkgUncertain = packager.buildCandidateContextPackage({
  queryText: queryUncertain,
  recentTurns: mockTurnsUncertain,
  contextRelevance: relevanceUncertain
});

assert.strictEqual(pkgUncertain.strategy, 'FULL_WINDOW', 'Uncertain relevance must default to FULL_WINDOW');
assert.strictEqual(pkgUncertain.conservativeFallbackApplied, true, 'Conservative fallback flag must be true');
assert.strictEqual(pkgUncertain.includedTurns.length, 3, 'All candidate turns must be retained when uncertain');
assert(pkgUncertain.decisionReason.includes('uncertain'), 'Reason must explicitly state uncertainty');
console.log('PASS: Conservative uncertainty fallback verified');

// Test 2: Code Block Preservation (Never delete solely because verbose)
console.log('Test 2: Code block preservation...');
const verboseCode = `\`\`\`python
def setup_connection_pool(host, port, min_conn=5, max_conn=20):
    # Verbose connection pool initialization logic
    pool = ThreadedConnectionPool(min_conn, max_conn, host=host, port=port)
    return pool
\`\`\``;

const mockTurnsWithCode = [
  {
    turnId: 'turn_code_user',
    role: 'user',
    originalIndex: 0,
    timestamp: Date.now() - 30000,
    snippet: 'Here is our database connection pool setup in Python:\n' + verboseCode,
    features: { hasCode: true, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_code_asst',
    role: 'assistant',
    originalIndex: 1,
    timestamp: Date.now() - 20000,
    snippet: 'Your pool setup initializes `ThreadedConnectionPool` with 5 min and 20 max connections.',
    features: { hasCode: true, hasMath: false, hasQuestions: false }
  }
];

const structuresCode = packager.inspectPreservationStructures(mockTurnsWithCode[0]);
assert.strictEqual(structuresCode.hasCode, true, 'Must detect code blocks');
assert(structuresCode.codeBlockCount >= 1, 'Must count code blocks');

const queryCode = 'How do I add health checks to this code?';
const relevanceCode = {
  rankedCandidates: [
    { turnId: 'turn_code_user', relevanceScore: 0.70 },
    { turnId: 'turn_code_asst', relevanceScore: 0.60 }
  ],
  contextConfidence: 'HIGH',
  needsContext: true,
  topScore: 0.70
};

const pkgCode = packager.buildCandidateContextPackage({
  queryText: queryCode,
  recentTurns: mockTurnsWithCode,
  contextRelevance: relevanceCode
});

assert(pkgCode.metrics.preservedCodeBlockCount >= 1, 'Metrics must record preserved code blocks');
assert(pkgCode.includedTurns[0].content.includes('def setup_connection_pool'), 'Code block must remain intact');
console.log('PASS: Code block preservation verified');

// Test 3: Cited Material Preservation
console.log('Test 3: Cited material preservation...');
const mockTurnWithCitations = {
  turnId: 'turn_cite',
  role: 'assistant',
  originalIndex: 0,
  timestamp: Date.now() - 15000,
  snippet: '> RFC 7519 defines JSON Web Tokens (JWT) as a compact, URL-safe means of representing claims.\nSource: https://tools.ietf.org/html/rfc7519\nAccording to the specification, tokens must be verified.',
  features: { hasCode: false, hasMath: false, hasQuestions: false }
};

const citations = packager.extractCitations(mockTurnWithCitations.snippet);
assert(citations.length >= 2, 'Must extract blockquote and citation markers');
const structuresCite = packager.inspectPreservationStructures(mockTurnWithCitations);
assert.strictEqual(structuresCite.hasCitations, true, 'Must flag citations');
console.log('PASS: Cited material preservation verified');

// Test 4: Explicit User Constraints Preservation
console.log('Test 4: Explicit user constraints preservation...');
const mockTurnConstraint = {
  turnId: 'turn_constraint',
  role: 'user',
  originalIndex: 0,
  timestamp: Date.now() - 25000,
  snippet: 'You must format as strict JSON. Never use third-party libraries. Output only valid arrays.',
  features: { hasCode: false, hasMath: false, hasQuestions: false }
};

const constraints = packager.extractUserConstraints(mockTurnConstraint.snippet, 'user');
assert.strictEqual(constraints.length, 1, 'Must extract all constraint lines');
assert(constraints[0].includes('must format as strict JSON'));

const mockTurnsWithConstraintAndOthers = [
  mockTurnConstraint,
  {
    turnId: 'turn_unrelated',
    role: 'assistant',
    originalIndex: 1,
    timestamp: Date.now() - 10000,
    snippet: 'Understood, I will generate JSON arrays without third-party dependencies.',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_newer',
    role: 'assistant',
    originalIndex: 2,
    timestamp: Date.now() - 5000,
    snippet: 'Here is the data.',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  }
];

const relevanceConstraint = {
  rankedCandidates: [
    { turnId: 'turn_newer', relevanceScore: 0.65 },
    { turnId: 'turn_constraint', relevanceScore: 0.15 } // Low lexical score
  ],
  contextConfidence: 'HIGH',
  needsContext: true,
  topScore: 0.65
};

const pkgConstraint = packager.buildCandidateContextPackage({
  queryText: 'Add 3 more entries to the list',
  recentTurns: mockTurnsWithConstraintAndOthers,
  contextRelevance: relevanceConstraint
});

// The user constraint turn MUST be included even if its lexical score alone was low!
const constraintIncluded = pkgConstraint.includedTurns.find((t) => t.turnId === 'turn_constraint');
assert(Boolean(constraintIncluded), 'Turn with explicit user constraint must be preserved');
assert.strictEqual(constraintIncluded.selectionReason, 'USER_CONSTRAINT_PRESERVATION');
console.log('PASS: Explicit user constraints preservation verified');

// Test 5: Named Entities and Tech Identifiers Extraction
console.log('Test 5: Named entities and tech identifiers extraction...');
const techText = 'Configuring PostgreSQL with pgBouncer and Redis for OAuth2 JWT authorization in Kubernetes.';
const entities = packager.extractNamedEntities(techText);
assert(entities.includes('PostgreSQL'), 'Must extract PostgreSQL');
assert(entities.includes('Redis'), 'Must extract Redis');
assert(entities.includes('OAuth2') || entities.includes('OAuth'), 'Must extract OAuth');
assert(entities.includes('JWT'), 'Must extract JWT');
assert(entities.includes('Kubernetes'), 'Must extract Kubernetes');
console.log('PASS: Named entities extraction verified');

// Test 6: High-Confidence Filtered Relevance & Reciprocal Pair Preservation
console.log('Test 6: High-confidence filtered relevance & reciprocal pairs...');
const mockTurnsDialogue = [
  {
    turnId: 'turn_q1',
    role: 'user',
    originalIndex: 0,
    timestamp: Date.now() - 40000,
    snippet: 'What is the default port for Redis?',
    features: { hasCode: false, hasMath: false, hasQuestions: true }
  },
  {
    turnId: 'turn_a1',
    role: 'assistant',
    originalIndex: 1,
    timestamp: Date.now() - 30000,
    snippet: 'The default port for Redis is 6379.',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  },
  {
    turnId: 'turn_q2',
    role: 'user',
    originalIndex: 2,
    timestamp: Date.now() - 20000,
    snippet: 'How do I set maxmemory in redis.conf?',
    features: { hasCode: false, hasMath: false, hasQuestions: true }
  },
  {
    turnId: 'turn_a2',
    role: 'assistant',
    originalIndex: 3,
    timestamp: Date.now() - 10000,
    snippet: 'In redis.conf, set: maxmemory 2gb',
    features: { hasCode: false, hasMath: false, hasQuestions: false }
  }
];

const relevanceHigh = {
  rankedCandidates: [
    { turnId: 'turn_a2', relevanceScore: 0.85 },
    { turnId: 'turn_q2', relevanceScore: 0.75 },
    { turnId: 'turn_a1', relevanceScore: 0.10 },
    { turnId: 'turn_q1', relevanceScore: 0.05 }
  ],
  contextConfidence: 'HIGH',
  needsContext: true,
  topScore: 0.85
};

const pkgHigh = packager.buildCandidateContextPackage({
  queryText: 'Can I increase that to 4gb?',
  recentTurns: mockTurnsDialogue,
  contextRelevance: relevanceHigh
});

assert.strictEqual(pkgHigh.strategy, 'FILTERED_RELEVANT', 'Strategy must be FILTERED_RELEVANT');
// turn_a2 and its reciprocal turn_q2 should be included
const includedIds = pkgHigh.includedTurns.map((t) => t.turnId);
assert(includedIds.includes('turn_a2'), 'Must include top candidate turn_a2');
assert(includedIds.includes('turn_q2'), 'Must include reciprocal question turn_q2');
assert(!includedIds.includes('turn_q1'), 'Off-topic turn_q1 must be filtered out');
assert.strictEqual(pkgHigh.excludedTurns.length, 2, 'Two turns should be excluded');
console.log('PASS: High-confidence filtered relevance and reciprocal pairs verified');

// Test 7: Confident Standalone Query Exemption
console.log('Test 7: Confident standalone query exemption...');
const queryStandalone = 'What is the capital of France?';
const relevanceStandalone = {
  rankedCandidates: [],
  contextConfidence: 'LOW',
  needsContext: false,
  topScore: 0.05
};
const contextDepStandalone = {
  requiresContextAnalysis: false,
  category: 'INDEPENDENT'
};

const pkgStandalone = packager.buildCandidateContextPackage({
  queryText: queryStandalone,
  recentTurns: mockTurnsDialogue,
  contextRelevance: relevanceStandalone,
  contextDependency: contextDepStandalone
});

assert.strictEqual(pkgStandalone.strategy, 'NONE', 'Standalone query must result in strategy NONE');
assert.strictEqual(pkgStandalone.includedTurns.length, 0, 'Zero turns included for standalone query');
assert.strictEqual(pkgStandalone.excludedTurns.length, 4, 'All turns recorded as excluded');
console.log('PASS: Confident standalone query exemption verified');

// Test 8: Chronological Reconstruction and Original Index Integrity
console.log('Test 8: Chronological reconstruction...');
// pkgHigh had includedTurns: turn_a2 (index 3) and turn_q2 (index 2)
assert(pkgHigh.chronologicalTurns[0].originalIndex < pkgHigh.chronologicalTurns[1].originalIndex, 'Chronological turns must be in ascending originalIndex order');
assert.strictEqual(pkgHigh.chronologicalTurns[0].turnId, 'turn_q2');
assert.strictEqual(pkgHigh.chronologicalTurns[1].turnId, 'turn_a2');
console.log('PASS: Chronological reconstruction verified');

// Test 9: Edge Cases Handling
console.log('Test 9: Edge cases handling...');
const pkgEmpty = packager.buildCandidateContextPackage({
  queryText: '',
  recentTurns: []
});
assert.strictEqual(pkgEmpty.strategy, 'NONE');
assert.strictEqual(pkgEmpty.includedTurns.length, 0);

const pkgNulls = packager.buildCandidateContextPackage({
  queryText: null,
  recentTurns: null,
  contextRelevance: null,
  contextDependency: null
});
assert.strictEqual(pkgNulls.strategy, 'NONE');
assert.strictEqual(pkgNulls.includedTurns.length, 0);
console.log('PASS: Edge cases gracefully handled');

console.log('--- ALL CONTEXT PACKAGER TESTS PASSED ---');
