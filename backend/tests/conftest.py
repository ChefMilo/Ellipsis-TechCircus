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
    monkeypatch.setenv("DASFAX_ALLOW_MODEL_DOWNLOAD", "0")
    monkeypatch.setenv("DASFAX_WARMUP", "0")
