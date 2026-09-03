"""WS4 Tier 2 contract tests — the seam between the extension and the backend.

Every test here is a regression guard for a defect that was live in the first WS4 pass and
would have been invisible in production: the failures are silent (dropped images, a whole
tier that never runs) rather than loud.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.models.screening import ScreeningInput, ScreeningResult

client = TestClient(app)

ARTICLE = (
    "The National Environment Agency said dengue cases fell to 214 in the week ending "
    "8 March, down from 287 the week before. According to the agency, 91 active clusters "
    "remain islandwide. A spokesperson said inspections of about 12,000 premises in "
    "February found mosquito breeding at 380 of them."
)
IMAGES = [
    "https://cdn.example.org/photos/one.jpg",
    "https://cdn.example.org/photos/two.jpg",
    "https://cdn.example.org/photos/three.jpg",
]


def _payload(**overrides) -> dict:
    body = {"url": "https://news.example.org/sg/dengue", "title": "Dengue cases fall", "text": ARTICLE}
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Defect 1 — the client sends `images`, the contract calls it `image_urls`.
# Pydantic drops unknown keys, so an unaliased field means every page in production
# screens image-clean and the image model never runs on anything.
# --------------------------------------------------------------------------- #
def test_client_images_key_is_accepted():
    r = client.post("/tier2/screen", json=_payload(images=IMAGES))
    assert r.status_code == 200, r.text
    assert r.json()["model_meta"]["images_available"] == 3


def test_both_image_key_spellings_agree():
    a = client.post("/tier2/screen", json=_payload(images=IMAGES)).json()
    b = client.post("/tier2/screen", json=_payload(image_urls=IMAGES)).json()
    assert a["image_results"] == b["image_results"]
    assert a["model_meta"]["images_available"] == b["model_meta"]["images_available"] == 3


def test_null_image_list_is_not_a_validation_error():
    # A JS worker marshalling `undefined` can put an explicit null on the wire.
    r = client.post("/tier2/screen", json=_payload(images=None))
    assert r.status_code == 200, r.text
    assert r.json()["image_results"] == []


def test_missing_image_key_defaults_to_empty():
    r = client.post("/tier2/screen", json=_payload())
    assert r.status_code == 200, r.text
    assert r.json()["max_image_score"] == 0.0


# --------------------------------------------------------------------------- #
# Defect 2 — bootstrap.ts sends `text: extracted?.bodyText`, which is undefined when
# Readability fails. Tier 2 must degrade to image-only screening, not 422.
# --------------------------------------------------------------------------- #
def test_page_without_text_screens_on_images_only():
    r = client.post("/tier2/screen", json={"url": "https://x.example.org/a", "images": IMAGES})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text_scored"] is False
    assert body["degraded"] is True
    assert body["text_score"] == 0.0
    assert body["text_flagged"] is False
    assert any("no extracted body text" in reason for reason in body["reasons"])


def test_blank_text_is_treated_as_absent_not_scored():
    r = client.post("/tier2/screen", json=_payload(text="   "))
    assert r.status_code == 200, r.text
    assert r.json()["text_scored"] is False


def test_text_present_is_actually_scored():
    r = client.post("/tier2/screen", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text_scored"] is True
    assert body["model_meta"]["text_backend"] != "not-run"


# --------------------------------------------------------------------------- #
# The Tier 3 contract must survive the PageEnvelope split unchanged.
# --------------------------------------------------------------------------- #
def test_tier3_still_rejects_blank_text():
    r = client.post("/tier3/claims", json={"url": "https://x.example.org", "text": "   "})
    assert r.status_code == 422


def test_tier3_still_requires_text():
    r = client.post("/tier3/claims", json={"url": "https://x.example.org"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Shape + config
# --------------------------------------------------------------------------- #
def test_screening_result_round_trips():
    body = client.post("/tier2/screen", json=_payload(images=IMAGES)).json()
    assert ScreeningResult.model_validate(body).model_dump()["url"] == body["url"]


def test_screening_input_serialises_under_the_canonical_name():
    # Accepting `images` on the way in must not change what WS3 sees on the way out.
    parsed = ScreeningInput.model_validate(_payload(images=IMAGES))
    assert parsed.model_dump()["image_urls"] == IMAGES


def test_escalation_is_the_or_of_both_signals():
    body = client.post(
        "/tier2/screen",
        json=_payload(images=["https://cdn.example.org/midjourney/fake-crowd.png"]),
    ).json()
    assert body["image_flagged"] is True
    assert body["escalate_to_tier3"] is True


def test_health_still_reports_ws5():
    # WS5's own test asserts this key; WS4 must not repurpose the health payload.
    assert client.get("/health").json()["ws"] == "WS5"


# --------------------------------------------------------------------------- #
# Defect 5 — Settings read env at import time, so monkeypatch could not reach it.
# --------------------------------------------------------------------------- #
def test_settings_reflect_the_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("DASFAX_TEXT_THRESHOLD", "0.42")
    assert get_settings().text_threshold == 0.42


def test_label_index_default_matches_the_verified_value():
    # Established by `ws4_eval.py --verify-labels`: index 0 == fake, AUROC 1.0000 vs
    # 0.0000. If someone changes this without re-running that check, the tier inverts
    # silently — fake articles would score as safe.
    assert Settings().bert_fake_label_index == 0
