"""WS4 unit tests: label resolution, calibration maths, fetch hygiene, import hygiene."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.clients.hf_loader import positive_probability, resolve_fake_index
from app.services.calibration import (
    auroc,
    brier,
    ece,
    is_bimodal,
    wilson_interval,
)
from app.services.image_detector import heuristic_image_score, is_fetchable
from app.services.text_classifier import heuristic_score

BACKEND = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Defect 4 — the label mapping must never be guessed.
# --------------------------------------------------------------------------- #
def test_id2label_is_used_when_the_checkpoint_ships_one():
    index, provenance = resolve_fake_index({0: "artificial", 1: "human"}, None)
    assert (index, provenance) == (0, "id2label")


def test_configured_index_is_used_when_there_is_no_label_map():
    # This is the omykhailiv case: no id2label, so the empirically verified index applies.
    assert resolve_fake_index(None, 0) == (0, "configured")


def test_absent_map_and_absent_config_is_unresolved():
    index, provenance = resolve_fake_index(None, None)
    assert index is None
    assert "unresolved" in provenance


def test_label_map_naming_no_known_class_is_unresolved_not_a_fallback_guess():
    # A map exists but uses a vocabulary we don't recognise. That is evidence our
    # assumptions are wrong here, so falling back to the configured index would be
    # guessing with extra steps.
    index, provenance = resolve_fake_index({0: "class_a", 1: "class_b"}, 0)
    assert index is None
    assert "unresolved" in provenance


def test_id2label_overrides_a_conflicting_configured_index():
    index, provenance = resolve_fake_index({0: "human", 1: "artificial"}, 0)
    assert index == 1
    assert "overrides configured" in provenance


def test_positive_probability_does_not_invert_on_an_unknown_scheme():
    """The original fallback returned `1 - max(score)`, turning a confident fake into a
    confident pass. Unrecognised must mean None so the caller can degrade."""
    predictions = [{"label": "totally_unknown", "score": 0.9}, {"label": "other", "score": 0.1}]
    assert positive_probability(predictions, 0, None) is None


def test_positive_probability_reads_the_requested_index():
    predictions = [{"label": "LABEL_0", "score": 0.83}, {"label": "LABEL_1", "score": 0.17}]
    assert positive_probability(predictions, 0, None) == pytest.approx(0.83)
    assert positive_probability(predictions, 1, None) == pytest.approx(0.17)


def test_positive_probability_handles_nested_pipeline_output():
    assert positive_probability([[{"label": "fake", "score": 0.7}]], 0, None) == pytest.approx(0.7)


# --------------------------------------------------------------------------- #
# Calibration maths
# --------------------------------------------------------------------------- #
def test_auroc_perfect_separation():
    assert auroc([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) == 1.0


def test_auroc_is_inverted_for_a_flipped_label_mapping():
    # This is exactly the signal --verify-labels relies on: the two candidate mappings
    # produce AUROCs that sum to 1.
    scores, labels = [0.1, 0.2, 0.8, 0.9], [False, False, True, True]
    flipped = [1 - s for s in scores]
    assert auroc(scores, labels) + auroc(flipped, labels) == pytest.approx(1.0)


def test_auroc_with_all_ties_is_chance():
    assert auroc([0.5] * 6, [True, False] * 3) == pytest.approx(0.5)


def test_auroc_on_a_single_class_returns_chance_not_a_perfect_score():
    assert auroc([0.1, 0.9], [True, True]) == 0.5


def test_auroc_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        auroc([0.1, 0.2], [True])


def test_wilson_interval_is_wide_on_small_samples():
    low, high = wilson_interval(18, 20)
    assert 0.0 < low < 0.9 and high <= 1.0
    assert high - low > 0.15, "an interval this narrow on n=20 would be misleading"


def test_wilson_interval_does_not_collapse_at_the_extremes():
    # The normal approximation claims a zero-width interval for k == n. Wilson must not.
    low, high = wilson_interval(20, 20)
    assert low < 1.0 and high == pytest.approx(1.0)


def test_wilson_interval_on_no_data_is_maximally_uncertain():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_brier_and_ece_reward_calibrated_confidence():
    labels = [True, True, False, False]
    confident_right = [0.99, 0.99, 0.01, 0.01]
    confident_wrong = [0.01, 0.01, 0.99, 0.99]
    assert brier(confident_right, labels) < brier(confident_wrong, labels)
    assert ece(confident_right, labels) < ece(confident_wrong, labels)


def test_is_bimodal_detects_the_uncalibrated_signature():
    assert is_bimodal([0.001, 0.002, 0.999, 0.998, 0.99, 0.01]) is True
    assert is_bimodal([0.4, 0.45, 0.5, 0.55, 0.6, 0.5]) is False


# --------------------------------------------------------------------------- #
# Fetch hygiene
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    ["data:image/png;base64,iVBOR", "file:///etc/passwd", "javascript:alert(1)", "", "http://"],
)
def test_non_http_urls_are_not_fetchable(url):
    assert is_fetchable(url) is False


def test_https_urls_are_fetchable():
    assert is_fetchable("https://cdn.example.org/a.jpg") is True


def test_unusable_url_is_skipped_not_scored():
    risk = heuristic_image_score("data:image/png;base64,iVBOR")
    assert risk.score == 0.0
    assert "skipped" in risk.reason


def test_unknown_image_is_never_flagged_by_the_heuristic():
    """The fallback must not invent a score for pixels it never saw."""
    risk = heuristic_image_score("https://cdn.example.org/photos/unknown.jpg")
    assert risk.score < 0.7
    assert "does not inspect pixels" in risk.reason


# --------------------------------------------------------------------------- #
# Heuristic behaviour
# --------------------------------------------------------------------------- #
def test_heuristic_is_deterministic():
    text = "Some ordinary article text that is long enough to be scored properly. " * 3
    assert heuristic_score("A title", text) == heuristic_score("A title", text)


def test_short_text_returns_the_neutral_prior_not_a_confident_score():
    score, features = heuristic_score(None, "Too short to judge.")
    assert score == 0.20
    assert all(v == 0.0 for v in features.values())


def test_acronym_dense_local_news_is_not_read_as_shouting():
    """Singapore news is full of legitimate three-letter acronyms. A generic ALL-CAPS
    density feature scores this as shouting; the inclusion-list feature must not."""
    text = (
        "MOH and NEA said the HDB and CPF schemes would be reviewed. According to MOM, "
        "the MRT and COE figures were published by LTA and IRAS this week, and GST "
        "changes announced by MOF take effect in April. The IMDA and CSA also commented."
    )
    score, features = heuristic_score("MOH, NEA and HDB review CPF and COE rules", text)
    assert features["shouting"] == 0.0, "acronyms must not register as shouting"
    assert score < 0.65, f"ordinary acronym-dense local reporting scored {score}"


# --------------------------------------------------------------------------- #
# Import hygiene — a circular import that only fires on one import order.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "module",
    [
        "app.services.image_detector",
        "app.services.text_classifier",
        "app.services.image_fetch",
        "app.clients.factory",
        "app.clients.heuristic_screening",
        "app.pipeline.ws4",
        "app.main",
    ],
)
def test_every_module_imports_standalone(module):
    """Regression guard for a real circular import.

    `app/clients/__init__.py` eagerly imports the factory, so a factory that imports
    app.services at module level closes a loop through app.services -> app.clients. It
    stayed invisible in the test suite because importing app.main first happens to
    resolve the modules in a working order; `ws4_eval.py` imported them in the other
    order and crashed. Each module must therefore import as the FIRST thing in a fresh
    interpreter.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{module} failed to import standalone:\n{result.stderr}"
