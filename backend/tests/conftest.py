"""Shared test fixtures.

The autouse fixture below pins every test to the offline heuristic backends regardless of
what the developer happens to have exported in their shell. Without it, whoever has
DASFAX_SCREENING_MODE=huggingface set for a demo would silently start downloading model
weights during `pytest`, and the "16 tests, all offline" property would quietly die.

Tests that WANT the real models are marked `@pytest.mark.models` and deselected by
default (see pyproject.toml); they opt back in by overriding these vars themselves.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_screening(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("models"):
        return
    monkeypatch.setenv("DASFAX_SCREENING_MODE", "heuristic")
    # Both halves explicitly: the image component defaults to "auto" in the shipped
    # configuration (the CNN works; no text checkpoint does), so pinning only the global
    # mode would leave tests downloading weights and hitting the network.
    monkeypatch.setenv("DASFAX_TEXT_MODE", "heuristic")
    monkeypatch.setenv("DASFAX_IMAGE_MODE", "heuristic")
    monkeypatch.setenv("DASFAX_ALLOW_MODEL_DOWNLOAD", "0")
    monkeypatch.setenv("DASFAX_WARMUP", "0")
    # Tier 2 SHIPS ON, but this suite runs the offline heuristic backends, and the
    # heuristic is measured at 89.8% false-negative rate and fires on 0 of 600 real
    # articles (backend/WS4.md) — it clears essentially everything. Leaving the gate on
    # here would silently make every Tier 3 test depend on the quirks of a backend that
    # never runs in production: they would pass or fail on whether a regex happened to
    # escalate their fixture. Tests that actually exercise the gate opt back in and
    # inject their own screener — see tests/test_orchestrator_tier2.py.
    monkeypatch.setenv("DASFAX_TIER2_ENABLED", "0")
