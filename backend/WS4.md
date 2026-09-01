# WS4 — Tier 2 ML Screening

Tier 2 sits between WS1's page triage and WS5/WS6's Tier 3 verification. It runs two fast,
task-specific models **in parallel** and answers one question: *is this page worth spending
Tier 3 budget on?* (proposal §2.2).

**Tier 2 is a router, not a verdict.** Nothing it produces is ever shown to a reader. A high
text score means "check this properly", not "this is fake".

```
POST /tier2/screen   (alias POST /screen)   ScreeningInput -> ScreeningResult
                                            WS3 routes on `escalate_to_tier3`
```

---

## Quick start

```bash
# from backend/
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # offline: heuristic fallback, ~10s, no weights
pip install -r requirements-ml.txt         # the real models (~2.5GB; CPU wheels on Linux)

pytest -q                                  # 98 offline tests, no network, no weights
pytest -m models                           # 8 more, needs the downloaded checkpoints

python ws4_eval.py --backend huggingface   # text confusion matrix + threshold justification
python ws4_eval.py --images                # image detector accuracy, broken down by generator
python ws4_eval.py --verify-labels         # which class index means "fake"
python ws4_bench.py --with-images          # p95 latency; re-derives the budget
uvicorn app.main:app                       # then POST to /tier2/screen
```

`GET /health` reports which backends are actually live — a degraded tier is visible there,
not only in an individual response.

---

## The two models

| | Model | Notes |
|---|---|---|
| Text | `omykhailiv/bert-fake-news-recognition` | `bert-base-uncased` fine-tuned on **two datasets the author never names**. See the provenance warning below — this is not the model the proposal describes. |
| Image | `Organika/sdxl-detector` | **Swin-tiny, not EfficientNet-B4** (in spec under "or alternative", but say it out loud). Fine-tuned from `umm-maybe/AI-image-detector` on Wikimedia↔SDXL pairs. **Licence is CC-BY-NC-3.0 — non-commercial only.** |

### Provenance warning: read this before quoting the text model

Straight from the model card, all of which conflicts with how we use it:

- **"Predicts whether the news article's *title* is fake or real."** It is a headline
  classifier by its author's own description, not an article-text classifier.
- **"One should not give less than 6 and more than 12 words for predictions"**, excluding
  stopwords. We feed it article bodies. A 900-word article is ~75× that ceiling.
- **"It was trained on 2 datasets, combined and preprocessed"** — the datasets are never
  named. The training data is undisclosed, so contamination against any public benchmark
  can be neither confirmed nor ruled out.
- **"Trained on pre-2023 data"**, so it knows nothing of recent events, and the author
  flags a likely bias around people's names left unpreprocessed.

The proposal (§2.2) says "a BERT text classifier, **fine-tuned on ISOT Fake News Dataset**".
This checkpoint is not documented as ISOT-trained. If a judge asks what it was trained on,
the honest answer is that we do not know.

Measured on our corpus, ignoring the card's advice is currently the right call — but only
just, and only at short inputs:

| input | AUROC (all) | AUROC (hard subset) |
|---|---|---|
| title only (the documented 6–12 word range) | 0.892 | 0.625 |
| body only (~62 words) | 0.942 | 0.766 |
| **title + body (what we ship)** | **0.947** | **0.766** |

This also reframes the dilution finding below. The model has a usable input window of
roughly 60–150 words; at 900 it collapses to noise. Chunked scoring is therefore not a
workaround bolted onto a working model — it is the way to keep this checkpoint inside the
only input range where it functions at all.

### We kept it anyway, because we measured the alternatives

Three public checkpoints, same corpus, same input handling per each card's documented
format:

| checkpoint | AUROC all | AUROC hard | recall@0.40 | FPR@0.40 | ms/doc |
|---|---|---|---|---|---|
| **`omykhailiv/bert-fake-news-recognition`** (ours) | **0.947** | **0.766** | 0.967 | 0.233 | 16 |
| `Pulk17/Fake-News-Detection` | 0.934 | 0.766 | 0.800 | **0.067** | 13 |
| `hamzab/roberta-fake-news-classification` | 0.907 | 0.719 | 0.567 | 0.033 | 19 |

The result is counter-intuitive and worth stating plainly. `hamzab` is the one that *looks*
right on paper — 200× more downloads than ours, `roberta-base`, an explicit
`<title>…<content>…<end>` article format, and trained on the Kaggle fake-and-real-news
dataset, **which is ISOT**, exactly what the proposal names. It reports **100% accuracy** on
that dataset. It is the worst of the three here, catching 57% of fakes against our 97%.

That is the artifact story in one line: 100% on ISOT buys nothing out of distribution.
Our accidental title-classifier beats it.

`Pulk17` is the honest runner-up — clearly better precision (FPR 0.067 vs 0.233) at lower
recall. Under a recall-first policy ours still wins, and it wins on AUROC too, so this is
not a threshold artefact. If the escalation budget ever becomes the binding constraint,
`Pulk17` is the swap to make.

### Dilution is not a model choice — it is the task formulation

All three collapse identically when a fake passage is diluted into a 900-word article:

| checkpoint | fake passage alone | same passage in 900 w | clean 900 w |
|---|---|---|---|
| omykhailiv | 0.9997 | 0.0095 | 0.0063 |
| Pulk17 | 0.9784 | 0.0002 | 0.0002 |
| hamzab | 0.9998 | 0.0001 | 0.0000 |

**Swapping checkpoints cannot fix this.** Every whole-document fake-news classifier scores
the register of the document as a whole. Chunked scoring, or moving the signal to Tier 3's
claim level, are the only routes.

Three backend modes via `DASFAX_SCREENING_MODE`:

- **`auto`** (default) — real checkpoints when the weights load, heuristic fallback otherwise,
  with `degraded_reason` on every response saying which.
- **`huggingface`** — strict. Refuses to start rather than silently fall back.
- **`heuristic`** — forced offline. What CI uses.

`ws4_eval.py` and `ws4_bench.py` pass `strict=True`, because a silent degrade would print a
confusion matrix captioned "BERT" that is actually a regex.

---

## Two things that would have been silent in production

**The label mapping.** Neither BERT checkpoint ships an `id2label`, so transformers invents
`LABEL_0`/`LABEL_1` and there is nothing in the artifact to read the mapping off. Getting it
backwards inverts the entire tier — fake articles score as safe — with no error anywhere.
It was established empirically, not from the model card:

```
python ws4_eval.py --verify-labels
  id2label       ABSENT — nothing to read the mapping off
  assuming index 0 == 'fake'  ->  AUROC 1.0000
  assuming index 1 == 'fake'  ->  AUROC 0.0000
  => index 0 is the 'fake' class.
```

Re-run it after any checkpoint or revision change. An unresolvable mapping degrades to the
heuristic rather than guessing.

**The image path was silently dead twice.** Once because the extension sends `images` while
the contract called the field `image_urls` (Pydantic drops unknown keys, so every page
screened image-clean); once because a python.org macOS build has no CA bundle, so every
https fetch failed `CERTIFICATE_VERIFY_FAILED`. Both now have regression tests, and a page
whose images could not be examined is reported as *not cleared*, never as clean.

---

## Why the threshold is 0.40

Measured on the 60-sample corpus with the real classifier. The corpus is deliberately split
into **easy** cases (obvious clickbait) and **hard** ones (real journalism that reads as
sensational — court reports leaning on "allegedly", genuine recall notices, police
advisories that really do say "share this with elderly relatives" — and misinformation
written in a calm, sourced-sounding register).

| subset | AUROC |
|---|---|
| easy (n=44) | 1.000 |
| **hard (n=16)** | **0.766** |

The hard subset is the honest number: it is what real browsing looks like.

| threshold | hard-subset recall | overall FPR |
|---|---|---|
| **0.40** | **0.875** | 0.233 |
| 0.65 | 0.625 | 0.200 |
| 0.85 | 0.625 | 0.133 |

Selecting on the *aggregate* picks 0.85. Selecting on the *hard subset* picks 0.40 — two
more of eight realistic fakes caught, for 3.3pp more false escalation. The aggregate hides
this because every easy case is caught at any threshold.

**Recall-first, deliberately.** A Tier 2 miss means the article is never checked and the
reader sees nothing. A Tier 2 false alarm costs Tier 3 compute and never renders a wrong
badge. "Don't cry wolf" (§3.3) binds at Tier 3/WS6, where a status is actually shown.

The same asymmetry sets the timeout policy: **text times out → fail open** (escalate);
**images time out → fail closed** (fetch failures correlate with CDNs and paywalls, not with
synthetic content).

Scores are **bimodal** (AUROC 0.947, ECE 0.134) — they pile at 0 and 1. The model ranks well
but its numbers are not probabilities, so any threshold between the modes behaves nearly
identically. Do not over-claim precision about the exact value.

---

## Read this first: the BERT checkpoints are not usable as a gate

Measured flag rate on **480 real news articles** (AG News test split, 120 each from World,
Sports, Business, Sci/Tech), at the 0.40 threshold:

| model | real news flagged as fake | recall on our synthetic fakes |
|---|---|---|
| `omykhailiv/bert-fake-news-recognition` | **83.1%** | 96.7% |
| `Pulk17/Fake-News-Detection` | 61.7% | 80.0% |
| `hamzab/roberta-fake-news-classification` | 55.2% | 56.7% |
| **`heuristic-text-v1`** (our fallback) | **0.0%** | 73.3% |

A gate that flags 83% of real news is not a gate. It escalates nearly everything, which
destroys the cost rationale for the entire cascade — and once WS6 renders verdicts, it puts
warnings on ordinary journalism.

Broken down by category, the pattern is the register bias below, at scale:

| category | flagged |
|---|---|
| Sports | 95.0% |
| Sci/Tech | 95.0% |
| Business | 79.2% |
| World (closest to its training distribution) | 63.3% |

Even World news — political hard news, the register it was trained on — is 63% false
positives.

**`DASFAX_SCREENING_MODE` therefore defaults to `heuristic`.** That is a decision made on
evidence, and it is one env var to reverse (`=auto`) for evaluation or once a fine-tuned
model exists.

**This is not a claim that the heuristic is good.** Its 0% false-positive rate is measured
on real articles and is trustworthy. Its 73.3% recall is measured against synthetic fakes
*written with the very markers it looks for* — that number is circular, and its true recall
against real misinformation is unknown. We have a gate that demonstrably does not cry wolf
and whose sensitivity is unmeasured, which is a better starting point than the reverse, but
it is not a finished component.

## Fine-tuning: WELFake alone halves the problem, and that is not enough

`train_ws4.py` fine-tunes `bert-base-uncased` on WELFake (72k articles merging four
source datasets), freezing all but the top two encoder layers.

**Held-out WELFake test: 94.2% accuracy, 95.6% precision, 90.8% recall.** That number is
worthless on its own, and quoting it would repeat the mistake every checkpoint above makes.
The benchmark that matters:

| model | real news flagged as fake |
|---|---|
| `omykhailiv` | 83.1% |
| `Pulk17` | 61.7% |
| `hamzab` | 55.2% |
| **ours, WELFake-only** | **46.7%** |
| heuristic fallback | 0.0% |

Best of every trained model, and still unusable. The register pattern survives training:

| category | flagged |
|---|---|
| World | 24.2% |
| Sports | 48.3% |
| Business | 50.0% |
| Sci/Tech | 64.2% |

No threshold rescues it. Sweeping to the extreme:

| threshold | FPR on real news | recall on synthetic fakes |
|---|---|---|
| 0.40 | 50.8% | 76.7% |
| 0.90 | 34.2% | 73.3% |
| 0.99 | 17.5% | 73.3% |

Recall is flat from 0.5 upward — the scores are bimodal again — so raising the threshold
buys precision without costing recall right up until it stops helping. Even at 0.99, one
real article in six is flagged.

**Why WELFake alone was never going to fix it:** it merges ISOT, McIntire, BuzzFeed
Political and a Kaggle set — all political hard news. Its "real" class has the same
register skew as the checkpoints trained on it. Training on register-skewed data teaches
register, more accurately.

Two things did improve and are worth keeping: the model now **ships a real `id2label`**
(`{0: real, 1: fake}`), so the empirical label-direction check is no longer needed
downstream; and the dateline scrubbing means it is not leaning on `(Reuters)`.

### Adding real news across content types: a perfect score that means nothing

The second run adds 8,000 AG News real articles (World/Sports/Business/Sci-Tech) to the
training mix. Result on the benchmark:

| | real news flagged |
|---|---|
| WELFake only | 46.7% |
| **WELFake + AG News** | **0.0%** |

A perfect score — and it is an illusion. Those articles came from AG News *train* and the
benchmark is AG News *test*: different splits, same distribution. The caveat was written
down before the run, and it turned out to be the whole story.

**The honest test is the chilli article** — a real, benign local story, never in any
training set:

| model | chilli article (REAL) | AG News test FPR | our-corpus real FPR |
|---|---|---|---|
| WELFake only | **0.9897 — flagged** | 46.7% | 3.3% |
| WELFake + AG News | **0.9960 — flagged** | 0.0% | 0.0% |

Both fine-tunes still flag it, and the augmented one flags it *harder* while scoring zero
on two other benchmarks. A model can look perfect on every held-out set you have and still
fail the one page a user actually opened.

**It is not a length artifact.** Concatenating real AG News articles up to 614 words keeps
them at 0.0% flagged, so the model is not simply calling long text fake. What augmentation
bought was *coverage of one more register* — wire-service news across topics — not
generalisation. Community and promotional writing sits outside both training registers and
is still flagged at 0.996.

**The lesson:** register bias cannot be patched by adding a register. Every real-news source
you add teaches the model that *that* source is real, and the long tail of web writing —
local papers, community reporting, blogs, trade press — remains outside. Fixing this needs
either genuinely diverse real-web-text data, or a different formulation than
whole-document fake-news classification.

**Where that leaves the tier:** the heuristic is still the only gate with a defensible
false-positive rate (0.0% on AG News, and it scored the chilli article 0.127 — correctly).
It is not good; it is honest, inspectable, and it does not cry wolf. The real
misinformation-detection work belongs at Tier 3, where claims are checked against
retrieved evidence rather than prose style.

## What the classifier actually discriminates

The first real page ever tested through the live extension was a local South African
story about a **chilli-eating contest**. The classifier scored it **0.999** — as
confidently fake as the deliberately sensational samples in our corpus. Every 120-word
chunk of it scored 0.998–0.9997, including the neutral list of prize sponsors.

The heuristic fallback — the "primitive" one — scored the same article **0.127**, and was
right.

A controlled probe explains it. Same facts, different framings:

| text | score |
|---|---|
| Institutional hard news: "The agency said… According to the agency… A spokesperson said…" | **0.022** |
| Same facts in community-event register | 0.988 |
| Neutral prose, South African names and Rand amounts | 0.882 |
| **Identical prose with US names and dollar amounts** | **0.990** |
| Formal wire register, but about a chilli contest | 0.831 |

It is **not** geography or domain shift — the US-markers version scored *higher* than the
South African one. What the model has learned is closer to:

> text that looks like attributed institutional hard news = REAL; everything else = FAKE.

Soft news, lifestyle, community events, sport and entertainment all read as fake to it,
regardless of truth. Chunking does not help — every chunk of a benign article scores 0.999.

### This invalidates part of our own benchmark

Every "real" sample in `eval/heldout_dataset.json` was hand-written by me in exactly that
institutional register — ministries, police, agencies, "according to". Every "fake" sample
was sensational. **So the reported AUROC of 0.947 largely measures institutional-register
versus sensational-register, not real versus fake.** The corpus and the model share a bias,
and the corpus therefore could not detect it.

The signs were already in the results and I read them too narrowly: the false positives at
the operating threshold were the opinion column (0.907) and the product recall notice
(0.937) — the two "real" samples written in a non-institutional register.

**Consequence for the product:** as a gate on general browsing this would escalate most
non-hard-news pages. Once WS6 renders verdicts, users would see flags on chilli-eating
contests. The number to trust is not 0.947; it is closer to the hard-subset 0.766, and even
that is measured on a corpus with the same blind spot.

**Cheapest mitigation to evaluate next:** the heuristic scored this correctly, so requiring
both signals to agree before escalating would have caught it. That trades recall for
precision and needs measuring, not assuming — but it is a concrete, testable lead.

## The text classifier is a register detector, not a claim detector

**This is the most important limitation in WS4, and it goes to the product's premise.**

The classifier scores a whole document. It fires when an entire article *reads* like fake
news — sensational register, absent attribution, share-bait — not when an article
*contains* false claims. Measured by embedding a 63-word passage that scores 0.9997 on its
own into increasingly long stretches of ordinary reporting:

| document | fake share | score | flagged at 0.40? |
|---|---|---|---|
| 63 w | 100% | 0.9997 | yes |
| 100 w | 63% | 0.9947 | yes |
| 150 w | 42% | 0.6984 | yes |
| 200 w | 32% | 0.2628 | **no** |
| 300 w | 21% | 0.0438 | no |
| 900 w | 7% | 0.0095 | no |

**Roughly 35–40% of the document must be fake before it fires.** Position is irrelevant —
a fake passage as the very first paragraph of a 900-word article scores 0.0095, versus
0.0063 for the same article with no fake content at all. This is dilution, not truncation.

Why that matters more than the accuracy figures: the proposal's own case for claim-level
assessment is that "real articles often mix sound and unsound claims" (§2.3, §3.2). Tier 3
exists precisely to handle mixed articles — and Tier 2, which gates it, is structurally
incapable of detecting them. **The articles Tier 3 was built for are the ones that will
never reach it.** Tier 2 catches wholesale fabrications; a mostly-true article carrying
three false claims passes straight through.

Note this is invisible in the confusion matrix above, because every corpus sample is ~62
words — one chunk, no dilution. The evaluation never exercised production input length.

### The candidate fix, and why it is not shipped yet

Score overlapping windows and take the maximum, instead of scoring the document once.
Measured on a 900-word article with a 7% fake passage against a clean 900-word control:

| window/stride | spiked | clean | separation | latency |
|---|---|---|---|---|
| 60/40 | 0.9996 | 0.2561 | 3.9× | 1169 ms |
| 90/60 | 0.9980 | 0.2686 | 3.7× | 589 ms |
| **120/80** | **0.6321** | **0.0842** | **7.5×** | **572 ms** |
| 180/120 | 0.2163 | 0.0649 | 3.3× | 708 ms |
| whole doc | 0.0095 | 0.0063 | 1.5× | ~25 ms |

A 120-word window recovers the diluted signal from 0.0095 to 0.632 — above threshold —
while clean prose stays at 0.084.

It is **not** enabled, for two reasons. First, max-pooling over N windows takes the
maximum of N draws, so longer articles get more chances to cross the threshold; that
plausibly raises the false-positive rate on genuine long-form journalism, and the current
corpus cannot measure it because every sample is one chunk. Second, 572 ms of text
inference does not fit the 250 ms text budget and would need the budget re-derived.

Shipping it now would trade a measured limitation for an unmeasured one. It needs a corpus
of real, full-length articles first — which is the same thing the text benchmark needs.

## The image detector is the weak half — measured, not assumed

`python ws4_eval.py --images` scores the real CNN against
[eval/images_ood.json](eval/images_ood.json): 66 Wikimedia Commons images, AI-generated
ones grouped **by generator**, evaluated at the 512px rendition the production fetch path
actually produces (benchmarking pristine originals would overstate accuracy, because
resampling destroys the high-frequency artifacts these detectors key on).

Overall: **AUROC 0.821**, and at the 0.70 threshold recall 0.722, precision 0.812.
That is materially worse than the text side, and the breakdown says why:

| generator | n | recall | 95% interval |
|---|---|---|---|
| Grok | 6 | 1.000 | 0.61–1.00 |
| Flux | 6 | 0.833 | 0.44–0.97 |
| DALL·E | 8 | 0.750 | 0.41–0.93 |
| Midjourney | 8 | 0.625 | 0.31–0.86 |
| **Stable Diffusion** | 8 | **0.500** | 0.22–0.78 |

**The checkpoint is called `sdxl-detector` and it is worst on Stable Diffusion.** The
likely explanation is that Commons' Stable Diffusion category spans SD 1.x through SDXL
and leans heavily on stylised art, while the detector was tuned on SDXL photorealism — but
that is a hypothesis, and the intervals here overlap heavily at n=8. Do not quote a single
aggregate for this model; the per-generator spread *is* the finding.

Two limits that moving the threshold cannot fix:

- **Six of thirty genuine photographs score above 0.95** — a 20% false-alarm rate that is
  flat across every threshold from 0.60 to 0.95. Because escalation is OR (per §2.2), about
  one page in five carrying real photos escalates on the image signal alone. That costs
  Tier 3 compute, not user trust: images never render a badge. A plausible future fix is
  requiring two or more flagged images per page, which this per-image manifest cannot
  calibrate.
- **Recall never reaches 0.90 at any threshold**, so the recall-first criterion that sets
  the text threshold cannot be satisfied for images at all.

Two caveats on this benchmark, the second of which I introduced myself:

- Commons' AI categories contain a lot of illustration and digital art, not only
  photorealistic imagery. The deployment case that matters — a photorealistic fake in a
  news article — is under-represented.
- **The authentic class is drawn from Wikimedia, and this checkpoint was fine-tuned on
  Wikimedia↔SDXL pairs.** Its "human" training examples come from the same source as my
  negatives, so the 20% false-alarm rate is measured on in-distribution photographs and
  should be read as a *floor*. Real news photography from CNA or the Straits Times is
  out-of-distribution for it and would likely fare worse. Replacing the negative class
  with press photographs is the fix.

The card also warns that performance "may be lower for images generated using models other
than SDXL", and specifically that it underperforms its predecessor on older generators —
which is consistent with the weak Stable Diffusion row above, since Commons' Stable
Diffusion category spans SD 1.x through SDXL.

## Latency

`python ws4_bench.py --with-images`, arm64 macOS, python 3.14, single user, 3 images:

| config | p95 |
|---|---|
| heuristic fallback | 0.2 ms |
| real models, text only | 17.5 ms |
| real models, with images | 549 ms |

Budget is **750 ms**, derived from that p95 with headroom — not the 300 ms this started
with. At 300 ms the image task hit its deadline on *every* request, so the image half of
Tier 2 contributed nothing while responses still read "nothing flagged". A budget that
quietly disables a model is worse than a slow one. Tier 2 runs in the background while the
user reads, so ~0.5s is not user-visible latency.

Images are fetched **in parallel** with a shared deadline. Sequentially, six images at a 4s
timeout is a 24-second worst case.

---

## Honest limitations

- **The bundled corpus is synthetic and hand-authored.** It is large enough to pick a
  threshold and catch regressions in CI, and too small to be an accuracy claim. Every figure
  carries a Wilson 95% interval for that reason. For a defensible number, run
  `ws4_eval.py --backend huggingface --dataset <real-heldout>.jsonl`.
- **The text checkpoint's training data is unprovable.** Its config records only a Colab
  path. Overlap with public fake-news corpora cannot be ruled out, so any in-distribution
  benchmark score should be treated as presumed contaminated. ISOT in particular is
  separable by a dateline artifact (`WASHINGTON (Reuters) -` appears only in the real class),
  so near-100% ISOT scores mean very little.
- **The model over-flags science reporting.** Two genuine study write-ups in the corpus
  scored 0.98+. Worth knowing before a judge finds it.
- **The heuristic fallback's image scoring reads URL markers only.** It never inspects
  pixels, so its recall reflects marker coverage, not vision accuracy. It deliberately never
  flags an image it has not seen.

## Layout

```
backend/
  app/models/screening.py          Tier 2 contract (ScreeningInput / ScreeningResult)
  app/clients/screening_base.py    TextScorer / ImageScorer Protocols
  app/clients/heuristic_screening.py  offline fallbacks
  app/clients/hf_loader.py         cached, locked model loading + label resolution
  app/clients/hf_text.py           real BERT backend
  app/clients/hf_image.py          real CNN backend
  app/services/text_classifier.py  heuristic text scoring (pure)
  app/services/image_detector.py   heuristic URL-provenance scoring (pure)
  app/services/image_fetch.py      bounded parallel image fetch + decode
  app/services/calibration.py      AUROC / ECE / Brier / Wilson
  app/pipeline/ws4.py              run_ws4(): the WS4 entry point
  ws4_eval.py                      threshold calibration + label verification
  ws4_bench.py                     latency measurement
  eval/heldout_dataset.json        60 labelled text + 18 image samples (synthetic)
```
