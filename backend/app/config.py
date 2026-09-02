"""Runtime configuration for the backend (WS4 Tier 2 + WS5 Tier 3).

Everything defaults to the MOCK/heuristic backends so the whole pipeline runs with no API
keys. Set the env vars below to swap in real providers without touching pipeline code.

Environment is read in `get_settings()`, NOT in the dataclass field defaults. Defaults
evaluated in a class body are frozen at import time, which would make every env var
unsettable by tests (and by any future .env loader) once this module had been imported
once. The dataclass defaults below are the documented fallbacks; `get_settings()` overlays
the environment on top of them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value is not None else default


def _get_opt_str(name: str) -> str | None:
    return os.environ.get(name)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_opt_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Which backends to use. "mock" (default) needs no keys.
    llm_provider: str = "mock"        # mock | openai
    search_provider: str = "mock"     # mock | tavily

    # Credentials (only read by the real providers).
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    tavily_api_key: str | None = None

    # WS5 tuning knobs. These are the numbers to defend in the pitch.
    max_claims: int = 5                       # top-N load-bearing claims (proposal: 3–5)
    min_checkworthiness: float = 0.35
    evidence_per_claim: int = 3               # proposal "done when": >=3 sources/claim
    dedup_threshold: float = 0.85             # token-Jaccard above this = duplicate
    anchor_min_similarity: float = 0.5        # min Dice to anchor a rewritten claim

    # --- WS4 (Tier 2 screening) ------------------------------------------- #
    # auto        real checkpoints when the weights are available, heuristic otherwise
    # huggingface strict: refuse to run at all if the real models cannot load
    # heuristic   forced offline; what CI sets
    #
    # DEFAULTS TO "heuristic" ON EVIDENCE, not for convenience. Measured flag rate on 480
    # REAL news articles (AG News test split, 120 each from World/Sports/Business/Sci-Tech):
    #
    #   omykhailiv/bert-fake-news-recognition    83.1% of real news flagged as fake
    #   Pulk17/Fake-News-Detection               61.7%
    #   hamzab/roberta-fake-news-classification  55.2%
    #   heuristic-text-v1                         0.0%
    #
    # A gate that flags 83% of real news is not a gate — it escalates almost everything
    # and destroys the cost rationale for the whole cascade. Set DASFAX_SCREENING_MODE=auto
    # to run the real models anyway (for evaluation, or after fine-tuning one that works).
    #
    # This is NOT a claim that the heuristic is good. Its 0% false-positive rate is measured
    # on real articles; its recall is only measured against synthetic fakes written with the
    # markers it looks for, so that number is circular and its true recall is unknown.
    screening_mode: str = "heuristic"

    # Per-component overrides. The text and image halves of Tier 2 are in very different
    # states — the image CNN measurably works (AUROC 0.821) while no text checkpoint does —
    # so a single mode for both would force us to disable a working model to disable a
    # broken one. Empty means "use screening_mode".
    #
    # SHIPPED CONFIGURATION: text=heuristic, image=auto. That is the best available
    # combination of the two, and each half is set from its own evidence.
    text_mode: str = ""      # heuristic | auto | huggingface  (default: screening_mode)
    image_mode: str = "auto"  # heuristic | auto | huggingface

    # Operating thresholds. Measured, not guessed:
    #
    #   python ws4_eval.py --backend huggingface
    #
    # Tier 2 routes rather than judges, so it is tuned RECALL-FIRST: a miss here means the
    # article is never checked and the reader sees nothing, while a false alarm only costs
    # Tier 3 compute and never renders a wrong badge. "Don't cry wolf" (§3.3) binds at
    # Tier 3 / WS6, where a status is actually shown.
    #
    # 0.40 rather than a higher value because the corpus is split into easy cases (obvious
    # clickbait) and hard ones (real journalism that reads as sensational; misinformation
    # written in a calm register). Selecting on the aggregate picks 0.85; selecting on the
    # hard subset — which is what real browsing looks like — picks 0.40:
    #
    #   threshold   hard-subset recall   overall FPR
    #   0.40        0.875                0.233
    #   0.65        0.625                0.200
    #   0.85        0.625                0.133
    #
    # Two more of eight hard fakes caught, for 3.3pp more false escalation. The aggregate
    # hides this because every easy case is caught at any threshold.
    text_threshold: float = 0.40
    # Image threshold, measured against the real CNN on 66 Wikimedia images:
    #
    #   python ws4_eval.py --images
    #   overall AUROC 0.821 | at 0.70: recall 0.722, precision 0.812, FPR 0.200
    #
    # 0.70 sits in the middle of a plateau: recall is flat at 0.722 from 0.30 to 0.80 and
    # FPR is flat at 0.200 from 0.60 to 0.95, so anything in 0.60-0.80 behaves identically.
    # Dropping to 0.25 buys 2.8pp recall for 10pp more false alarms — a bad trade.
    #
    # TWO THINGS TO KNOW, neither fixable by moving this number:
    #  1. Six of thirty genuine photographs score above 0.95. The false-positive rate is
    #     not a threshold problem; it is the model. Because escalation is OR (per §2.2),
    #     roughly one page in five carrying real photos escalates on the image signal
    #     alone. That costs Tier 3 compute, not user trust — images never render a badge.
    #  2. Recall never reaches 0.90 at ANY threshold, so the recall-first criterion used
    #     for text cannot be satisfied here at all.
    image_threshold: float = 0.70

    # Real model checkpoints (read unless screening_mode="heuristic").
    bert_model_name: str = "omykhailiv/bert-fake-news-recognition"
    image_model_name: str = "Organika/sdxl-detector"
    # Pin a commit SHA so a confusion matrix stays reproducible when the checkpoint moves.
    bert_revision: str | None = None
    image_revision: str | None = None

    # Index of the "fake" class for the text checkpoint.
    #
    # ESTABLISHED EMPIRICALLY, not read off the model card. omykhailiv/bert-fake-news-
    # recognition ships no `id2label`, so transformers synthesises LABEL_0/LABEL_1 and
    # there is nothing in the artifact to read the mapping off. Guessing wrong silently
    # inverts the whole tier — fake articles would score as safe, with no error anywhere.
    #
    #   python ws4_eval.py --verify-labels
    #   -> index 0 == fake: AUROC 1.0000, vs 0.0000 for index 1.
    #
    # Re-run that after ANY change of checkpoint or revision. A different checkpoint that
    # ships a real id2label overrides this value (see resolve_fake_index); one that ships
    # neither, with this set to None, degrades to the heuristic rather than picking a side.
    bert_fake_label_index: int | None = 0

    # Latency budget hierarchy. Every value is a deadline, not a hope: work that overruns
    # is abandoned and reported as partial rather than being waited on.
    #
    # DERIVED FROM MEASUREMENT (`python ws4_bench.py --with-images`), on
    # arm64 / Darwin 25.4 / python 3.14, single user, 3 images per page:
    #
    #   heuristic fallback           p95    0.2 ms
    #   real models, text only       p95   17.5 ms
    #   real models, with images     p95  549.3 ms   <- the binding case
    #
    # The image path is NETWORK-dominated: a single fetch+decode runs 385-480 ms, and the
    # three run in parallel. That is why the budget is 750 ms and not the 300 ms this
    # started with. 300 ms was picked before any model ran, and at that value the image
    # task hit its deadline on every single request — images fail closed, so the image
    # half of Tier 2 contributed nothing while the response still read "nothing flagged".
    # A budget that quietly disables a model is worse than a slow one.
    #
    # Tier 2 runs in the background while the user reads, so ~0.5s is not user-visible
    # latency. Re-run ws4_bench.py on the demo machine before quoting any figure.
    screen_budget_ms: float = 750.0        # global wall-clock deadline for one screen
    text_budget_ms: float = 250.0          # text model alone (p95 17.5 ms — a guard, not a constraint)
    image_budget_ms: float = 700.0         # fetch + inference for the whole image batch
    image_fetch_timeout_s: float = 4.0     # per-request socket timeout
    max_images_screened: int = 6           # a news page can carry 40+ images, mostly chrome

    # Model lifecycle.
    warmup: bool = True                    # load + one dummy forward pass at startup
    allow_model_download: bool = True      # False = only use weights already in the HF cache


def effective_text_mode(s: Settings) -> str:
    """Mode for the text half; falls back to screening_mode when unset."""
    return s.text_mode or s.screening_mode


def effective_image_mode(s: Settings) -> str:
    """Mode for the image half; falls back to screening_mode when unset."""
    return s.image_mode or s.screening_mode


def get_settings() -> Settings:
    """Build Settings from the current environment. Cheap; call it per request."""
    return Settings(
        llm_provider=_get_str("DASFAX_LLM_PROVIDER", "mock"),
        search_provider=_get_str("DASFAX_SEARCH_PROVIDER", "mock"),
        openai_api_key=_get_opt_str("OPENAI_API_KEY"),
        openai_model=_get_str("DASFAX_OPENAI_MODEL", "gpt-4o-mini"),
        tavily_api_key=_get_opt_str("TAVILY_API_KEY"),
        max_claims=_get_int("DASFAX_MAX_CLAIMS", 5),
        min_checkworthiness=_get_float("DASFAX_MIN_CHECKWORTHINESS", 0.35),
        evidence_per_claim=_get_int("DASFAX_EVIDENCE_PER_CLAIM", 3),
        dedup_threshold=_get_float("DASFAX_DEDUP_THRESHOLD", 0.85),
        anchor_min_similarity=_get_float("DASFAX_ANCHOR_MIN_SIMILARITY", 0.5),
        screening_mode=_get_str("DASFAX_SCREENING_MODE", "heuristic"),
        text_mode=_get_str("DASFAX_TEXT_MODE", ""),
        image_mode=_get_str("DASFAX_IMAGE_MODE", "auto"),
        text_threshold=_get_float("DASFAX_TEXT_THRESHOLD", 0.40),
        image_threshold=_get_float("DASFAX_IMAGE_THRESHOLD", 0.70),
        bert_model_name=_get_str("DASFAX_BERT_MODEL_NAME", "omykhailiv/bert-fake-news-recognition"),
        image_model_name=_get_str("DASFAX_IMAGE_MODEL_NAME", "Organika/sdxl-detector"),
        bert_revision=_get_opt_str("DASFAX_BERT_REVISION"),
        image_revision=_get_opt_str("DASFAX_IMAGE_REVISION"),
        bert_fake_label_index=(
            _get_opt_int("DASFAX_BERT_FAKE_LABEL_INDEX")
            if "DASFAX_BERT_FAKE_LABEL_INDEX" in os.environ
            else 0
        ),
        screen_budget_ms=_get_float("DASFAX_SCREEN_BUDGET_MS", 750.0),
        text_budget_ms=_get_float("DASFAX_TEXT_BUDGET_MS", 250.0),
        image_budget_ms=_get_float("DASFAX_IMAGE_BUDGET_MS", 700.0),
        image_fetch_timeout_s=_get_float("DASFAX_IMAGE_FETCH_TIMEOUT_S", 4.0),
        max_images_screened=_get_int("DASFAX_MAX_IMAGES_SCREENED", 6),
        warmup=_get_bool("DASFAX_WARMUP", True),
        allow_model_download=_get_bool("DASFAX_ALLOW_MODEL_DOWNLOAD", True),
    )
