# Dasfax

A Chrome extension that fact-checks news articles as you read them — entirely
client-side triage first, so it never wastes a backend call (or your attention)
on a page that doesn't need one.

---

## What it does

1. You browse normally.
2. **Tier 0** checks the domain against a whitelist of trusted news sources
   (Straits Times, CNA, BBC, Reuters, ...). Trusted source → a small "✓ Trusted
   source" badge, nothing else happens. No backend call.
3. **Tier 1** scores whether the page actually looks like an article (byline,
   paragraph density, text-to-markup ratio, etc.) versus a product page, an
   inbox, a video player. Not an article → a quiet "Check this page anyway"
   button appears; nothing runs unless you click it.
4. If it *is* an article: the page's title, cleaned body text, author, date,
   and images are extracted locally (via Mozilla's Readability) and sent to
   the backend.
5. **Tier 2** screens the article in the backend in under a second — a fine-tuned
   BERT text classifier and an AI-generated-image CNN, run in parallel — and
   decides whether the page is worth the expense of Tier 3. Most pages stop here.
   That is the point of the cascade: Tier 3 costs seconds and real API spend.
   On by default; `DASFAX_TIER2_ENABLED=0` sends every page to Tier 3 instead. A page
   Tier 2 clears is never fact-checked, so see [backend/WS4.md](backend/WS4.md) for what
   the models do and do not detect, measured.
6. **Tier 3** extracts the article's load-bearing factual claims, retrieves
   evidence for each one, and returns a verdict per claim — which the
   extension paints back onto the live article as highlights, with a
   bottom-right summary pill and a hovercard/evidence panel per claim.

Nothing is ever sent anywhere until a real article has been positively
identified — Tiers 0 and 1 run 100% locally in the content script before any
network call is possible.

## Quick start

```bash
git clone <this repo>
cd Ellipsis-TechCircus
docker compose up --build      # backend, fully offline by default, no API keys needed
```

Then, separately:

```bash
npm ci
npm run build                  # -> extension/service-worker.js + extension/content-script.js
```

Load `extension/` as an unpacked extension in Chrome (`chrome://extensions` →
Developer mode → Load unpacked), then visit any real news article.

### Tier 2 model weights

The image CNN downloads itself on first use. **The text model does not** — it is our own
fine-tune, and its 418MB of weights are not in the repo: GitHub rejects any single file
over 100MB, and the weights compress to 387MB at best. Get them one of two ways.

**Option A — copy the folder (5 minutes, recommended).** Ask a teammate for
`dasfax-tier2-text.tgz`, then from the repo root:

```bash
mkdir -p backend/models
tar -xzf dasfax-tier2-text.tgz -C backend/models
```

The folder must land at exactly `backend/models/dasfax-tier2-text/`, containing
`model.safetensors`, `config.json`, `tokenizer.json`, `tokenizer_config.json`,
`special_tokens_map.json` and `vocab.txt`.

**Option B — train it yourself (~27 minutes).**

```bash
pip install -r backend/requirements-ml.txt
cd backend && python train_ws4.py
```

This trains on the same corpus with the same seed, but it will not reproduce the shipped
weights *exactly*: the script does not set `torch.use_deterministic_algorithms`, and
floating-point results differ across MPS / CUDA / CPU. You get an equivalent model, not an
identical one — and since Tier 2 ships at a threshold of 0.995, the measured figures in
[backend/WS4.md](backend/WS4.md) describe the shipped weights specifically. Prefer Option A
if you want the numbers to match.

### Verify which backend is actually live

Do this after either option, before concluding anything from a run:

```bash
curl -s localhost:8000/health | python3 -m json.tool | grep -A3 screening_backends
```

```json
"screening_backends": [
    "text: hf:models/dasfax-tier2-text",
    "image: hf:Organika/sdxl-detector"
]
```

If it reads `heuristic-text-v1`, the weights were not found — check the path above.

**This matters more than it looks.** Tier 2 gating is ON by default, and when the text
model is missing the backend falls back to a keyword heuristic that clears almost every
page. You will get `unrated` with zero claims on every article, with `status: complete`
and no error anywhere — indistinguishable from a broken backend. Set
`DASFAX_TIER2_ENABLED=0` to route every page to Tier 3 regardless, which is also what you
want if you are demoing without the weights.

See **[backend/WS4.md](backend/WS4.md)** for what the models do and do not detect, measured.

**For the full walkthrough** — offline/no-network demo mode, swapping in real
LLM/search providers, three concrete test URLs that exercise each tier, and
every known limitation stated plainly — see **[DEMO.md](DEMO.md)**.

## Repo layout

```
src/                  Chrome extension (TypeScript)
  lib/                Tier 0/1: whitelist, article heuristic, extraction
  background/         Service worker: message hop to the backend
  content/            Content script: bootstrap, panel/highlighting (WS2), anchoring
  shared/             Contract types shared between extension and backend

backend/              FastAPI backend (Python)
  app/models/         contract.py — the single source of truth for the Tier 3 shape
  app/orchestrator/   WS3: routes a request through Tier 2 -> Tier 3 -> response
  app/pipeline/       ws4.py — Tier 2 screening; ws5.py — claims + evidence
  app/services/       Ranking, anchoring, assessment, envelope; Tier 2 scorers + calibration
  app/clients/        LLM / search / assessor backends, and the Tier 2 model loaders
                      (mock + real, swappable)
  train_ws4.py        Fine-tunes the Tier 2 text model (~27 min; see Tier 2 model weights)
  ws4_eval.py         Tier 2 confusion matrices, threshold sweeps, real-news FPR

docs/ws3/             Deep-dive docs on the contract, the message hop, and deploy
DEMO.md               Full run-through: setup, offline mode, test URLs, limitations
backend/README.md     WS5 pipeline detail
WS2.md                WS2 panel/rendering detail
```

## Tech stack

- **Extension:** TypeScript, Vitest, esbuild, Chrome MV3, [`@mozilla/readability`](https://github.com/mozilla/readability)
- **Backend:** Python, FastAPI, Pydantic v2, pytest
- **LLM/search/assessment providers:** pluggable — mock (default, fully offline), OpenAI-compatible, Anthropic, Tavily

## Testing

```bash
npm test              # extension: 109 tests
cd backend && pytest -q   # backend: 203 tests
```

Both suites run in CI on every push and PR (`.github/workflows/ci.yml`) — backend
and extension as independent jobs, so either can fail without hiding the other.

## Team

Six workstreams, one extension:

- **WS1** — Extension shell & page triage
- **WS2** — In-page rendering & evidence panel
- **WS3** — Backend orchestration & deploy
- **WS4** — Tier 2 ML screening ([backend/WS4.md](backend/WS4.md))
- **WS5** — Claim extraction & evidence retrieval
- **WS6** — Claim assessment
