const fs = require('fs');
const path = require('path');

const datasetPath = path.join(__dirname, '..', 'backend', 'app', 'dataset', 'external', 'greetings_dataset.json');
const rawData = JSON.parse(fs.readFileSync(datasetPath, 'utf8'));

// Negative lookahead so conversational pleasantries like "what's up", "what's new", "how do you do" are not blocked,
// but substantive queries like "what is", "why", "how to" ARE blocked.
const SUBSTANTIVE_INTENT_PATTERN = /\b(write|explain|create|code|tell|show|give|why|where|when|who|which|how to|can you|could you|would you|will you|fix|debug|find|search|is it|are you able|do you(?!\s+do\b)|please|make|generate|build|solve|what\s+(?!up\b|new\b|s\s+up\b|s\s+new\b|s\s+happening\b|s\s+the\s+latest\b|s\s+been\s+going\b))\b/i;

const CASUAL_GREETINGS = [
  'hi', 'hello', 'hey', 'hiya', 'heya', 'howdy', 'yo',
  'good morning', 'good afternoon', 'good evening', 'good day', 'greetings',
  'greetings and salutations'
];

const PLEASANTRIES = [
  'how are you', 'how are you doing', 'how is it going', "how's it going", "hows it going",
  'how do you do', "what's up", 'what is up', 'whats up', "what's new", 'what is new',
  'how are things', "how's life", 'how have you been', 'hope you are well',
  "hope you're well", 'hope you are doing well', "hope you're doing well",
  'nice to meet you', 'good to see you', 'good to connect', 'long time no see',
  'long time no chat', 'it has been a while', "it's been a while", 'everything okay',
  'everything going well', 'how is everything', "how's everything",
  'good to see you again', 'nice to see you again', 'glad to see you',
  'how is your day', "how's your day", 'how is your evening', "how's your evening",
  'hope all is well', 'hope everything is okay', "hope everything's okay",
  "what's happening", "what's the latest", "what's up with you", "how are things with you",
  "how's everything going", "how's life treating you", "what's on your mind", "what's on your mind today",
  "what's been going on", "what's new today", "it's good to connect", "good to connect again"
];

const ADDRESSES = ['there', 'claude', 'friend', 'bot', 'partner', 'sunshine', 'stranger'];

function normalize(text) {
  return text.trim().toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[!?,;:\.\(\)]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function testClassify(trimmed) {
  if (trimmed.length > 55) return false;
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length > 8) return false;

  if (SUBSTANTIVE_INTENT_PATTERN.test(trimmed)) return false;

  const norm = normalize(trimmed);

  // 1. Direct match
  if (CASUAL_GREETINGS.includes(norm) || PLEASANTRIES.includes(norm)) return true;

  // 2. Greeting + allowed address
  for (const g of CASUAL_GREETINGS) {
    if (ADDRESSES.some(a => norm === g + ' ' + a)) return true;
  }

  // 3. Compound matches:
  // e.g. "hi how are you", "good morning how are you", "hey good morning how are you"
  for (const g of CASUAL_GREETINGS) {
    for (const p of PLEASANTRIES) {
      if (norm === g + ' ' + p || norm === g + ' there ' + p || norm === p + ' ' + g) return true;
    }
  }

  // Double greeting + pleasantry: "hey good morning how are you"
  for (const g1 of ['hey', 'hello', 'hi']) {
    for (const g2 of ['good morning', 'good afternoon', 'good evening']) {
      for (const p of PLEASANTRIES) {
        if (norm === g1 + ' ' + g2 + ' ' + p) return true;
      }
      if (norm === g1 + ' ' + g2) return true;
    }
  }

  return false;
}

const seen = new Set();
const unique = rawData.filter(i => {
  const c = i.context.trim();
  if (seen.has(c)) return false;
  seen.add(c);
  return true;
});

let matched = 0;
let missed = [];
for (const item of unique) {
  if (testClassify(item.context)) {
    matched++;
  } else {
    missed.push(item.context);
  }
}

console.log('Tested against Kaggle dataset:');
console.log('Matched:', matched, '/', unique.length, '(' + ((matched / unique.length) * 100).toFixed(1) + '%)');
console.log('\nRemaining missed (' + missed.length + '):');
missed.forEach(m => console.log(' - ' + m));

// Substantive queries safety check
const substantive = [
  'Hello, can you help me refactor this Python function?',
  'Hi, what is the capital of France?',
  'Hey Claude, write a bash script to backup my directory',
  'Show me a hello world example in Rust',
  'Explain how "good morning" is translated into Japanese',
  'How are you able to process visual images in multimodality?',
  'Thank you for the advice, now please generate the unit tests',
  'Hello! Please tell me why the sky is blue',
  'Good morning routine for high productivity and focus'
];

let falsePositives = 0;
for (const s of substantive) {
  if (testClassify(s)) {
    console.error('FALSE POSITIVE:', s);
    falsePositives++;
  }
}
console.log('Substantive safety test false positives:', falsePositives);
