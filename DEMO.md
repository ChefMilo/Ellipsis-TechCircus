# Dasfax — Demo Guide

From a clean clone to a running backend + loaded extension, offline-safe by default.
Every command below was actually run against this repo before this file was written —
see **docs/ws3/WS3-DEPLOY.md** for exactly what was verified by execution versus by reading, and
for why this branch (`ws3/deploy`) also carries the not-yet-merged WS3 orchestrator
(`backend/app/orchestrator/`) and extension message-hop (`src/background/`) work: this
guide would be lying about the product if it only described what's on `main` today.

---

## 1. Clean clone to a running backend

```bash
git clone <this repo>
cd Ellipsis-TechCircus
docker compose up --build
```

That's it. No `.env` file is required — every setting has a working default baked
into the Python code (`backend/app/config.py`, `backend/app/orchestrator/config.py`),
so this serves a fully-functional, fully-offline (mock LLM + mock search + mock
assessor), healthy backend with zero configuration. Wait for:

```
clean-clone-test-backend-1  | INFO:     Application startup complete.
```

or check health directly:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","ws":"WS5","llm_provider":"mock","search_provider":"mock"}
```

`docker compose ps` should show `(healthy)` within ~10-15 seconds of `Up`.

**To use real providers** (OpenAI-compatible LLM/assessor, Tavily search) instead of
the mocks: `cp backend/.env.example backend/.env`, fill in the keys you want, then
`docker compose up --build` again (`backend/.env` is read at container start, not
baked into the image — editing it and restarting the container is enough; no rebuild
needed unless you changed code).

**Without Docker**, the equivalent is:

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m pytest -q                                 # optional, but see for yourself: 147 tests, offline
uvicorn app.main:app --reload
```

---

## 2. Load the unpacked extension

```bash
npm ci
npm run build
```

This produces `extension/service-worker.js` and `extension/content-script.js`
(git-ignored build output — `npm run build` must be run locally; they aren't checked
in).

Then in Chrome:

1. `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. **Load unpacked** → select the `extension/` folder
4. Confirm the "Ellipsis Fact-Check Triage" card appears with no errors. If you edit
   TS source later, `npm run build` again and click the extension's reload icon on
   this page.

---

## 3. Which port the backend binds, and how to change it — both sides

**Default: `8000`**, on both the container and (via `docker-compose.yml`'s
`"8000:8000"` port mapping) the host.

**Backend side:**

- Docker: edit the host-side port in `docker-compose.yml`'s `ports:` list, e.g.
  `"9000:8000"` to keep the container internally on 8000 but expose it on the host as
  9000. `docker compose up` again to apply.
- Native `uvicorn`: `uvicorn app.main:app --reload --port 9000`.

**Extension side — must match whatever the backend now uses.** The extension does
**not** read this from `manifest.json` or a build-time constant; it's a runtime
override read fresh from `chrome.storage.local` on every request
(`src/background/config.ts`). With the extension loaded (§2), open its service
worker's own console — `chrome://extensions` → this extension → the **"service
worker"** link — and run:

```js
chrome.storage.local.set({ dasfaxBackendBaseUrl: "http://127.0.0.1:9000" })
```

No rebuild, no extension reload needed; it takes effect on the next `/analyze`
request. To go back to the default, either set it back explicitly or
`chrome.storage.local.remove(["dasfaxBackendBaseUrl"])`.

The same console also controls the request timeout (`dasfaxRequestTimeoutMs`, default
`130000` — deliberately above the backend's own 120s `DASFAX_TIER3_TIMEOUT_S` so the
backend times out first and returns a readable `tier3_timeout` in `errors[]`) and
whether the offline fixture path is used at all (`dasfaxUseFixture`, see §5, default
`false`) — same mechanism, same place.

If you change the port, remember `extension/manifest.json`'s `host_permissions` only
allow `http://127.0.0.1:8000/*` and `http://localhost:8000/*` today (§4) — a genuinely
different **host**, not just port, needs a new entry there and a rebuild of the
extension.

---

## 4. CORS

The extension's content script runs the request through its background service
worker (`src/background/backend-client.ts`), which `fetch()`es the backend from a
`chrome-extension://<the extension's own generated id>` origin — cross-origin from
the browser's point of view, same as any other site. Two independent gates apply, and
both must pass:

1. **The extension's own permission model** — `extension/manifest.json`'s
   `host_permissions` must list the backend's origin (`http://127.0.0.1:8000/*` /
   `http://localhost:8000/*` today), or Chrome blocks the `fetch()` before it leaves
   the extension, before CORS is even relevant. See `docs/ws3/WS3-MESSAGE-HOP.md` for the full
   explanation of why this is a *separate* gate from CORS, not a substitute for it.
2. **The backend's CORS policy**, which governs whether the *browser* lets the
   extension's JS read the response. Currently (`backend/app/main.py`):

   ```python
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["*"],
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```

   `allow_origins=["*"]` is what makes an unpredictable `chrome-extension://<id>`
   origin (the id is assigned per-install, per-machine, and changes for an unpacked
   extension unless you pin a `key` in the manifest) work at all without
   hardcoding it — the code's own comment flags this as **dev only**, to be tightened
   to the real published extension origin before this is exposed anywhere real.

   **The landmine to know about, not currently tripped:** `allow_origins=["*"]`
   combined with `allow_credentials=True` is rejected outright by browsers (the Fetch
   spec forbids a wildcard origin alongside credentialed requests — Chrome will fail
   the request client-side, not send a header a server could work around). This
   backend does **not** set `allow_credentials` (it's absent, so Starlette defaults it
   to `False`), and the extension's `fetch()` in `backend-client.ts` does not send
   credentials either — so this isn't a live bug today. It becomes one automatically
   the moment anyone adds `allow_credentials=True` (e.g. for a future cookie-based
   auth scheme) without *also* narrowing `allow_origins` off the wildcard — worth
   remembering before that day, not after.

---

## 5. OFFLINE MODE — demoing with no network to the LLM/search provider at all

Two independent layers of offline-safety exist; use the first one if the venue
network is untrusted or blocks outbound calls entirely.

**Layer 1 — `dasfaxUseFixture` (extension-side, needs no backend running at all).**
`src/background/config.ts`'s `dasfaxUseFixture` defaults to `false`, so a normal load
calls the real backend. Turn it **on** for a venue with no network at all — one command
in the service worker's console, no rebuild:

```js
chrome.storage.local.set({ dasfaxUseFixture: true })
```

With it on, the service worker never calls `fetch()` — `src/background/fixture.ts`
returns a hardcoded, contract-valid `AnalysisResponse` (4 claims, `assessment: null`
on all of them, `articleVerdict.level: "unrated"`) locally, including two claims with
real anchor offsets that genuinely highlight on `test/fixtures/gnarly-article.html`
(see `docs/ws3/WS3-MESSAGE-HOP.md` for the by-hand derivation and an executed test proving it).

**It is no longer the default, on purpose.** The fixture is contract-valid and renders
convincingly, so a run that silently served it is indistinguishable on screen from a
successful end-to-end run against the real pipeline — a bad thing to discover mid-demo.
Opt into it deliberately; switch back with `chrome.storage.local.set({ dasfaxUseFixture: false })`.

**Layer 2 — the backend's own mock providers (default, if you do run a real
backend).** Independent of the extension's fixture: `DASFAX_LLM_PROVIDER`,
`DASFAX_SEARCH_PROVIDER`, and `DASFAX_ASSESSOR_PROVIDER` all default to `"mock"`
(`backend/app/config.py`) and need no API key, no external network call of any kind —
the mock LLM is a local regex/heuristic classifier, the mock search fabricates
`*.example.org` snippets from a seeded hash, the mock assessor pattern-matches those
same fabricated snippets. `docker compose up` with no `backend/.env` at all (§1) is
this path. Use this layer instead of Layer 1 specifically when you want to demo the
real network hop (extension → backend → response) but still can't reach OpenAI/Tavily
at the venue.

---

## 6. Three test URLs — three different paths through Tier 0/1

Load the extension (§2) with the backend running (§1). For a result that is guaranteed
regardless of the network or provider keys, set `dasfaxUseFixture` to `true` first (§5).
Then visit each of these:

| URL | Path exercised | What you should see |
|---|---|---|
| `https://www.bbc.com/` (any page) | **Tier 0: whitelisted domain** (`src/lib/whitelist.ts` lists `bbc.com`) | A small "✓ Trusted source" chip, top-right. No fact-check pill, no backend/fixture call at all — Tier 0 short-circuits before Tier 1 ever runs. |
| `https://www.youtube.com/` | **Tier 1: not an article** (video-dominant layout, few paragraphs — `isProbablyArticle()` in `src/lib/article-heuristic.ts` scores this low) | A "Check this page anyway" chip, top-right. No automatic fact-check — clicking it is the only thing that starts one. |
| `https://en.wikipedia.org/wiki/Singapore` (or any long-form Wikipedia article) | **Tier 1 passes, not whitelisted** — the real analyze path | The bottom-right summary pill: loading spinner, then the backend's claims highlighted in the article body (or, with `dasfaxUseFixture: true`, the fixture's 4 claims with two highlighted). |

(Any non-whitelisted, paragraph-heavy article page works for the third row — Wikipedia
is just a stable, content-rich, non-whitelisted example that doesn't depend on a news
site's layout staying the same.)

---

## 7. KNOWN LIMITATIONS

Stated plainly, not buried in a code comment:

- **Tier 2 (BERT text classifier + AI-generated-image detector) is disabled by
  default, and turning it on today does not change that.** `DASFAX_TIER2_ENABLED`
  defaults to `false`, and even set to `true`, `backend/app/orchestrator/tier2.py`'s
  `screen()` is an honest no-op — there is no real Tier 2 implementation wired in on
  this branch. The only trained checkpoint that exists anywhere in this project's
  history (on the unmerged `ws4-tier2-screening` branch) **flagged 83% of real news
  articles as fake** in its own authors' benchmark (`docs/ws3/RECON.md` §10.3). This is not a
  placeholder oversight; it's a safety decision, made explicit in
  `orchestrator/config.py`'s own comment next to the default.
- **WS6 assessment (the per-claim SUPPORTED/CONTRADICTED/etc. verdict) is
  mock-backed unless real provider keys are supplied.** The default
  `DASFAX_ASSESSOR_PROVIDER=mock` pattern-matches stance prefixes the mock search
  client stamped onto its own fabricated snippets — its own module docstring says,
  in caps, *"THIS IS A RENDERING FIXTURE, NOT REAL ASSESSMENT"*. Set
  `DASFAX_ASSESSOR_PROVIDER=openai` (plus `OPENAI_API_KEY`, in `backend/.env`) for a
  real, model-backed assessment — see `backend/.env.example` for every variable this
  touches, including the Gemini/Groq base-URL swap.
- **The result cache is in-process, in-memory, no persistence.** Restarting the
  container (or, for a multi-worker deployment, hitting a different worker process)
  clears it. Not a bug — Task 5's own brief ruled out Redis/new infrastructure — but
  worth knowing before reading a "why did this re-run Tier 3" moment as a defect.
- **CORS is wide open (`allow_origins=["*"]`) by design, for now** — see §4. Fine for
  a laptop demo behind a firewall; explicitly flagged in the code as needing
  tightening before this is exposed anywhere public.
