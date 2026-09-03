"""Tests that need the real HuggingFace weights.

Deselected by default (see `addopts` in pyproject.toml). Run them with:

    pip install -r requirements-ml.txt
    pytest -m models

These encode WS4's acceptance criteria — "both models behind one /screen call, p95 inside a
stated budget" — plus the two failure modes that are silent in production: a flipped label
mapping, and a model reloaded per request.
"""
from __future__ import annotations

import statistics
import threading
import time

import pytest

from app.clients import hf_loader
from app.clients.factory import make_image_scorer, make_text_scorer
from app.config import Settings, get_settings
from app.models.screening import ScreeningInput
from app.pipeline.ws4 import run_ws4
from app.services.calibration import auroc

pytestmark = pytest.mark.models

REAL = (
    "The National Environment Agency said dengue cases fell to 214 in the week ending "
    "8 March, down from 287 the week before. According to the agency, 91 active clusters "
    "remain islandwide. A spokesperson said inspections of about 12,000 premises in "
    "February found mosquito breeding at 380 of them. Residents were advised to check "
    "flower pot plates weekly."
)
FAKE = (
    "SHOCKING: They don't want you to know what's really going on. Sources say a secret "
    "report has been buried for months and insiders claim officials knew ALL ALONG. "
    "Everyone knows the mainstream media will never touch this story. Allegedly the "
    "levels are 100% higher than they admit. Share this before it's deleted!! They are "
    "already censoring posts about it!!!"
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(**{**get_settings().__dict__, "screening_mode": "huggingface"})


@pytest.fixture(scope="module")
def text_scorer(settings):
    scorer = make_text_scorer(settings, strict=True)
    scorer.warmup()
    return scorer


def test_the_real_text_model_actually_loads(text_scorer):
    assert text_scorer.name.startswith("hf:"), "strict mode must never return the fallback"


def test_fake_scores_above_real(text_scorer):
    """The single most important behavioural check: if the label mapping were inverted,
    this is the assertion that catches it."""
    assert text_scorer.score(title=None, text=FAKE).score > text_scorer.score(title=None, text=REAL).score


def test_verified_label_index_still_wins_on_auroc(settings):
    """Re-derives the fake-class index empirically, the way ws4_eval.py --verify-labels
    does, and asserts the configured value still matches. A checkpoint or revision change
    that flips the mapping is otherwise completely silent."""
    from app.clients.hf_loader import get_model, positive_probability

    loaded = get_model("text-classification", settings.bert_model_name, settings.bert_revision)
    labels = [True, True, False, False]
    texts = [FAKE, FAKE.replace("SHOCKING", "URGENT"), REAL, REAL.replace("dengue", "influenza")]

    per_index: dict[int, list[float]] = {0: [], 1: []}
    for text in texts:
        with loaded.lock:
            predictions = loaded.pipe(text, top_k=None)
        for index in (0, 1):
            score = positive_probability(predictions, index, loaded.id2label)
            per_index[index].append(0.5 if score is None else score)

    winner = max(per_index, key=lambda i: auroc(per_index[i], labels))
    assert winner == settings.bert_fake_label_index, (
        f"evidence says index {winner} is 'fake', config says "
        f"{settings.bert_fake_label_index} — the tier is inverted"
    )


def test_image_model_ships_a_real_label_map(settings):
    scorer = make_image_scorer(settings, strict=True)
    assert scorer.name.startswith("hf:")
    assert scorer.label_provenance == "id2label"
    assert scorer.needs_pixels is True


def test_model_is_loaded_once_under_concurrency(settings):
    """Ten threads racing on a cold cache must produce one load, not ten. Without
    double-checked locking each thread starts its own 2-10s load and the box runs out of
    memory under a burst."""
    hf_loader.clear_cache()
    loads = []
    original = hf_loader._build

    def counting_build(*args, **kwargs):
        loads.append(1)
        return original(*args, **kwargs)

    hf_loader._build = counting_build
    try:
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(hf_loader.get_model("text-classification", settings.bert_model_name))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        hf_loader._build = original

    assert len(loads) == 1, f"loaded {len(loads)} times under concurrency"
    assert all(r is results[0] for r in results), "threads got different pipeline objects"


def test_p95_latency_is_inside_the_stated_budget(settings):
    """WS4's acceptance criterion, as a test. Text path only — the image path is
    network-bound and would make this a test of someone's wifi."""
    page = ScreeningInput.model_validate(
        {"url": "https://news.example.org/a", "title": "Dengue cases fall", "text": REAL}
    )
    run_ws4(page, settings=settings)  # untimed warm-up

    latencies = []
    for _ in range(30):
        started = time.perf_counter()
        run_ws4(page, settings=settings)
        latencies.append((time.perf_counter() - started) * 1000)

    latencies.sort()
    p95 = latencies[int(0.95 * (len(latencies) - 1))]
    assert p95 <= settings.text_budget_ms, (
        f"text-path p95 {p95:.1f}ms exceeds DASFAX_TEXT_BUDGET_MS={settings.text_budget_ms:.0f}ms "
        f"(median {statistics.median(latencies):.1f}ms)"
    )


def test_end_to_end_screen_escalates_on_a_fake_article(settings):
    page = ScreeningInput.model_validate(
        {"url": "https://news.example.org/x", "title": "SHOCKING truth EXPOSED", "text": FAKE}
    )
    result = run_ws4(page, settings=settings)
    assert result.model_meta["text_backend"].startswith("hf:")
    assert result.escalate_to_tier3 is True
    assert result.text_scored is True


def test_end_to_end_screen_stops_on_a_real_article(settings):
    page = ScreeningInput.model_validate(
        {"url": "https://news.example.org/y", "title": "Dengue cases fall", "text": REAL}
    )
    result = run_ws4(page, settings=settings)
    assert result.escalate_to_tier3 is False
    assert result.text_score < settings.text_threshold
