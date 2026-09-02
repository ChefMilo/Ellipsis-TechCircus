# Dasfax — Recon Report

Read-only recon of `Ellipsis-TechCircus` (repo root: `C:\Users\terry\Desktop\Tech Series\Ellipsis-TechCircus`), branch `main` @ `5541991` (2026-09-02). All paths below are repo-relative; all claims are cited to a file and, where the file is short enough for a line number to mean something, a line.

---

## 1. REPO MAP

```
.
├── .gitignore
├── package.json                 # root TS/extension toolchain (esbuild, vitest, tsc)
├── package-lock.json
├── README.md                    # one line, no setup instructions
├── tsconfig.json
├── WS2.md                       # WS2 (rendering) dev notes/status doc
├── backend/                     # Python/FastAPI — Tier 3 (WS5+WS6) + the /analyze envelope
│   ├── .env.example
│   ├── README.md
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── run_demo.py              # offline demo script
│   ├── app/
│   │   ├── main.py              # FastAPI app: /health, /tier3/claims, /analyze
│   │   ├── config.py            # env-driven Settings
│   │   ├── clients/             # LLM/search/assessor Protocols + mock + real (openai, tavily)
│   │   ├── models/contract.py   # THE CONTRACT — Pydantic v2 models, source of truth
│   │   ├── pipeline/ws5.py      # run_ws5(): extract→dedup→rank→retrieve
│   │   └── services/            # anchoring, assessment (WS6), envelope, ranking, retrieval
│   └── tests/                   # pytest, offline, 6 files + fixtures
├── extension/                   # Chrome MV3 unpacked-extension output dir
│   ├── manifest.json
│   └── icons/
├── src/                         # TypeScript source the extension is built from
│   ├── background/service-worker.ts   # MV3 service worker
│   ├── content/                       # content-script code: bootstrap, analysis-client,
│   │                                    trusted-ui, anchor/, render/
│   ├── lib/                           # whitelist, hostname-match, article-heuristic,
│   │                                    article-extractor (Tier 0/1/2, client-side)
│   └── shared/                        # contract.ts (TS mirror of backend contract),
│                                        text-normalize.ts
└── test/fixtures/gnarly-article.html  # a DOM fixture for anchoring tests
```

No `node_modules/`, `.venv/`, `dist/`, or `build/` present in the working tree (confirmed via `find`).

---

## 2. STACK

**Languages:** TypeScript (extension), Python 3.11+ (backend).

**Dependency manifests found:**
- `package.json` (root) — `backend/requirements.txt` — `backend/pyproject.toml`
- No `pyproject.toml` at repo root, no `environment.yml`, no `Pipfile`, no `poetry.lock`.

**`package.json`** (`package.json:1-24`), name `ellipsis-techcircus-ws1`:
- deps: `@mozilla/readability ^0.6.0`
- devDeps: `typescript ^5.6.3`, `vitest ^2.1.4`, `esbuild ^0.24.0`, `jsdom ^29.1.1`, `@types/chrome ^0.0.270`, `@types/jsdom ^30.0.0`
- scripts: `build` (esbuild → `extension/service-worker.js` + `extension/content-script.js`), `test` (`vitest run`), `typecheck` (`tsc --noEmit`)

**`backend/requirements.txt`** (`backend/requirements.txt:1-13`):
- `fastapi>=0.110,<1.0`, `uvicorn[standard]>=0.27,<1.0`, `pydantic>=2.6,<3.0`
- `pytest>=8.0,<9.0`, `httpx>=0.27,<1.0`
- `ruff>=0.6,<1.0`
- Comment notes real providers are separate optional installs: `openai`, `tavily-python` — neither is in this file, so `pip install -r requirements.txt` alone cannot run the real (non-mock) LLM/search path.

**`backend/pyproject.toml`** (`backend/pyproject.toml:1-17`): project metadata only (`dasfax-backend`, `requires-python = ">=3.11"`) plus `pytest`/`ruff` tool config. No build backend / no packaging config — this is not currently `pip install`-able as a package.

No ML libraries (no `torch`, `transformers`, `sentence-transformers`, etc.) appear in any manifest on `main`.

---

## 3. BACKEND STATE

Yes — `backend/app/main.py` defines a FastAPI app (`app = FastAPI(...)`, `backend/app/main.py:37`).

| Method | Path | Request model | Response model | File:line | Body |
|---|---|---|---|---|---|
| GET | `/health` | none | `dict` (untyped) | `backend/app/main.py:55` | **Real implementation** — returns status + configured provider names from `get_settings()`. |
| POST | `/tier3/claims` | `ArticleInput` | `ClaimExtractionResult` | `backend/app/main.py:66` | **Real implementation** — calls `run_ws5(article)`. |
| POST | `/analyze` | `AnalysisRequest` | `AnalysisResponse` | `backend/app/main.py:84` | **Real implementation** — handles the empty-text case explicitly (`main.py:95-111`), otherwise runs `run_ws5` + `assess_claims` (WS6) and wraps via `to_analysis_response` (`main.py:113-133`). |

No other routes exist. `grep`-level check: only these three `@app.get`/`@app.post` decorators are in `backend/app/main.py`.

**Tier controller / orchestrator:** There is a pipeline entry point, `run_ws5()` in `backend/app/pipeline/ws5.py:34`, which does extract → dedup → rank → retrieve → anchor → assemble (`ws5.py:1-16` docstring, `ws5.py:49-121` body). This is **not** a tier orchestrator in the sense the task description implies (a component that decides whether to run Tier 2 vs Tier 3, or sequences Tier 0→1→2→3) — it is WS5/WS6-internal only. `backend/app/main.py:13` says explicitly: *"Tier 0/1 routing still lives in the extension; when a fuller WS3 orchestrator appears it can own `/analyze` or call `run_ws5` directly."* There is no Tier 2 code anywhere in the `backend/app/` tree on `main` (see §6) and no code that calls into a Tier 2 step from `/analyze`. **A true four-tier orchestrator does not exist on `main`.**

---

## 4. THE CONTRACT

Backend truth: `backend/app/models/contract.py` (Pydantic v2). Extension truth: `src/shared/contract.ts` (hand-maintained mirror, `contract.ts:1-12` says so explicitly — "Keep these shapes in sync by hand — there is no codegen").

**`AnalysisResponse`** (backend `contract.py:252-263`; extension `contract.ts:101-109`):

```json
{
  "schemaVersion": "1.0",
  "url": "string",
  "status": "complete | processing | failed | skipped",
  "articleVerdict": { "level": "trusted|ok|caution|high_risk|unrated", "summary": "string", "confidence": "number|null" },
  "verifiedClaims": [ { "claim": Claim, "assessment": Assessment | null } ],
  "errors": [ { "code": "string", "message": "string" } ]
}
```

Field-by-field comparison:
- `AnalysisStatus` enum: backend has `COMPLETE, PROCESSING, FAILED, SKIPPED` (`contract.py:195-201`); extension has the same 4 string literals (`contract.ts:74`). **Match.**
- `ArticleVerdictLevel`: backend `TRUSTED, OK, CAUTION, HIGH_RISK, UNRATED` (`contract.py:204-220`); extension `trusted, ok, caution, high_risk, unrated` (`contract.ts:83-88`). **Match** — this 5-value set is the result of the most recent commit (`5541991`, "added unrated category"), which touched both `backend/app/models/contract.py` and `src/shared/contract.ts` together, so the two are in sync as of `main`'s tip.
- `Claim`: backend (`contract.py:92-119`) has `id, text, claim_type, checkworthiness, rank, search_query, evidence, char_start, char_end, prefix, suffix`. Extension `Claim` (`contract.ts:33-51`) mirrors all of these. **Match.**
- `Evidence`: backend (`contract.py:78-89`) has `snippet, source_url, source_title, source_domain, published_at, relevance_score`. Extension `Evidence` (`contract.ts:25-31`) has `snippet, source_url, source_title, source_domain, relevance_score` — **`published_at` is present on the backend and absent from the extension's mirror.** Not a breaking disagreement (extension's runtime guard `isEvidence`, `contract.ts:139-141`, only checks `snippet`/`source_url`), but the mirror is not 1:1 as the comment at `contract.ts:5-7` claims for "fields WS2 actually renders."
- `Assessment` / `Citation`: backend `contract.py:153-172`; extension `contract.ts:53-65`. **Match** on shape.
- Runtime guard: extension's `isAnalysisResponse()` (`contract.ts:184-203`) structurally validates exactly the fields above; backend's `test_analyze.py:43-79` (`assert_passes_ws2_guard`) is a hand-written Python port of that same guard, used to test the backend never emits something the extension would silently drop. The two are consistent by inspection.

**Where the contract is exercised end-to-end vs. not:** `backend/tests/test_analyze.py` posts to `/analyze` and asserts the response satisfies the guard (`test_analyze.py:103-300`) — this is tested **on the backend side only, via FastAPI's in-process `TestClient`**, not over real HTTP, and not from the extension.

**Does the extension actually call `/analyze`?** `src/content/analysis-client.ts:55-78` (`requestAnalysis`) sends `runtime.sendMessage({ type: "dasfax:analyze", payload })` to the background service worker (`analysis-client.ts:66-69`) and expects a reply shaped like `AnalysisResponse`. **`src/background/service-worker.ts` (the entire file, 13 lines) registers only a `chrome.tabs.onUpdated` listener for Tier-0 hostname logging — it has no `chrome.runtime.onMessage` listener, no `fetch()` call, and no reference to `"dasfax:analyze"` at all.** Confirmed by grep across `src/` for `onMessage`, `fetch(`, `sendMessage`: the only hits are the `sendMessage` call site itself in `analysis-client.ts:62,66`. **The extension never actually reaches `POST /analyze` on `main` — there is no code path that makes the HTTP call.** `src/content/bootstrap.ts:70-76` acknowledges this directly in a comment: *"Until WS3 wires the background <-> backend path, every non-mock page hits this [error]. It's expected, not a fault."* This wiring commit does exist (`"Wire the extension to the backend: first end-to-end fact check"`, `4e11aa7`, TerrorByte) but only on branch `ws4-tier2-screening`, which is **not merged into `main`** (see §9).

---

## 5. EXTENSION STATE

`extension/manifest.json:1-34` — **yes, manifest_version 3.**

| Piece | Exists? | Real or stub | Evidence |
|---|---|---|---|
| Content script | Yes | Real | `content_scripts` in manifest points at `content-script.js` (build output of `src/content/bootstrap.ts`), `manifest.json:26-33`. |
| Background service worker | Yes | Stub / partial | `src/background/service-worker.ts:1-13` — only Tier-0 hostname logging via `chrome.tabs.onUpdated`; no message handling, no fetch, no relay to the backend (see §4). |
| Whitelist / domain check (Tier 0) | Yes | Real | `src/lib/whitelist.ts` (16 hardcoded domains) + `src/lib/hostname-match.ts:20-40` (`classifyHostname`, exact/subdomain match, anti-suffix-spoof). Used both in the service worker (`service-worker.ts:11`) and content script (`bootstrap.ts:88`). |
| Article-detection heuristic (Tier 1) | Yes | Real | `src/lib/article-heuristic.ts:92-155` (`isProbablyArticle`) — weighted scoring over `<article>` tag, paragraph count/length, text-to-markup ratio, byline/date selectors, Readability's `isProbablyReaderable`, minus penalties for product grids / video-dominant / chat UIs. Threshold `0.5` (`article-heuristic.ts:13`). |
| Article text extraction | Yes | Real | `src/lib/article-extractor.ts:173-201` (`extractArticle`) — wraps `@mozilla/readability`'s `Readability.parse()`, normalizes date to ISO 8601, filters tracking-pixel/icon images, resolves hostname. Explicitly draft/unlocked in a couple of respects per its own doc comment (`article-extractor.ts:3-24`). |
| DOM highlight injection | Yes | Real | `src/content/render/highlights.ts` — CSS Custom Highlight API primary path (`highlights.ts:48-54`, Chrome 105+), `<span>`-wrapping fallback (`highlights.ts:86-132`) for environments without it (e.g. jsdom tests). |
| Shadow DOM panel | Yes | Real | `src/content/render/shadow-root.ts` (host + shadow root + scoped CSS, `HOST_TAG = "dasfax-root"`) and `src/content/render/panel.ts` (drawer with list/detail views, focus trap, Esc-to-close). |

Also present but not asked for explicitly: `src/content/render/pill.ts` (summary pill), `src/content/render/controller.ts` (orchestrates the above + re-anchor `MutationObserver` + per-URL dismissal via `chrome.storage.session`), `src/content/trusted-ui.ts` (separate minimal "Trusted source" / "Check anyway" chip), and a bundled offline mock (`src/content/render/__mock__/mock-response.ts`, referenced by `analysis-client.ts:13`) that lets WS2 be exercised without any backend at all via `?dasfaxMock=1`.

**Note:** `manifest.json` requests only the `"tabs"` permission (`manifest.json:6`). `WS2.md:63-64` flags that per-URL dismissal persistence wants `"storage"` in the manifest and doesn't have it yet — `controller.ts:39,54` does reference `chrome.storage.session`, wrapped in try/catch so it degrades silently without the permission.

---

## 6. ML AND AI LAYER

**On `main`, there is no model loading and no local inference of any kind.** No BERT / CLIP / image-classifier code exists anywhere in `backend/app/` on this branch (confirmed by the depth-4 tree in §1 and by the branch-content diff in §9 — that code lives only on `ws4-tier2-screening`, unmerged).

What does exist on `main` is Tier 3's two swappable external-call clients, both defaulting to deterministic offline mocks:

- **LLM (claim extraction):** `backend/app/clients/base.py:37-43` defines the `LLMClient` protocol. Default: `MockLLMClient` (`backend/app/clients/mock_llm.py`) — a **regex/heuristic sentence classifier**, not a model call at all (opinion/prediction keyword lists, checkworthiness scored from number/date/%/proper-noun density; `mock_llm.py:21-76`). Real path: `OpenAILLMClient` (`backend/app/clients/openai_llm.py:36-73`) — calls OpenAI's chat completions API (`self._client.chat.completions.create(...)`, `openai_llm.py:50`) with model default `gpt-4o-mini` (`config.py:46`), only imported lazily and only when `DASFAX_LLM_PROVIDER=openai` (`factory.py:15-17`).
- **Search (evidence retrieval):** `backend/app/clients/base.py:47-53` defines `SearchClient`. Default: `MockSearchClient` (`backend/app/clients/mock_search.py`) — fabricates snippets from a fixed pool of 6 fake `*.example.org` sources, seeded by SHA-256 hash of the query for determinism (`mock_search.py:53-55, 80-103`); it also stamps stance-indicating prefixes ("Records indicate that…") purely so the mock assessor has something to key on. Real path: `TavilySearchClient` (`backend/app/clients/tavily_search.py:14-42`), only imported lazily when `DASFAX_SEARCH_PROVIDER=tavily`.
- **Assessor (WS6 verdicts):** `backend/app/clients/base.py:72-79` defines `AssessorClient`. Only one implementation exists, ever: `MockAssessorClient` (`backend/app/clients/mock_assessor.py`) — pattern-matches the mock search client's own stance prefixes and counts them (`mock_assessor.py:28-35`); its own docstring states in caps: *"THIS IS A RENDERING FIXTURE, NOT REAL ASSESSMENT... Do NOT quote its verdicts as results in the pitch"* (`mock_assessor.py:3-15`). `factory.py:31-39` raises `ValueError` for any `DASFAX_ASSESSOR_PROVIDER` other than `"mock"` — **there is no real assessor implementation on any branch shown in `main`'s history.**

**No AI-generated-image detector, no CLIP, no BERT text classifier exist on `main`.** (They exist, unmerged, on `ws4-tier2-screening` — see §9; out of scope for "current state of this checkout" but noted since the task's architecture description explicitly calls for them.)

**Credentials:** `OPENAI_API_KEY` and `TAVILY_API_KEY`, read from environment via `os.environ.get` in `backend/app/config.py:45,47`, with no other credential source (no keyring, no secrets manager, no `.env` loader library imported — `python-dotenv` is not in `requirements.txt`, so `.env` is not actually auto-loaded by this code; a user must export the vars themselves despite `backend/README.md:14-17`/`.env.example` implying a `.env` file is sufficient).

---

## 7. CONFIG, SECRETS, DEPLOY

**Every env var read in the codebase** (all in `backend/app/config.py`, via `os.environ.get`):

| Var | Default | Line |
|---|---|---|
| `DASFAX_LLM_PROVIDER` | `"mock"` | `config.py:36` |
| `DASFAX_SEARCH_PROVIDER` | `"mock"` | `config.py:37` |
| `DASFAX_ASSESSOR_PROVIDER` | `"mock"` | `config.py:38` |
| `DASFAX_MOCK_DEMO` | `False` (bool) | `config.py:42` |
| `OPENAI_API_KEY` | `None` | `config.py:45` |
| `DASFAX_OPENAI_MODEL` | `"gpt-4o-mini"` | `config.py:46` |
| `TAVILY_API_KEY` | `None` | `config.py:47` |
| `DASFAX_MAX_CLAIMS` | `5` (int) | `config.py:50` |
| `DASFAX_MIN_CHECKWORTHINESS` | `0.35` (float) | `config.py:51` |
| `DASFAX_EVIDENCE_PER_CLAIM` | `3` (int) | `config.py:52` |
| `DASFAX_DEDUP_THRESHOLD` | `0.85` (float) | `config.py:53` |
| `DASFAX_ANCHOR_MIN_SIMILARITY` | `0.5` (float) | `config.py:54` |

No env vars are read anywhere in `src/` (TypeScript has no `process.env` access — it's browser-side extension code).

**`.env.example`:** yes, `backend/.env.example` — all values commented out, describes itself as optional ("copy to `.env` and fill in ONLY if you want to switch off the mocks"). **`Dockerfile`:** not present (repo-wide glob for `Dockerfile*` returned nothing). **Compose file:** not present (`docker-compose*` glob returned nothing). There is therefore **no containerization or deploy tooling of any kind in this repo on any inspected branch.**

**Committed credentials:** none found. No `.env` file exists in the working tree (only `.env.example`), and a search of every commit across all branches (`git log --all --diff-filter=A --name-only`) for filenames matching `.env`, `secret`, `credential`, `apikey`/`api_key` returned no results. `backend/.env.example` itself contains no real values, only commented-out placeholder assignments.

---

## 8. TESTS AND CI

**Backend — pytest**, offline by design (`backend/README.md:20` says "16 tests, all offline" as of the WS5-only README, but more have been added since; current file count below).

Files in `backend/tests/`: `test_analyze.py`, `test_anchoring.py`, `test_api.py`, `test_assessment.py`, `test_demo_fixture.py`, `test_pipeline.py`, plus fixtures `demo_article.txt`, `sample_article.txt`.

By inspection:
- `test_api.py` (`/health`, `/tier3/claims` shape, blank-text rejection) — asserts against the same `main.py` routes and `contract.py` models actually present on `main`; consistent.
- `test_analyze.py` — the most load-bearing file; it hand-ports the extension's `isAnalysisResponse()` guard into Python (`assert_passes_ws2_guard`, `test_analyze.py:43-79`) and checks it field-for-field, including CORS preflight (`test_analyze.py:253-264`) and determinism (`test_analyze.py:286-290`). All fields it asserts on (`schemaVersion`, `ArticleVerdictLevel` values including `unrated`, `char_start`/`char_end`, etc.) match what `contract.py` currently defines — **looks internally consistent, no evidence of drift by inspection.**
- `test_anchoring.py`, `test_assessment.py`, `test_pipeline.py`, `test_demo_fixture.py` — each imports only modules that exist on `main` (`app.services.anchoring`, `app.services.assessment`, `app.pipeline.ws5`, `app.clients.mock_*`); imports resolve against the current tree.
- I did not execute the suite (explicitly out of scope per instructions), so this is "no red flags found by static reading," not "confirmed passing."

**Frontend — vitest.** Test files under `src/`: `content/anchor/fixtures.test.ts`, `content/anchor/match-claim.test.ts`, `content/anchor/text-index.test.ts`, `content/anchor/to-range.test.ts`, `content/render/controller.test.ts`, `content/render/panel.test.ts`, `content/trusted-ui.test.ts`, `lib/article-extractor.test.ts`, `lib/article-heuristic.test.ts`, `lib/hostname-match.test.ts`, `shared/contract.test.ts`, `shared/text-normalize.test.ts` — 12 files. `WS2.md:23` claims "44 WS2 tests" (a `it()`/`test()` count, not a file count — plausible given 12 files, not independently verified here). `package.json:9` wires `npm test` → `vitest run`; not executed per the read-only constraint.

**CI:** **Not present.** No `.github/` directory exists at all in the working tree (`.github/workflows/*` glob returned nothing), so there are no GitHub Actions workflows, and by extension no automated test/lint gate on PRs or pushes.

---

## 9. GIT ACTIVITY

Repo has 1 local branch (`main`) tracking `origin/main`, plus 6 other remote branches never merged in full. Per-branch tip:

| Branch | Last commit (date, author) | One-line summary of what it adds |
|---|---|---|
| `main` | 2026-09-02, DarrenHengLiWei, `5541991` | Full history through WS1 (extension shell/triage), WS5 (claim extraction+anchoring), WS6 (assessment), WS3 (`/analyze` envelope), and a same-day `unrated` verdict-level addition touching both `contract.py` and `contract.ts` together. **Furthest-ahead, most-integrated branch** — it's the merge target all other branches (except ws4) were squashed/merged into via PRs #1–#5. |
| `origin/ws3-analyze-endpoint` | 2026-09-01, ChefMilo, `7747e43` | Added `/analyze`. 0 commits ahead of `main` — fully absorbed into `main` (merge PR #3, `eea2ac5`). |
| `origin/ws5-claim-extraction` | 2026-09-01, ChefMilo, `2f9389e` | WS5 claim extraction/retrieval, mock-backed. 0 ahead of `main` — absorbed (PR #1, `d4c5662`). |
| `origin/ws5-claim-anchoring` | 2026-09-01, ChefMilo, `6c728d6` | WS5 char-offset anchoring + ruff-in-requirements fix. 0 ahead of `main` — absorbed (PR #2, `3a8196d`). |
| `origin/ws6-assessor` | 2026-09-01, ChefMilo, `2c226ce` | WS6 mock-first assessment + real article verdict rollup. 0 ahead of `main` — absorbed (PR #4, `b11cc5c`). |
| `origin/ws6-demo-statuses` | 2026-09-01, ChefMilo, `9936d63` | Offline demo run exercising every assessable status. 0 ahead of `main` — absorbed (PR #5, `7af2e85`), which is `main`'s common ancestor with everything below. |
| `origin/ws4-tier2-screening` | 2026-09-02, TerrorByte, `acc70d6` | **11 commits ahead of `main`, 13 behind — diverged, not merged.** Adds the actual Tier 2 layer entirely missing from `main`: `hf_text.py`/`hf_image.py`/`hf_loader.py` (HuggingFace BERT + image-CNN clients), `heuristic_screening.py`, `screening_base.py`, `text_classifier.py`, `image_detector.py`, `image_fetch.py`, `calibration.py`, `pipeline/ws4.py` + `pipeline/analyze.py` (a real tier orchestrator — `models/screening.py`, `models/analysis.py`), plus `train_ws4.py`, `ws4_bench.py`, `ws4_eval.py`, an `eval/` dataset, and — critically — the commit `4e11aa7 "Wire the extension to the backend: first end-to-end fact check"`, which is the only place `src/background/service-worker.ts` and `src/content/analysis-client.ts` were ever actually connected. None of this is on `main`. |

**Furthest ahead:** by commit count, `ws4-tier2-screening` (11 unique commits) — but it is stale relative to `main` by 13 commits (it forked before WS6/WS3 finished landing), so merging it back is a real merge, not a fast-forward. `main` itself is the furthest-ahead *integrated* line.

Commit-message color worth noting for risk assessment (§10): several `ws4-tier2-screening` messages are self-diagnosing failures, not features — `"WS4: fine-tuning results — both runs fail the one honest test"` (`3bdea92`), `"WS4: default to the heuristic — the BERT checkpoints flag 83% of real news"` (`6b4e6b8`), `"WS4: correct the model provenance — the text checkpoint is a title classifier"` (`e425d8b`).

---

## 10. GAPS AND RISKS

Ranked most-blocking first.

1. **The extension cannot call the backend at all on `main`.** `src/background/service-worker.ts` has no `chrome.runtime.onMessage` listener and no `fetch`; `analysis-client.ts:66` sends a message nothing is listening for, so every real (non-mock) page hits the catch block in `bootstrap.ts:65-83` and shows "Fact-check backend not connected." The fix (`4e11aa7`, "Wire the extension to the backend") exists but sits unmerged on `ws4-tier2-screening`, itself 13 commits behind `main`. **This is the single hardest blocker to end-to-end integration** — nothing downstream matters until this exists on `main` or is cherry-picked/re-implemented against `main`'s current contract.

2. **Tier 2 (the BERT classifier + image detector this task says you own the spine for) does not exist on `main` at all.** Every ML file (`hf_text.py`, `hf_image.py`, `text_classifier.py`, `image_detector.py`, `pipeline/ws4.py`, `pipeline/analyze.py`) lives only on `ws4-tier2-screening`. `/analyze` on `main` runs straight from raw request to Tier 3 (`main.py:84-133`) with **no Tier 2 screening step and no orchestrator** — the docstring at `main.py:13` explicitly defers "when a fuller WS3 orchestrator appears." Since your stated ownership is "the tier controller/orchestrator," this is the actual scope gap: right now there is no controller to own, just `run_ws5()` called directly from a route handler.

3. **The unmerged Tier 2 work has evidence of a not-yet-solved accuracy problem, not just missing integration.** Commit messages on `ws4-tier2-screening` are unusually candid: the fine-tuned BERT checkpoint "flags 83% of real news" (`6b4e6b8`) and both fine-tuning runs "fail the one honest test" (`3bdea92`); the branch's *own tip commit* claims to fix this ("make our fine-tuned BERT the active text model," `acc70d6`) but that's one commit past two failed attempts, unreviewed, on a stale branch. Merging Tier 2 is therefore not a mechanical rebase — the model behind it may still be bad, and you'd be wiring your orchestrator to a component whose reliability the branch's own history disputes.

4. **No CI, and the mock-vs-real seam is untested beyond unit level.** There's no `.github/workflows`, so nothing currently gates a merge on `pytest`/`vitest` passing, and the only assessor implementation that has ever existed (`MockAssessorClient`) explicitly disclaims itself as a "rendering fixture, not real assessment" (`mock_assessor.py:3-15`) with no real provider even stubbed (`factory.py:37-39` raises on anything but `"mock"`). If the hackathon demo depends on `DASFAX_LLM_PROVIDER=openai` / `DASFAX_SEARCH_PROVIDER=tavily` actually working end-to-end, that path is currently exercised by zero automated tests (all backend tests default to mock clients) and has no CI to catch a regression before the pitch.

5. **No Docker/deploy artifact exists anywhere**, despite this being explicitly in your ownership scope ("Docker"). There's no `Dockerfile`, no `docker-compose.yml`, on `main` or on any of the 6 other branches inspected. Today "deploy" means someone runs `uvicorn app.main:app --reload` locally (`backend/README.md:22`) and the extension's manifest has CORS wide open (`allow_origins=["*"]`, `main.py:49`, flagged in its own comment as "DEV ONLY... tighten before this is exposed anywhere real," `main.py:44-46`) — fine for a laptop demo, not defined for anything beyond it.

---

*Compiled by read-only inspection (Read/Grep/Glob + non-mutating `git` commands only). No files were installed, built, or executed; no code beyond `RECON.md` itself was modified, created, or deleted.*
