# Smart Query Router: Engineering Baseline & Operating Standards

## 1. Current State & Baseline Architecture

The Smart Query Router project is a hybrid system designed to enhance user interaction with Claude (`https://claude.ai/*`) by autonomously reducing unnecessary prompt overhead, checking query caches, and routing requests to the optimal model.

- **Status**: Greenfield initial baseline.
- **Components**:
  1. `extension/`: Chrome Extension (Manifest V3) running invisibly on `https://claude.ai/*`.
  2. `backend/`: Fast, lightweight evaluation & routing engine in Python (FastAPI).

### Host Runtime Baseline
- **Python**: 3.12.8+
- **Node.js**: v22.14.0+
- **npm**: 11.7.0+
- **Browser**: Google Chrome 153.0.8010.27+ (Manifest V3 strictly enforced)
- **Target Origin**: `https://claude.ai/*`

---

## 2. Setup, Build & Execution Guide

### 2.1 Backend Setup & Execution
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the development server:
   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
5. Health check verification:
   ```bash
   curl http://127.0.0.1:8000/health
   # Expected response: {"status": "ok"}
   ```

### 2.2 Extension Installation for Testing in Chrome
1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Toggle on **Developer mode** in the top right corner.
3. Click **Load unpacked**.
4. Select the `extension/` folder inside this repository.
5. Verification:
   - Confirm "Smart Query Router" appears in the extensions list.
   - Click the **service worker** link on the extension card to open DevTools for the background worker.
   - Verify no runtime errors are logged in the extension console.

---

## 3. Test Execution

### 3.1 Backend Tests
Run automated unit and integration tests using `pytest`:
```bash
cd backend
pytest tests/ -v
```

### 3.2 Extension Verification Harness
When browser-driven test automation or live Claude interaction is verified:
1. Open the deterministic test fixture in Chrome:
   ```
   file:///d:/Vedhanth/studies/Coding/Projects/Smart Query Router/extension/tests/mock_claude_dom.html
   ```
2. Test editor input detection, keyboard `Enter` interception, and mock backend routing.

---

## 4. Mandatory Verification Rules & Operating Policy

> [!IMPORTANT]
> **Mandatory Verification Rule**:
> Every feature change must be manually verified in the target Claude page (`claude.ai`) or in the deterministic test harness (`mock_claude_dom.html`) when browser testing is available.

### Core Architectural Guardrails
1. **Strict Fail-Open Guarantee**:
   - Under no circumstances may a backend timeout, network disconnection, or extension error block the user from submitting their prompt to Claude.
   - If the router does not respond within 120ms, the extension must immediately pass through the user prompt untouched.
2. **Zero Credential / Token / PII Leakage**:
   - Never log, store, or forward session cookies, authorization headers, CSRF tokens, or private conversational history.
   - Only log non-identifying telemetry (query character length, token estimations, route decisions, and latency).
3. **Preserve Claude Internal Integrity**:
   - Do not monkey-patch native `window.fetch` to synthesize fake SSE streams.
   - Interact with Claude strictly via supported DOM mechanisms (`contenteditable` ProseMirror editor and accessible model picker controls).
4. **Resilient Semantic Selectors**:
   - Never rely on obfuscated, dynamic CSS classes (e.g. `.css-1x9a8b`).
   - Use semantic attributes (`div[contenteditable="true"]`, `.ProseMirror`, `role="combobox"`, `role="menuitem"`, and ARIA labels).

---

## 5. Reusable Regression Checklist

Future prompts and modifications must review and execute this checklist prior to marking work complete:

- [ ] **Build & Parse Check**:
  - Does `manifest.json` validate against Manifest V3 specifications?
  - Does the backend Python app import cleanly without syntax or dependency errors?
- [ ] **Fail-Open Verification**:
  - If the backend server is stopped, does pressing `Enter` in Claude submit normally with zero latency or error modals?
- [ ] **Typing & Editing Non-Interference**:
  - Does regular typing, `Shift + Enter` (multiline insertion), and IME composition behave identically to native Claude?
  - Are React / ProseMirror internal states preserved without text duplication?
- [ ] **Semantic Model Switching**:
  - Does the model selector correctly switch between Haiku, Sonnet, and Opus only when warranted?
  - If the recommended model is already active, does the extension avoid unnecessary DOM clicks?
- [ ] **Overhead & Cache Optimization**:
  - Is prompt trimming strictly non-destructive (only removes redundant trailing/leading whitespace and boilerplate)?
  - Does the cache check execute in under 50ms locally?
- [ ] **Privacy & Logging Audit**:
  - Are terminal/console logs free of prompt text, cookies, or auth tokens?
- [ ] **Automated Test Suite**:
  - Do all existing backend and extension tests pass with 100% success?
