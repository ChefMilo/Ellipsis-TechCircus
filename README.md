# Ellipsis-TechCircus — Dasfax

A Chrome extension that fact-checks news **in the page you are already reading**, using a
four-tier cascade that spends effort only where it is warranted.

| tier | where | what it does |
|---|---|---|
| 0 | extension | hostname whitelist — trusted sources stop here, no network call |
| 1 | extension | article-shaped heuristic — feeds, apps and shops stop here |
| 2 | backend | fast ML screening (BERT + image CNN) — decides what is worth escalating |
| 3 | backend | claim extraction, evidence retrieval, assessment |

---

## Run it end to end

Two terminals.

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt                       # offline, no API keys, ~10s
pip install -r requirements-ml.txt                    # optional: the real Tier 2 models
uvicorn app.main:app --port 8000
```

`GET /health` shows which backends are live. Without `requirements-ml.txt` everything still
runs — Tier 2 falls back to offline heuristics and says so in every response.

**Extension**

```bash
npm install
npm run build          # bundles src/ -> extension/*.js
```

Then `chrome://extensions` → enable Developer mode → **Load unpacked** → select the
`extension/` folder. Open any news article that is not on the Tier 0 whitelist.

The extension talks to `http://127.0.0.1:8000` (set in `src/background/service-worker.ts`,
and it must match `host_permissions` in `extension/manifest.json`).

**Without the backend**, add `?dasfaxMock=1` to any URL to render the in-page UI against a
bundled mock.

---

## What happens on a page

```
content script  -- chrome.runtime.sendMessage --> service worker
                                                        |
                                                   POST /analyze
                                                        |
                                              Tier 2 screen (WS4)
                                              /                  \
                                    not escalated              escalated
                                    status: complete       Tier 3 claims (WS5)
                                    level: "ok"            level: caution/high_risk
                                    no claims              verifiedClaims[]
```

Most pages stop at Tier 2 — that is the point of the cascade. Every response carries a
`tier2` block saying what was decided and why.

---

## Contract

`src/shared/contract.ts` is the client mirror of the backend Pydantic models. The content
script validates every reply with `isAnalysisResponse()`, so a renamed field is a
user-visible failure rather than a type error.

[`src/shared/backend-contract.test.ts`](src/shared/backend-contract.test.ts) guards that
seam by running the guard against **real captured `/analyze` responses** in
`test/fixtures/`. Re-capture them if the envelope changes.

---

## Tests

```bash
npm test                      # 93 extension tests
cd backend && pytest -q       # 107 backend tests, offline, no weights
cd backend && pytest -m models   # 8 more, needs requirements-ml.txt
```

---

## Workstreams

| | area | status |
|---|---|---|
| WS1 | extension shell, Tier 0/1 triage, extraction | built |
| WS2 | in-page rendering, evidence panel, DOM anchoring | built |
| WS3 | backend spine, `/analyze` orchestration | **placeholder** — [backend/app/pipeline/analyze.py](backend/app/pipeline/analyze.py) works end to end but has no cache, rate limiting or retries |
| WS4 | Tier 2 screening models | built — see [backend/WS4.md](backend/WS4.md) |
| WS5 | claim extraction + evidence retrieval | built — see [backend/README.md](backend/README.md) |
| WS6 | assessment, CLIP context check, benchmark | **not started** — claims currently return `assessment: null` |
