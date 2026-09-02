# WS3 Deploy — Docker, Compose, .env.example, CI, DEMO.md

Branch: `ws3/deploy`. Read `RECON.md`, `WS3-CONTRACT-AUDIT.md`, `WS3-MESSAGE-HOP.md`,
`WS3-ANALYZE-ENVELOPE.md` first, as instructed. **`origin/main` had moved since
`RECON.md`** (a "real-providers" PR, #6) — verified below rather than trusted.

**This branch was built by combining the two prior unmerged workstreams** (backend
orchestrator from `ws3/analyze-envelope`, extension message-hop from
`ws3/message-hop`), not by branching plain `origin/main`. Reason: task 3 explicitly
requires `.env.example` to cover `app/orchestrator/config.py`, and task 5 requires
documenting `chrome.storage.local` overrides "per `src/background/config.ts`" — neither
file exists on `origin/main` today. A deploy guide that only described what's actually
merged would be describing a product that can't do what this one asks it to demo. Full
consolidation mechanics (stash juggling) omitted here as uninteresting; both suites were
confirmed green together before any Docker/CI work started: **147 backend (pytest) +
99 extension (vitest)**.

**Scope respected.** New: `backend/Dockerfile`, `backend/.dockerignore`,
`docker-compose.yml`, `.github/workflows/ci.yml`, `DEMO.md`. Modified:
`backend/.env.example` (rewritten to be complete — it already existed, from the
real-providers PR, but was missing 5 of 16 real vars). **No application logic was
touched** — everything below either reads existing behavior or adds infrastructure
around it.

---

## Verified by execution vs. verified by reading — read this first

Per the definition of done's own instruction. Executed, with real output captured
below:

- `docker compose build` / `docker compose up -d` / `docker compose ps` / `curl
  .../health` / `curl .../analyze` — **twice**: once in place, once from a byte-copy of
  `backend/` + `docker-compose.yml` in an unrelated temp directory (git can't do a real
  clean-clone test pre-commit — see that section below for why and what this substitute
  actually proves).
- `docker image ls` / `docker history` — real, measured image size (not estimated).
- `git diff` of `backend/requirements.txt` across the real-providers merge — real, not
  recalled from `RECON.md`.
- `grep` for every `os.environ`/`os.getenv` call across `backend/` — real, not guessed.
- `grep`/`git log --all` for credential-shaped content across all branches — real.
- The full backend pytest suite and the full extension vitest suite, both on this
  consolidated branch — real, both green (147 / 99).

Read/reasoned, not executed:

- The `.github/workflows/ci.yml` file itself was **not** run on real GitHub Actions
  infrastructure (that requires a push, which wasn't done). Its YAML was validated to
  parse; each underlying command it runs (`pip install -r requirements.txt` — done for
  real inside the Docker build; `python -m pytest -q`; `npm ci`; `npx vitest run`) was
  independently verified to work in this exact repo, in this session. The workflow
  composes known-good pieces; it was not itself executed end-to-end on a runner.
- DEMO.md's extension-loading steps (§2) were confirmed by running `npm run build` and
  by every underlying piece being separately, already proven (WS3-MESSAGE-HOP.md's own
  DoD verification for the fixture/anchoring path). Actually clicking through
  `chrome://extensions` in a real Chrome window was not re-done in this session — no
  new claim is made there beyond what WS3-MESSAGE-HOP.md already established.

---

## TASK 1 — Dockerfile

**Step done first, as instructed: what did real-providers add?**

```
$ git diff 5541991..e5e795a -- backend/requirements.txt
```

Two lines added, both commented out, both optional:

```
# openai>=1.30,<2.0
# tavily-python>=0.5,<1.0
```

Neither installs by default. `grep -rniE "torch|transformers|tensorflow|onnx|sentence.?
transformers|huggingface" backend/requirements.txt backend/app/` returns **nothing** —
confirmed across the whole `app/` tree, not just requirements.txt. Every real-provider
client (`openai_llm.py`, `openai_assessor.py`, `tavily_search.py`) imports its SDK
lazily, inside `__init__`, never at module level — confirmed by running
`tests/test_real_providers.py` (37 tests) with **neither `openai` nor `tavily` Python
package installed** (`python -c "import openai"` / `import tavily` both fail
`ModuleNotFoundError` in this environment) — all 37 pass anyway, because they inject a
stub transport rather than importing the real SDK.

**Conclusion, as the task asked for explicitly: real-providers only added API clients.**
No torch, no transformers, no model weights, no multi-GB risk. Said here, not silently
assumed.

**The Dockerfile** (`backend/Dockerfile`): two-stage (`deps` → `runtime`), pinned to
`python:3.13.3-slim-bookworm` (the exact interpreter this repo's suite has actually run
against in this session, not just pyproject.toml's floating `>=3.11`), non-root user
with a fixed UID/GID (10001, not the ambiguous default "nobody"), `COPY requirements.txt`
before `COPY app` so the (slow) dependency-install layer is cached across every
app-code-only change. Installs `requirements.txt` as-is (fastapi/uvicorn/pydantic/
pytest/httpx/ruff — a deliberate simplicity choice over inventing a second
`requirements-runtime.txt`: a parallel manifest is a real, documented drift risk
elsewhere in this project — `WS3-CONTRACT-AUDIT.md` Task C is entirely about exactly
that failure mode — and pytest/httpx/ruff add tens of MB, not gigabytes; see the
measured size below).

**Measured image size** (real `docker` output, not estimated):

```
$ docker images dasfax-backend:local
IMAGE                  ID             DISK USAGE   CONTENT SIZE
dasfax-backend:local   fa9895249296        292MB         71.5MB

$ docker history dasfax-backend:local --format "{{.Size}}\t{{.CreatedBy}}"
85.2MB   # debian.sh --arch 'amd64' ... 'bookworm' ...      <- base OS layer
10.4MB   RUN apt-get install ca-certificates netbase tzdata
41.1MB   RUN ... build CPython 3.13.3 from source ...
83.3MB   COPY /install /usr/local                            <- our pip install
 430kB   COPY app ./app
  16kB   COPY README.md ./
 438kB   RUN chown -R dasfax:dasfax /app
   0B    USER / EXPOSE / CMD
```

~220MB of actual layers; Docker's own summary reports 292MB total disk usage. **Well
under a GB, nowhere near multi-GB** — the outcome the "report the impact, don't silently
produce a multi-GB image" instruction was guarding against doesn't apply here, and now
that's a measured fact, not an assumption.

---

## TASK 2 — docker-compose.yml

`env_file: [{path: ./backend/.env, required: false}]` — env passed in, never baked;
the `required: false` (Compose's long-form env_file syntax) means a clean clone with no
`backend/.env` at all still starts successfully, on the code's own built-in defaults
(confirmed — see DoD below). `environment:` sets exactly one compose-level knob
(`PYTHONUNBUFFERED=1`, so `docker compose logs` isn't buffered) — not an app setting,
so it stays separate from the `.env` mechanism on purpose.

**Healthcheck:** Python's own `urllib.request`, not `curl` — `python:*-slim` images
don't ship `curl` by default and installing it just for a healthcheck would be an
avoidable few MB added back after all the size-consciousness in Task 1. Same
interpreter already in the image, zero extra packages.

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 10s
```

Verified reaching `(healthy)` in ~10-15s in both real runs (below).

---

## TASK 3 — .env.example

**Every var, found by grepping, not guessing:**

```
$ grep -rn "os\.environ\|os\.getenv" backend/
backend/app/config.py               (9 os.environ.get call sites)
backend/app/orchestrator/config.py  (2 call sites, feeding 3 vars)
```

Nothing else in `backend/` reads an env var directly — confirmed the same grep covers
`app/clients/` too (the real-provider clients read `settings.openai_api_key` etc.,
never `os.environ` themselves).

| # | Var | File | Default |
|---|---|---|---|
| 1 | `DASFAX_LLM_PROVIDER` | `app/config.py` | `mock` |
| 2 | `DASFAX_SEARCH_PROVIDER` | `app/config.py` | `mock` |
| 3 | `DASFAX_ASSESSOR_PROVIDER` | `app/config.py` | `mock` |
| 4 | `DASFAX_MOCK_DEMO` | `app/config.py` | off |
| 5 | `OPENAI_API_KEY` | `app/config.py` | unset |
| 6 | `DASFAX_OPENAI_BASE_URL` | `app/config.py` | `https://api.openai.com/v1` |
| 7 | `DASFAX_OPENAI_MODEL` | `app/config.py` | `gpt-4o-mini` |
| 8 | `TAVILY_API_KEY` | `app/config.py` | unset |
| 9 | `DASFAX_MAX_CLAIMS` | `app/config.py` | `5` |
| 10 | `DASFAX_MIN_CHECKWORTHINESS` | `app/config.py` | `0.35` |
| 11 | `DASFAX_EVIDENCE_PER_CLAIM` | `app/config.py` | `3` |
| 12 | `DASFAX_DEDUP_THRESHOLD` | `app/config.py` | `0.85` |
| 13 | `DASFAX_ANCHOR_MIN_SIMILARITY` | `app/config.py` | `0.5` |
| 14 | `DASFAX_TIER2_ENABLED` | `app/orchestrator/config.py` | off |
| 15 | `DASFAX_TIER3_TIMEOUT_S` | `app/orchestrator/config.py` | `8.0` |
| 16 | `DASFAX_CACHE_TTL_S` | `app/orchestrator/config.py` | `300.0` |

The checked-in `backend/.env.example` (from real-providers) was missing #4, #6 (partially — had the key/model but not the base-URL var name check), #13, and obviously #14-16 (didn't exist yet). Rewritten to list all 16, one comment each, grouped by the two files that own them (WS5/WS6 vs WS3), each comment naming the file it reads from and, for the orchestrator vars, citing why the Tier 2 default is off.

**No real credentials anywhere in git history — checked across every branch, not just
`main`:**

```
$ git log --all --diff-filter=A --name-only --format='%H %s' \
    | grep -iE '\.env$|secret|credential|apikey|api_key'
(no output)

$ git log --all -p -- backend/.env.example backend/app/config.py \
    backend/app/orchestrator/config.py backend/app/clients/ \
    | grep -iE 'sk-[a-zA-Z0-9]{10,}|tvly-[a-zA-Z0-9]{10,}|AIza[a-zA-Z0-9_-]{20,}'
(no output)
```

Both empty. Clean.

---

## TASK 4 — CI workflow

`.github/workflows/ci.yml`: two independent jobs, `backend-tests` (`pip install -r
requirements.txt` then `python -m pytest -q`) and `extension-tests` (`npm ci` then
`npx vitest run`). **Nothing else** — no lint/typecheck step (I drafted one with `npm
run typecheck`, then removed it: the task's own words are "Nothing else — no deploy
step," and a job list that was handed exactly two commands per suite is the more
faithful reading, even though a typecheck step would be cheap and arguably useful —
noting the temptation and the reason it was cut, rather than silently either doing it
or not). No secrets required (`test_real_providers.py`'s 37 tests already confirmed to
pass with neither `openai` nor `tavily` installed and no key set — see Task 1).

---

## TASK 5 — DEMO.md

See `DEMO.md`. Section-by-section notes not obvious from the file itself:

- **§1 (clean clone → running stack):** the literal `docker compose up --build`
  sequence, run for real twice (see below) — once in-place, once from a byte-copy of
  `backend/` + `docker-compose.yml` into an unrelated temp directory to approximate a
  clean checkout (nothing on this branch is committed yet, so a literal `git clone`
  would produce an empty tree — this is the closest honest substitute available without
  committing on your behalf, and it does catch what a clean clone would: whether the
  compose file's relative paths and the Dockerfile's COPY paths are self-contained,
  independent of this specific working directory).
- **§3 (port, both sides):** the extension half is a direct citation of
  `src/background/config.ts`'s `chrome.storage.local` mechanism (`WS3-MESSAGE-HOP.md`
  §2), not new research.
- **§4 (CORS):** quotes `backend/app/main.py`'s actual current CORS middleware call
  verbatim (`allow_origins=["*"]`, no `allow_credentials`) rather than describing it
  from memory, and states plainly that the wildcard-plus-credentials landmine is not
  currently tripped (Starlette defaults `allow_credentials` to `False` when omitted,
  confirmed against the actual call site) — flagged as a future risk, not a present bug,
  because it isn't one today.
- **§5 (offline mode):** two layers, both already the default (`dasfaxUseFixture: true`
  on the extension, `DASFAX_*_PROVIDER=mock` on the backend) — the demo doesn't need to
  turn anything on, only knows how to *keep* it on / confirm it's on if someone else's
  earlier session changed it.
- **§6 (three URLs):** `bbc.com` (in `src/lib/whitelist.ts`, verbatim), `youtube.com`
  (video-dominant layout, scores low on `isProbablyArticle()`'s own stated signals —
  read from `article-heuristic.ts`, not executed against the live site in this
  session), and a Wikipedia article (long-form, not whitelisted, stable) for the third
  path — chosen for being unlikely to change shape before a demo, not verified live in
  a browser in this session (flagged, not hidden).

---

## Definition of done — the actual runs

**Run 1, in place** (repo root, this branch's working tree):

```
$ docker compose up -d --build
 ... Image dasfax-backend:local Built
 ... Container ellipsis-techcircus-backend-1 Started

$ docker compose ps
NAME                            STATUS
ellipsis-techcircus-backend-1   Up 17 seconds (healthy)

$ curl -s http://127.0.0.1:8000/health
{"status":"ok","ws":"WS5","llm_provider":"mock","search_provider":"mock"}

$ curl -s -X POST http://127.0.0.1:8000/analyze -H "Content-Type: application/json" -d '{"url":"https://news.example.org/x"}'
{"schemaVersion":"1.0", ... "status":"complete","articleVerdict":{"level":"unrated", ...

$ ls backend/.env
ls: cannot access 'backend/.env': No such file or directory   <- confirmed NOT required
```

**Run 2, from a byte-copy in an unrelated directory** (the clean-clone approximation —
see above for why a literal `git clone` wasn't possible pre-commit):

```
$ docker compose up -d --build      # ran from the copy's own directory
 ... (deps layer CACHED -- proves the layer-cache actually works, not just structurally)
 ... Container clean-clone-test-backend-1 Started
 ... healthy within ~5s

$ curl -s http://127.0.0.1:8000/health
{"status":"ok", ...}

$ curl -s -X POST http://127.0.0.1:8000/analyze -d '{"url":"https://x.example.org","text":"Singapore recorded 3,363 cases of scams in 2025, according to police."}'
{"schemaVersion":"1.0", ..., "status":"complete", "articleVerdict":{"level":"ok", ...},
 "verifiedClaims":[{"claim":{"id":"c1", ...}, "assessment":{"status":"supported", ...}}], "errors":[]}
```

Both torn down (`docker compose down`) after verification; no dangling containers.

**Backend + extension suites, on this exact consolidated branch:**

```
$ cd backend && python -m pytest -q
147 passed in 1.3s

$ npx vitest run
Test Files  13 passed (13)
     Tests  99 passed (99)
```

**Not run this session:** the actual `.github/workflows/ci.yml` on GitHub's own
runners (would require a push — not done without being asked); a real Chrome browser
click-through of the three test URLs in §6 (each underlying piece — Tier 0 whitelist
match, Tier 1 heuristic scoring, the fixture path — was independently verified
already, in this session or `WS3-MESSAGE-HOP.md`'s, but not re-clicked here).
