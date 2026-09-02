# WS3 Message Hop — background listener, backend client, fixture

Branch: `ws3/message-hop`. Read `RECON.md` and `WS3-CONTRACT-AUDIT.md` first — this
picks up directly from RECON.md §10.1 ("the extension cannot call the backend at all
on `main`") and WS3-CONTRACT-AUDIT.md's guard analysis, which this now actually wires
up and defends with, respectively.

**Scope respected.** Modified: `src/background/service-worker.ts`, `extension/manifest.json`.
New: `src/background/config.ts`, `src/background/backend-client.ts`,
`src/background/fixture.ts`, `src/background/fixture.test.ts`. **Not touched:**
`bootstrap.ts`, the extractor (`article-extractor.ts`), the renderer/panel
(`controller.ts`, `panel.ts`, `pill.ts`, `highlights.ts`, `shadow-root.ts`,
`status-config.ts`, `dom.ts`), or anything under `src/shared/` — `contract.ts` and
`analysis-client.ts` were only *imported from*, never edited.

Verified (all executed, not just read): `npm run typecheck` — clean. `npx vitest run` —
**99/99 passing** across 13 files (was 91/12 before this branch; +8 from the new
`fixture.test.ts`). `npm run build` — bundles cleanly, `extension/service-worker.js`
grew from a trivial Tier-0-only file to 9.9kb.

---

## 1. The listener (`service-worker.ts`)

`chrome.runtime.onMessage.addListener` is registered as a **plain synchronous
function**, exactly as instructed, with the reasoning inlined as a comment right above
it (`service-worker.ts:63-73`) so it survives a future "tidy this up into async" pass.
Mechanically: validate the message is ours and has a `url` → if not, either ignore it
(`return false`) or answer synchronously with a malformed-envelope stub (also
`return false` — no async work started, nothing to keep the channel open for) → else
kick off `handleAnalyze(payload)` (never awaited inside the listener itself), call
`sendResponse` from inside its `.then()`, and **return `true` synchronously** to hold
the message channel open for that later call.

## 2. The backend client (`backend-client.ts`) + config (`config.ts`)

`analyzeViaBackend()` does exactly what was asked: base URL from `getBackendConfig()`
(one config module, default `http://127.0.0.1:8000`), `POST {baseUrl}/analyze`,
`AbortController` + `setTimeout(..., config.timeoutMs)` (default 15000ms), and on
non-2xx / thrown network error / abort-from-timeout, it **returns** (never throws) a
`failureEnvelope()` — `status: "failed"`, `articleVerdict.level: "unrated"`,
`verifiedClaims: []`, one `AnalysisError` in `errors` whose `code` distinguishes
`backend_http_error` / `backend_timeout` / `backend_unreachable`.

**"Overridable without editing code"** is implemented via `chrome.storage.local`, not
a source-level constant — from the service worker's own DevTools console
(`chrome://extensions` → this extension → "service worker" link):

```js
chrome.storage.local.set({ dasfaxBackendBaseUrl: "http://localhost:9000" })
chrome.storage.local.set({ dasfaxRequestTimeoutMs: 30000 })
chrome.storage.local.set({ dasfaxUseFixture: false })
```

No rebuild, no reload of the unpacked extension required, and the override survives
the service worker being killed and restarted (unlike a variable set on the worker's
global scope via the console, which is wiped the next time MV3 tears the worker down —
this is *why* it's storage-backed rather than an in-memory override). This is also why
I added `"storage"` to `manifest.json`'s `permissions` (§3 below covers why a
permission was needed at all, for `fetch` specifically).

`getBackendConfig()` reads storage **fresh on every call**, never caches at module
scope — see §5.

## 3. `host_permissions`, and why it's required

Added to `extension/manifest.json`:

```json
"host_permissions": [
  "http://127.0.0.1:8000/*",
  "http://localhost:8000/*"
]
```

**Why this is required, not just polite:** MV3 does not let an extension `fetch()` an
arbitrary origin just because the code calling `fetch` happens to be an extension.
Cross-origin requests from extension contexts (service worker included) are gated by
the `host_permissions` the manifest declares — this is enforced independently of, and
in addition to, any CORS headers the *target* server sends. `backend/app/main.py:47-52`
already sets `allow_origins=["*"]` for CORS (per its own comment, so the browser
doesn't drop the *response* before Terry's code sees it) — but CORS only governs
whether the response is exposed to the page once the browser has already decided to
send the request in the first place. Without a matching `host_permissions` entry, the
`fetch()` call in `backend-client.ts` is blocked by Chrome's extension permission
model before it ever leaves the service worker, regardless of what the server would
have allowed — no network trace, no CORS error, just a rejected promise. This is a
different, extension-specific security boundary layered *underneath* CORS, and it's
also why plain `"tabs"` (the only permission `main` had before this branch) was never
going to be enough no matter what the backend's CORS policy said.

Both `127.0.0.1` and `localhost` are listed because Chrome's origin matching treats
them as genuinely different origins (they don't alias each other for
`host_permissions` purposes) even though they resolve to the same machine — and both
spellings are common in local dev instructions (the backend's own `README.md:22`
example uses `127.0.0.1`, but plenty of people habitually type `localhost`). If the
real backend URL ever moves to something else (a deployed host, a different port),
that origin needs its own `host_permissions` entry too — this is a real, recurring
maintenance point, not a one-time fix.

## 4. The fixture (`fixture.ts`)

`USE_FIXTURE` defaults to `true` (`config.ts`'s `DEFAULT_USE_FIXTURE`), so a fresh
unpacked-extension load works with **zero backend running** — `buildFixtureResponse()`
is called directly instead of `analyzeViaBackend()`.

**Shape discipline:** every field name comes from `src/shared/contract.ts`'s `Claim` /
`Evidence` / `AnalysisResponse` interfaces and from `MOCK_ANALYSIS` in
`src/content/render/__mock__/mock-response.ts` — nothing invented, no new field
names. Unlike `MOCK_ANALYSIS` (one different assessed status per claim, to exercise
every rendering treatment), **every claim here has `assessment: null`** and
**`articleVerdict.level` is `"unrated"`** — this is the exact shape
`backend/app/services/envelope.py`'s `to_analysis_response()` produces on the backend
when called with no `assessments` dict (the pre-WS6 path;
`WS3-CONTRACT-AUDIT.md` Task A table, row 9), which is the shape this message hop most
needs to prove it carries intact, independent of whether WS6 has run.

**Executed, not just asserted, contract-valid:** `fixture.test.ts` imports the real
`isAnalysisResponse()` from `src/shared/contract.ts` and runs it against
`buildFixtureResponse(...)` — passing (see run output above). This test file lives
under `src/background/`, in scope as "a new file under the extension source dir for
the backend client."

### f1/f2: real, non-null anchor hints — hand-derived, then proven by execution

Per the task ("at least two claims with populated `char_start`/`char_end`/`prefix`/
`suffix` so Darren's anchoring is genuinely exercised, not just the happy path with
empty anchors" — every claim in `MOCK_ANALYSIS` has these fields entirely absent),
`f1` and `f2` carry real values, computed by hand against
`test/fixtures/gnarly-article.html`'s DOM, following `src/content/anchor/text-index.ts`'s
exact flattening rules (NBSP and all whitespace collapse to single spaces; a synthetic
space is inserted at every block-level boundary; `nav`, `aside`, `figcaption` are
skipped tags and contribute no text).

By-hand derivation (article body only, `nav`/`aside`/`figcaption` excluded per
`text-index.ts:15-32`):

| Block | Content | Flat range |
|---|---|---|
| `<h1>` | "Singapore Reports Sharp Rise in Impersonation Scam Losses in 2025" | `[0, 65)` |
| synthetic boundary space | | `65` |
| byline `<p>` | "By A. Reporter · 1 Feb 2026" | `[66, 93)` |
| synthetic boundary space | | `93` |
| `<p>` "Singapore recorded 3,363 cases…2024." | | `[94, 205)` |
| synthetic boundary space | | `205` |
| `<p>` "Victims lost…per victim." — **this is `f2`** | | `[206, 316)` |
| synthetic boundary space | | `316` |
| `<p>` "In one widely reported case…senior officials." — **this is `f1`** | | `[317, 505)` |

So `f2.char_start = 206`, `f2.char_end = 316` (`316 - 206 = 110 === f2.text.length`,
asserted directly in `fixture.test.ts`), `f2.prefix = "1,504 cases in 2024. "` (the
literal 21 characters immediately before, taken from the end of the preceding `<p>`),
`f2.suffix = " In one widely reported case,"` (the literal next 29 characters, the
start of the following `<p>`, plus the boundary space).

`f1.char_start = 317`, `f1.char_end = 505` (`188` characters, matching `f1.text.length`),
`f1.prefix = "S$72,229 per victim. "` (last 22 characters of the preceding `<p>`, the
end of `f2`'s own sentence), `f1.suffix = " The Singapore Police Force"` (start of the
next `<p>`).

**`f1` is deliberately the trickier of the two.** Its sentence in
`gnarly-article.html` contains an inline `<sup><a href="#fn1">1</a></sup>` footnote
marker mid-sentence — `"...artificial intelligence<sup><a>1</a></sup> was used..."` —
and `<sup>`/`<a>` are inline elements, not in `text-index.ts`'s skip list, so the real
flattened DOM text is `"...intelligence1 was used..."`, **not** the clean
`"...intelligence was used..."` that `f1.text` (and `MOCK_ANALYSIS`'s equivalent claim,
`c1`) states. That single glued-in digit means `match-claim.ts`'s exact-substring path
(`allIndexesOf`) finds **zero** hits, and the claim can only be located via the fuzzy
Sørensen–Dice fallback (`match-claim.ts:165-193`). `f2`'s sentence has no such inline
markup and resolves via the exact path. This wasn't an oversight discovered after the
fact — `gnarly-article.html`'s own header comment says it exists specifically to
"reproduce the DOM shapes that break naive indexOf," including "an inline `<sup>`
footnote mid-sentence," and `MOCK_ANALYSIS`'s own `c1` is the same sentence for the
same reason (confirmed already passing in the pre-existing
`src/content/anchor/fixtures.test.ts`, run as a baseline before writing this fixture —
see the run log above).

One honest caveat, stated rather than hidden: `matchClaim()` only ever reads a claim's
`char_start`/`prefix`/`suffix` hints inside `scoreExactCandidate()`
(`match-claim.ts:98-123`), which only runs when the exact-match path finds **more than
one** hit to disambiguate between. Neither `f1` (zero exact hits → fuzzy path, which
never looks at the hints at all) nor `f2` (exactly one exact hit → hint disambiguation
is skipped, the single hit is just used) actually exercises that disambiguation branch
specifically — there's no repeated sentence in `gnarly-article.html`'s indexed content
to construct that scenario without editing the fixture file, which is out of scope
here. What *is* proven, by the executed test, is the part actually asked for: these
fields are populated (not `null`/absent), survive `chrome.runtime.sendMessage`'s
structured-clone serialization, arrive at the content script as real numbers/strings,
and the claims they belong to anchor correctly — one via each of the two paths the
matcher actually has.

`fixture.test.ts`'s last two tests build `document.body` from the real
`test/fixtures/gnarly-article.html` file and run the real `rangesForClaims()`
(`src/content/anchor/to-range.ts`, imported, not modified) over the fixture's claims —
this is the same pattern `src/content/anchor/fixtures.test.ts` already uses for
`MOCK_ANALYSIS`. Result: `f1` and `f2` both anchor, `f1` with `exact: false` and a Dice
score `>= 0.72`, `f2` with `exact: true`, and neither lands inside `nav`/`aside`. This
is executed proof, not a claim resting on my arithmetic being right.

## 5. No module-level request state

`config.ts`'s `getBackendConfig()` and `backend-client.ts`'s `analyzeViaBackend()` hold
nothing at module scope — every piece of per-call state (`payload`, `config`, the
`AbortController`, the timeout handle) lives in a function parameter or a local
`const`/`let` inside the call that needs it, captured by closure across the `await`
boundaries. `service-worker.ts`'s listener follows the same rule (comment at
`service-worker.ts:89-94`): `payload` is a `const` inside the listener callback, never
hoisted out. This matters specifically because Chrome can terminate and restart an
MV3 service worker's whole JS context at any time, including mid-request — a
module-level `let pendingRequest = ...` set when a request starts and read when the
response arrives would be silently wiped if the worker restarts in between, and the
caller's `sendMessage()` would just hang until its own timeout. Nothing in this
codebase does that.

**`sendResponse` failing:** wrapped in `try { sendResponse(envelope) } catch { ... }`
(`service-worker.ts:104-114`) with a comment explaining why swallowing it is correct:
the content script's port can die before the response arrives (tab closed, navigated
away, or `bootstrap.ts`'s own SPA-navigation handler already tore down and remounted
the fact-check UI) — at that point nobody is listening, there's nothing to recover,
and letting that exception propagate would be a worker-level unhandled rejection over
a completely ordinary race, not a bug.

## 6. Report only — `published_at` / `source_domain`, per your instruction not to fix

Confirmed by reading (not modified): `bootstrap.ts:50-59`'s call to `requestAnalysis()`
**does** include both `published_at: extracted?.publishDate ?? undefined` and
`source_domain: extracted?.sourceDomain ?? undefined` in the payload it sends. This
matches `WS3-CONTRACT-AUDIT.md`'s Task C finding exactly: `backend/app/models/contract.py`'s
`AnalysisRequest` (lines 229-235) declares only `url`, `title`, `text`, `images` — no
`published_at`, no `source_domain` — so Pydantic's default `extra="ignore"` behavior
silently drops both fields on arrival server-side. Nothing on the client needed to
change for this branch's scope (the extension is sending everything it reasonably can
already); the fix belongs in `contract.py`/`main.py`, which you said you'd handle.

---

## Definition of done — how to verify by hand

**With `USE_FIXTURE = true` (the default, no code change needed):**

1. `npm run build` (already run once by me; rerun if you pull further changes) —
   produces `extension/service-worker.js` and `extension/content-script.js`.
2. Chrome → `chrome://extensions` → enable Developer Mode → "Load unpacked" → select
   the `extension/` folder.
3. Open any real article page that Tier 0/1 would actually check — i.e. a page that
   passes `isProbablyArticle()` and is **not** on the Tier-0 whitelist in
   `src/lib/whitelist.ts` (so not Straits Times/CNA/BBC/etc.). Do **not** append
   `?dasfaxMock=1` — that flag makes `analysis-client.ts` resolve a bundled mock
   directly and skips this message hop entirely, proving nothing about this branch.
4. You should see the summary pill appear bottom-right, go through its loading state,
   then render **4 claims**, an "unrated" article verdict, and (open the panel) every
   claim showing the "Not yet verified" treatment (`assessment: null` → `unverified` in
   `status-config.ts`) — confirming the message actually round-tripped through the new
   `onMessage` listener and back, not the dev-mock path.
5. Open the service worker's own console (`chrome://extensions` → this extension →
   "service worker" link) — you should **not** see the old
   `"Could not establish connection"` / `"Receiving end does not exist"` error that
   `bootstrap.ts:71-72` used to catch and log as `[WS2] no analysis backend yet`; that
   error class should be gone entirely now that something is listening.

**To see genuinely anchored highlights (not just a correctly-shaped panel), load the
one page these two hint-bearing claims were computed against:**

6. `cd test/fixtures && python -m http.server 8080` *(any port other than the
   backend's 8000, to avoid confusion; builtin, nothing to install)* — Chrome content
   scripts don't run on `file://` URLs under this manifest's `matches` (`http://*/*`,
   `https://*/*` only), so the fixture needs to be served, not opened directly.
7. Visit `http://localhost:8080/gnarly-article.html` *(match whatever port you used)*.
8. You should see **two highlighted spans** in the article body: the "Victims lost a
   total of S$242.9 million…" sentence (`f2`, anchored via exact match) and the "In one
   widely reported case, a Singapore businessman lost S$4.9 million…" sentence (`f1`,
   anchored via the fuzzy fallback, despite the inline footnote digit glued into the
   live DOM text). Clicking either opens the panel to that claim's detail view. This is
   the "correctly anchored" half of the definition of done, made concrete and
   reproducible rather than dependent on whatever text a random live news site happens
   to contain that day.

**To exercise the real backend instead of the fixture:** start the backend
(`cd backend && uvicorn app.main:app --reload`), then from the service worker's
console: `chrome.storage.local.set({ dasfaxUseFixture: false })`, reload the article
tab. To point at a non-default backend URL or timeout, set
`dasfaxBackendBaseUrl` / `dasfaxRequestTimeoutMs` the same way (§2) — remember to add
the new origin to `host_permissions` in `manifest.json` first if it's not
`127.0.0.1:8000` or `localhost:8000` (§3), or the `fetch` will be blocked before it
leaves the extension regardless of what `dasfaxBackendBaseUrl` says.
