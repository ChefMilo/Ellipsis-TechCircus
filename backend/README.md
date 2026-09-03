> **Also in this backend: WS4 (Tier 2 ML screening)** — `POST /tier2/screen`, the BERT +
> image-detector cascade that decides what is worth escalating to Tier 3. See
> [WS4.md](WS4.md). It shares `app/config.py` and reuses `PageEnvelope` from
> [`app/models/contract.py`](app/models/contract.py); everything below is WS5.

# Dasfax backend — WS5 (Claim Extraction & Evidence Retrieval)

Tier 3, first half. Takes cleaned article text → returns a small set of **ranked,
checkable factual claims, each with retrieved evidence**. This is the exact object
WS6 (assessment) consumes.

Runs **fully offline** with mock LLM + mock search (no API keys). Swap in real
providers by setting env vars — no pipeline code changes.

---

## Quick start

```powershell
# from backend/
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m pytest -q          # 16 tests, all offline
python run_demo.py           # pretty-prints WS5 over the sample article
uvicorn app.main:app --reload   # then POST to http://127.0.0.1:8000/tier3/claims
```

`GET /health` and interactive docs at `/docs` (Swagger) once the server is up.

---

## The contract (this is the important part for WS6 / WS3)

Everything lives in [`app/models/contract.py`](app/models/contract.py) — **the single
source of truth for the Tier 3 seam.** WS6 and WS3 should `from app.models.contract import ...`
rather than redefining shapes. If a field must change, change it there and tell the team.

**WS5 input** (`ArticleInput`) — what WS3/WS1 hand in:

```json
{
  "url": "https://news.example.org/sg/story",
  "title": "Headline",
  "text": "Cleaned article body text ...",
  "lang": "en",
  "source_domain": "news.example.org",
  "published_at": null
}
```

**WS5 output** (`ClaimExtractionResult`) — what WS6 receives:

```json
{
  "url": "...",
  "title": "...",
  "claims": [
    {
      "id": "c1",
      "text": "Self-contained factual claim sentence.",
      "claim_type": "factual",
      "checkworthiness": 0.92,
      "rank": 1,
      "search_query": "...",
      "evidence": [
        {"snippet": "...", "source_url": "https://...", "source_title": "...",
         "source_domain": "...", "published_at": null, "relevance_score": 0.75}
      ]
    }
  ],
  "stats": { "total_claims_extracted": 10, "factual_claims": 8,
             "dropped_opinion_or_prediction": 2, "dropped_duplicate": 1,
             "kept_after_ranking": 5, "claims_with_evidence": 5 },
  "model_meta": { "llm_backend": "...", "search_backend": "...", "max_claims": 5 }
}
```

**WS6's side of the contract** is also defined here (`Assessment`, `AssessmentStatus`,
`Citation`) so WS6 can start immediately: WS6 reads each `Claim` and writes one
`Assessment` keyed by `claim.id`. WS6 does **not** mutate the Claim. Rule to honour
(proposal §2.5): every non-`opinion` assessment carries ≥1 `Citation`, or its status is
`needs_review`.

`claim.text` is a self-contained sentence on purpose — that's what **WS2** fuzzy-matches
back onto the live DOM.

---

## How WS3 calls WS5

Two options, same result:

```python
# In-process (preferred inside the orchestrator — no HTTP hop)
from app.pipeline.ws5 import run_ws5
from app.models.contract import ArticleInput
result = run_ws5(ArticleInput(url=..., title=..., text=...))

# Or over HTTP
# POST /tier3/claims  with an ArticleInput body -> ClaimExtractionResult
```

---

## Pipeline (what happens inside `run_ws5`)

1. **Extract** candidate claims from the text (LLM).
2. **Dedup** near-verbatim repeats (token-Jaccard ≥ `DASFAX_DEDUP_THRESHOLD`).
3. **Rank** factual claims by checkworthiness; keep the top `DASFAX_MAX_CLAIMS`
   (3–5). *Deliberate design decision:* checking all 30+ claims in an article is slow
   and reads as noise — we check the load-bearing few. Say this in the pitch.
4. **Retrieve** ≥ `DASFAX_EVIDENCE_PER_CLAIM` evidence snippets per kept claim,
   excluding the article's own domain.
5. **Assemble** `ClaimExtractionResult` with stats + provenance.

Opinions/predictions are extracted and counted but not retrieved on (no point spending
search budget on unverifiable statements) — WS6 can still give them an "Opinion" treatment.

---

## Swapping in real providers

Default is mock. To go live, set env vars (see `.env.example`) — nothing else changes:

| Concern | Mock (default) | Real |
|---|---|---|
| Claim extraction | heuristic sentence classifier | `DASFAX_LLM_PROVIDER=openai` + `OPENAI_API_KEY` |
| Evidence retrieval | seeded synthetic snippets (`*.example.org`) | `DASFAX_SEARCH_PROVIDER=tavily` + `TAVILY_API_KEY` |

The real LLM extraction **prompt** lives in `app/clients/openai_llm.py` — iterate it
there; the interface and downstream pipeline are unaffected. To use a different search
provider (Serper, Brave, Google PSE), add a sibling client implementing `SearchClient`
and register it in `app/clients/factory.py`.

Tuning knobs (env, all optional): `DASFAX_MAX_CLAIMS`, `DASFAX_MIN_CHECKWORTHINESS`,
`DASFAX_EVIDENCE_PER_CLAIM`, `DASFAX_DEDUP_THRESHOLD`. These are the numbers to defend
when a judge probes "why these claims, why this many".

---

## Known limitations (honest list, for the pitch and for WS6)

- **Dedup is lexical, not semantic.** Token-Jaccard collapses near-verbatim repeats but
  **not paraphrases** ("3,363 cases, up from 1,504" vs "3,363 cases, more than doubled").
  Real deployment wants embedding-based dedup. *(TODO — see `app/services/ranking.py`.)*
- **Mock extraction is heuristic**, so on the mock path a long opinion sentence with
  numbers can score as checkworthy. The real LLM extractor classifies far better; the
  mock exists so the seam and the demo work offline, not to be accurate.
- **Mock evidence is synthetic** and always `*.example.org` — never present it as real
  sourcing. Switch to Tavily for anything shown to judges as a live check.
- WS5 does **not** fetch or clean pages; it trusts `ArticleInput.text` (WS1's job).

---

## Layout

```
backend/
  app/
    models/contract.py     # THE CONTRACT (WS5 in, WS6 out) — single source of truth
    config.py              # env-driven settings; mock by default
    clients/               # LLM + search: base Protocols, mocks, real stubs, factory
    services/ranking.py    # dedup + checkworthiness ranking
    services/retrieval.py  # query building + evidence shaping
    pipeline/ws5.py        # run_ws5(): the WS5 entry point
    main.py                # FastAPI: /health, POST /tier3/claims
  tests/                   # 16 offline tests (pipeline + API)
  run_demo.py              # offline demo over the sample article
```

> **Note on folder naming:** there is deliberately **no `lib/` folder anywhere** in this
> tree. The repo's root `.gitignore` (GitHub Python template) ignores `lib/`, which is
> what silently swallowed WS1's `src/lib/`. Keep WS5 code out of any `lib/` directory.
