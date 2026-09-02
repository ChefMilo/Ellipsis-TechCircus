"""Tests for run_demo's backend wiring and its honesty disclaimer.

The demo script is what gets pointed at a projector, so two things about it are worth
pinning: it must run whatever provider is configured (not silently force the mock search
fixture), and its "RENDERING FIXTURE" warning must describe the run it actually did. A
warning that fires on real output teaches the audience to discount real output; a warning
that goes missing on fixture output is worse.
"""
from __future__ import annotations

import pytest

import run_demo
from app.clients.mock_search import MockSearchClient
from app.config import Settings

MOCK_LLM = "mock-llm-heuristic-v1"
MOCK_SEARCH = "mock-search-seeded-v1"
MOCK_ASSESSOR = "mock-assessor-stance-fixture-v1"
REAL_LLM = "anthropic:claude-sonnet-5"
REAL_SEARCH = "anthropic-web-search:claude-sonnet-5"
REAL_ASSESSOR = "anthropic-assessor:claude-sonnet-5"


def test_the_mock_clients_still_self_identify_as_mocks():
    # fixture_note keys off the "mock" name prefix, so the mocks must keep it.
    from app.clients.factory import make_assessor_client, make_llm_client, make_search_client

    settings = Settings()
    for client in (
        make_llm_client(settings),
        make_search_client(settings),
        make_assessor_client(settings),
    ):
        assert client.name.startswith("mock"), client.name


def test_note_names_every_mocked_role_on_a_full_fixture_run():
    note = run_demo.fixture_note(llm=MOCK_LLM, search=MOCK_SEARCH, assessor=MOCK_ASSESSOR)
    assert "LLM, search, assessor" in note
    assert "RENDERING FIXTURES" in note
    assert "Do not quote this as a result." in note


def test_note_names_only_the_mocked_role_on_a_mixed_run():
    note = run_demo.fixture_note(llm=REAL_LLM, search=REAL_SEARCH, assessor=MOCK_ASSESSOR)
    assert "the assessor is a labelled RENDERING FIXTURE" in note
    assert "LLM" not in note
    assert "search" not in note


def test_note_does_not_call_a_real_assessor_a_fixture():
    # The bug this replaces: the old line said the assessor was a fixture unconditionally.
    note = run_demo.fixture_note(llm=REAL_LLM, search=REAL_SEARCH, assessor=REAL_ASSESSOR)
    assert "FIXTURE" not in note
    assert "real providers" in note


@pytest.mark.parametrize(
    ("llm", "search", "assessor", "expected_role"),
    [
        (MOCK_LLM, REAL_SEARCH, REAL_ASSESSOR, "LLM"),
        (REAL_LLM, MOCK_SEARCH, REAL_ASSESSOR, "search"),
        (REAL_LLM, REAL_SEARCH, MOCK_ASSESSOR, "assessor"),
    ],
)
def test_a_single_mocked_role_is_always_disclosed(llm, search, assessor, expected_role):
    note = run_demo.fixture_note(llm=llm, search=search, assessor=assessor)
    assert f"the {expected_role} is a labelled RENDERING FIXTURE" in note


def test_demo_spread_forces_the_mock_search_fixture():
    # The flag is a presentation aid and must keep working regardless of configuration.
    forced = MockSearchClient(demo_spread=True)
    assert forced.name.startswith("mock")
    assert forced.name == "mock-search-demo-spread-v1"


def test_the_demo_builds_search_from_the_factory():
    # Regression: run_demo used to hardcode MockSearchClient, so a configured real search
    # provider was silently ignored and the demo always showed fabricated evidence.
    import inspect

    source = inspect.getsource(run_demo.main)
    assert "make_search_client()" in source
    assert "MockSearchClient(demo_spread=True) if args.demo_spread" in source
