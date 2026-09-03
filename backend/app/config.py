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

from dotenv import find_dotenv, load_dotenv

# Load the project's .env into os.environ ONCE, at import, before anything below reads a
# variable. Without this the file is inert on the native path: get_settings() reads
# os.environ directly, so `python -m uvicorn app.main:app` saw only what the shell had
# already exported and silently served the mock providers no matter what .env said.
#
# find_dotenv(usecwd=True) walks UP from the current working directory, so the same
# command works whether the server is launched from backend/ (the documented way, where
# the file is one level up) or from the repo root.
#
# override=False (the default, stated explicitly because it is load-bearing): a variable
# already present in the process environment WINS over the file. An explicit
# `$env:DASFAX_LLM_PROVIDER = "mock"` in the shell, docker-compose's env_file injection,
# or a test's monkeypatch.setenv must not be silently overwritten by a stale .env.
#
# Absent file: find_dotenv returns "" and load_dotenv is a no-op, so a clean clone with no
# .env still starts normally on the documented mock defaults.
load_dotenv(find_dotenv(usecwd=True), override=False)


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


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# The OpenAI-compatible endpoint used when DASFAX_OPENAI_BASE_URL is unset. Any provider
# that speaks the same /chat/completions shape (Gemini, Groq, ...) is a base-URL swap.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# Default model for the NATIVE anthropic providers (read + verify + search). Set
# DASFAX_ANTHROPIC_MODEL to a model your key actually has access to.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

# Anthropic's web_search is a dated SERVER TOOL and keys differ on which versions they
# accept. The SDK also ships web_search_20260209 / web_search_20260318; this is the
# long-standing one, overridable with DASFAX_ANTHROPIC_WEB_SEARCH_TOOL.
DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20250305"


@dataclass(frozen=True)
class Settings:
    # Which backends to use. "mock" (default) needs no keys.
    llm_provider: str = "mock"        # mock | openai | anthropic
    search_provider: str = "mock"     # mock | tavily | anthropic
    assessor_provider: str = "mock"   # mock | openai | anthropic

    # Demo/presentation only. OFF by default; changes nothing about the normal mock run.
    # See app/clients/mock_search.py for exactly what it does and why.
    mock_demo: bool = False

    # Credentials (only read by the real providers).
    # "openai" here means the OpenAI WIRE FORMAT, not the vendor: point base_url at
    # Gemini/Groq/vLLM and the same client works with only these two vars changed.
    openai_api_key: str | None = None
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    openai_model: str = "gpt-4o-mini"
    tavily_api_key: str | None = None

    # NATIVE Anthropic (Messages API), used by all three "anthropic" providers. One key
    # covers read + verify + search, because Claude's web_search is a built-in server tool
    # — no OpenAI key and no Tavily key are read on that path.
    anthropic_api_key: str | None = None
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    anthropic_web_search_tool: str = DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL

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
    # REAL news HEADLINE SNIPPETS (AG News test split, 120 each from World/Sports/Business/
    # Sci-Tech). NOTE THE UNIT: AG News items are a headline plus one sentence, 38 words
    # median -- they are NOT articles, and this file described them as articles until it
    # was checked. Deployment is 400-800 word articles; see the article-length table below,
    # where several models behave completely differently.
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
    text_mode: str = "auto"   # heuristic | auto | huggingface  ("" = use screening_mode)
    image_mode: str = "auto"  # heuristic | auto | huggingface

    # Operating thresholds. Each backend carries its OWN calibrated threshold, because
    # their score distributions are nothing alike — applying the model's threshold to the
    # heuristic would leave the fallback almost inert, and vice versa. The scorer decides
    # which one applies (see app/clients/*_screening.py and hf_text.py).
    #
    # Fine-tuned model (models/dasfax-tier2-text), swept on 480 AG News snippets (38w):
    #
    #   thresh   real-news FPR   all-fake recall   hard-fake recall   chilli article
    #    0.400        50.8%           76.7%             12.5%          FLAGGED
    #    0.900        34.2%           73.3%              0.0%          FLAGGED
    #    0.990        17.5%           73.3%              0.0%          ok
    #    0.995        10.6%           73.3%              0.0%          ok   <- shipped
    #    0.999         0.0%            0.0%              0.0%          ok   (inert)
    #
    # 0.995 is the best this model offers: below it the false-positive rate climbs with no
    # recall gain, above it the model stops firing entirely.
    #
    # BE CLEAR-EYED ABOUT WHAT THIS COSTS. The heuristic reaches the same 73.3% recall at
    # 0.0% false positives. Running the model as the gate trades 10.6pp of false escalation
    # for no measured recall gain; both score 0% on the hard subset. It is active because
    # the proposal specifies a BERT classifier and the team chose fidelity to that;
    # DASFAX_TEXT_MODE=heuristic reverses it in one variable.
    # FALSE-POSITIVE RATE BY DOCUMENT LENGTH, at a threshold giving 40% recall on
    # PolitiFact-false statements (LIAR2 test). This is the table that matters for
    # deployment, and it was missing until AG News was found to be 38-word snippets:
    #
    #   model                     AG News (38w)   BBC articles (433w)
    #   dhruvpal+LIAR2 fine-tune       29.4%           62.7%     <- best on statements
    #   dhruvpal/fake-news-bert        58.5%           72.0%
    #   ours (WELFake/BERT)            44.8%           27.7%     <- best on ARTICLES
    #   heuristic-text-v1              10.2%            1.2%     (but AUROC 0.532 = chance)
    #
    # The two columns rank the models DIFFERENTLY. A model trained on short claims fires
    # indiscriminately on long text and vice versa, so any FPR quoted without its document
    # length is meaningless. Deployment is the right-hand column.
    text_threshold: float = 0.995
    # The heuristic's own operating point, unchanged and separately calibrated.
    heuristic_text_threshold: float = 0.40
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
    # Our fine-tune (train_ws4.py). Weights are NOT in the repo — 438MB, past GitHub's
    # limit — so a fresh clone degrades to the heuristic with a message naming the command
    # that produces them. train_ws4.py rebuilds an equivalent model in ~27 min, but NOT a
    # bit-identical one: it does not set torch.use_deterministic_algorithms, and MPS/CUDA/CPU
    # disagree in the last places. At a 0.995 operating point that can move decisions, so the
    # figures below describe THESE weights. Copy the folder rather than retrain if you need
    # them to hold. See README.md 'Tier 2 model weights'.
    bert_model_name: str = "models/dasfax-tier2-text"
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
        assessor_provider=_get_str("DASFAX_ASSESSOR_PROVIDER", "mock"),
        mock_demo=_get_bool("DASFAX_MOCK_DEMO", False),
        openai_api_key=_get_opt_str("OPENAI_API_KEY"),
        openai_base_url=_get_str("DASFAX_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        openai_model=_get_str("DASFAX_OPENAI_MODEL", "gpt-4o-mini"),
        tavily_api_key=_get_opt_str("TAVILY_API_KEY"),
        anthropic_api_key=_get_opt_str("ANTHROPIC_API_KEY"),
        anthropic_model=_get_str("DASFAX_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
        anthropic_web_search_tool=_get_str(
            "DASFAX_ANTHROPIC_WEB_SEARCH_TOOL", DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL
        ),
        max_claims=_get_int("DASFAX_MAX_CLAIMS", 5),
        min_checkworthiness=_get_float("DASFAX_MIN_CHECKWORTHINESS", 0.35),
        evidence_per_claim=_get_int("DASFAX_EVIDENCE_PER_CLAIM", 3),
        dedup_threshold=_get_float("DASFAX_DEDUP_THRESHOLD", 0.85),
        anchor_min_similarity=_get_float("DASFAX_ANCHOR_MIN_SIMILARITY", 0.5),
        screening_mode=_get_str("DASFAX_SCREENING_MODE", "heuristic"),
        text_mode=_get_str("DASFAX_TEXT_MODE", "auto"),
        image_mode=_get_str("DASFAX_IMAGE_MODE", "auto"),
        text_threshold=_get_float("DASFAX_TEXT_THRESHOLD", 0.995),
        heuristic_text_threshold=_get_float("DASFAX_HEURISTIC_TEXT_THRESHOLD", 0.40),
        image_threshold=_get_float("DASFAX_IMAGE_THRESHOLD", 0.70),
        bert_model_name=_get_str("DASFAX_BERT_MODEL_NAME", "models/dasfax-tier2-text"),
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
