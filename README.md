# Smart Query Router

A Chrome extension + FastAPI backend that classifies incoming Claude queries by complexity and routes them to the appropriate model tier (Simple or Complex), with local on-device handling for trivial queries (greetings, arithmetic, datetime).

---

## What It Does

| Query Type | Handled By | Example |
|---|---|---|
| Greeting / pleasantry | On-device (instant, 0 tokens) | "Hello", "Thanks" |
| Arithmetic | On-device (instant, 0 tokens) | "5 + 2 * 3" |
| Current date/time | On-device (instant, 0 tokens) | "What time is it?" |
| Simple factual question | Simple Model route | "What is the capital of France?" |
| Complex reasoning / research | Complex Model route | Multi-part academic queries |

The routing decision is shown in the extension popup. Claude's website handles the actual AI response — the extension runs invisibly in the background.

---

## Architecture

```
Browser (Claude tab)
  └── Chrome Extension (content script)
        ├── Observes submitted query
        ├── Extracts features (length, cues, task type, code, math)
        ├── If local: shows on-device answer card (zero tokens)
        ├── Classifies route: simple | complex | local
        └── Sends routing package → Service Worker → Backend

Backend (FastAPI @ localhost:8000)
  ├── Receives routing package
  ├── Applies deterministic routing rules
  ├── Returns routing decision (model tier, reason code, confidence)
  └── Routing decision shown in extension popup
```

---

## Quick Start

### 1. Install Python dependencies

```powershell
pip install -r backend/requirements.txt
```

### 2. Start the backend

**Windows (PowerShell):**
```powershell
.\start.ps1
```

**macOS / Linux:**
```bash
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend runs at: `http://127.0.0.1:8000`
API docs: `http://127.0.0.1:8000/docs`
Health check: `http://127.0.0.1:8000/health`

### 3. Load the Chrome extension

1. Open Chrome → go to `chrome://extensions`
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked**
4. Select the `extension/` folder in this project

### 4. Use it

1. Go to `https://claude.ai`
2. Start a conversation
3. Click the **Smart Query Router** icon in the toolbar to see routing decisions

---

## How to Test

### Backend tests
```powershell
python -m pytest backend/tests/ -q
# Expected: 477 passed
```

### Extension unit tests
```powershell
node extension/tests/run_all_tests.js
# Expected: ALL 38 EXTENSION TEST SUITES PASSED
```

---

## Project Structure

```
Smart Query Router/
├── backend/                    # FastAPI routing backend
│   ├── app/
│   │   ├── main.py             # API endpoints
│   │   ├── schemas/            # Pydantic request/response models
│   │   ├── gateway.py          # Model gateway (stubbed locally)
│   │   ├── evaluator/          # Response completeness evaluators
│   │   ├── cache.py            # Response cache
│   │   ├── ml/                 # ML router classifier
│   │   └── metrics.py          # Prometheus metrics
│   ├── tests/                  # 477 pytest test cases
│   └── requirements.txt
├── extension/                  # Chrome extension (Manifest V3)
│   ├── manifest.json
│   ├── src/
│   │   ├── content/            # Claude page content scripts
│   │   │   └── claude_interceptor.js   # Main query interception
│   │   ├── shared/             # Routing logic, feature extraction
│   │   ├── rules/              # Local answer rules (greeting, math, datetime)
│   │   ├── background/         # Service worker
│   │   └── popup/              # Extension popup UI
│   └── tests/                  # 38 extension test suites
├── k8s/                        # Kubernetes manifests (production)
├── monitoring/                 # Prometheus + Grafana configs
├── start.ps1                   # Windows backend launcher
└── docker-compose.yml          # Docker setup (requires Redis + Postgres)
```

---

## Routing Logic

Queries are classified using deterministic rules in this priority order:

1. **Local eligible** — greeting, arithmetic, datetime → answered on-device
2. **Complex: rich content** — attachments, images, tables
3. **Complex: code** — code fences, programming intent
4. **Complex: math** — LaTeX, math symbols
5. **Complex: reasoning** — "explain why", "prove that", "step-by-step"
6. **Complex: comparison** — "compare", "pros and cons", "vs"
7. **Complex: analysis** — "analyze", "evaluate", "audit"
8. **Complex: multi-question** — multiple structured sub-questions
9. **Complex: structured list** — 3+ item lists
10. **Simple** — everything else (factual lookups, simple questions)
11. **Needs evaluation** — ambiguous fallback

---

## Known Limitations (Local MVP)

- **No real LLM execution**: The backend classifies queries but doesn't call a separate LLM. Claude's website handles AI responses.
- **No persistence**: Metrics reset when the extension is reloaded. No database connected locally.
- **No Redis/cache**: Semantic cache is stubbed locally. Requires Redis for production.
- **Popup metrics are session-only**: Route distribution resets on page reload.

---

## Configuration

| Setting | Where | Default |
|---|---|---|
| Backend URL | Extension storage / `backend_client.js` | `http://127.0.0.1:8000/api/v1/optimize` |
| Optimizer enabled | Extension popup toggle | On |
| Routing override | Extension popup dropdown | Automatic |
| Developer diagnostics | Extension popup toggle | Off |

---

## Validation Environment

- **Browser**: Chrome 126+  
- **OS**: Windows 11  
- **Python**: 3.12  
- **Node.js**: 22.x (for running extension tests)
