# WS2 — In-page rendering & evidence panel

Everything the user sees on the page: claim highlights painted onto the live
article, a Shadow-DOM **hovercard** that pops up beside a claim on hover (click to
pin it open) with that claim's status, explanation and sources, and a fuller
evidence **drawer** — the all-claims overview — opened from the summary pill. No
network calls originate here (MV3 rule) — WS2 renders whatever the background/WS3
hands back.

## Run it on any news site (no backend needed)

```bash
npm install
npm run build          # -> extension/content-script.js
```

Load `extension/` as an unpacked extension in Chrome, then open any article with
`?dasfaxMock=1` appended to the URL (or set `localStorage['dasfax:mock'] = '1'`).
The bundled mock (`src/content/render/__mock__/mock-response.ts`) renders, so you
can eyeball anchoring + the panel on Straits Times, CNA, etc. — the mock path
bypasses Tier 0/1 triage, which otherwise short-circuits on whitelisted domains.

```bash
npm test               # vitest
npm run typecheck
```

Regenerate the mock's claim objects from the real WS5 pipeline:

```bash
cd backend && python run_demo.py     # copy the `claims` into mock-response.ts
```

## Shape of things

| Area | File(s) |
|---|---|
| Contract mirror + client envelope + `isAnalysisResponse()` | `src/shared/contract.ts` |
| Text normalisation (shared with WS5's cleaning ideally) | `src/shared/text-normalize.ts` |
| DOM → flat text index + offset map | `src/content/anchor/text-index.ts` |
| Claim → flat span (exact, then fuzzy Dice) | `src/content/anchor/match-claim.ts` |
| Flat span → live `Range`, block-boundary clamp | `src/content/anchor/to-range.ts` |
| Status → colour/icon/label (one source) | `src/content/render/status-config.ts` |
| Shared badge + source-list builders (drawer & hovercard) | `src/content/render/claim-view.ts` |
| Highlight painting (Highlight API + span fallback), hover/active emphasis | `src/content/render/highlights.ts` |
| Shadow root + scoped CSS | `src/content/render/shadow-root.ts` |
| Summary pill (loading / result / error / trusted / dismissed) | `src/content/render/pill.ts` |
| Hover preview — status + evidence popover anchored to a claim (primary surface) | `src/content/render/hovercard.ts` |
| Evidence drawer (all-claims list + detail, focus-trap, Esc) — opened from the pill | `src/content/render/panel.ts` |
| Orchestration, pointer/keyboard hit-testing, re-anchor observer | `src/content/render/controller.ts` |
| Dev/prod data source | `src/content/analysis-client.ts` |

`bootstrap.ts` (WS1) calls `mountFactCheckUI()` when a page is judged an article,
then `showLoading()` → `render(response)` / `showError()`, and `teardown()` on SPA
navigation.

## Coordination still open

- **WS5** owns `backend/app/models/contract.py`. Requested: optional
  `char_start` / `char_end` (or `prefix` / `suffix`) on `Claim` — big win for DOM
  re-anchoring, matcher already reads them when present.
- **WS3** (not started): ratify the `AnalysisResponse` envelope in
  `src/shared/contract.ts`; it must wrap `VerifiedClaim[]` unchanged. WS2 sends
  `{type:"dasfax:analyze", payload}` to the service worker and validates the reply.
- **WS1**: hand the resolved article root element to the content script so
  matching is scoped to the article body.
- Per-URL dismissal persistence needs `"storage"` in `manifest.json`; without it
  the pill still collapses, just not across reloads.
