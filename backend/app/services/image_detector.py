"""Heuristic synthetic-image scoring — the offline fallback for Tier 2 image screening.

This is NOT the model the proposal promises. §2.2 specifies a CNN trained to separate
authentic photographs from GAN/diffusion output; that lives in `app/clients/hf_image.py`.

This fallback reads only *provenance markers* in the URL — generator names, known
synthetic-image hosts, known wire/stock hosts. It deliberately does NOT guess from pixels
it has never seen: an image with no marker scores `UNKNOWN_SCORE` and is never flagged. A
detector that invented scores for unseen images would manufacture exactly the false
positives §3.3 says the product dies on.

That also means its recall reflects marker coverage, not vision accuracy, and `ws4_eval.py`
says so wherever it reports a number from this backend.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.clients.screening_base import ImageRisk

# Generator/host markers that appear in the URL itself. Presence of one is real
# provenance evidence, not a guess.
GENERATOR_MARKERS = (
    "midjourney", "dall-e", "dalle", "stable-diffusion", "stablediffusion", "sdxl",
    "ai-generated", "aigenerated", "ai_generated", "generated-image", "gan-", "-gan",
    "leonardo.ai", "civitai", "thispersondoesnotexist", "generated.photos",
    "firefly-generated", "imagen-", "nightcafe", "artbreeder", "deepai", "flux-",
)
# Wire services and stock libraries: human-shot photography with editorial provenance.
PROVENANCE_HOSTS = (
    "reuters.com", "apimages.com", "gettyimages.com", "afp.com", "epa.eu",
    "shutterstock.com", "istockphoto.com", "bloomberg.com", "straitstimes.com",
    "channelnewsasia.com", "cna.asia", "gov.sg",
)

MARKER_SCORE = 0.92      # explicit generator marker in the URL
PROVENANCE_SCORE = 0.03  # wire/stock host
UNKNOWN_SCORE = 0.10     # no signal either way — must stay below any sane threshold
SKIPPED_SCORE = 0.0      # unusable URL: cannot be evidence of anything

BACKEND_NAME = "heuristic-image-v1"


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_fetchable(url: str) -> bool:
    """True for URLs the backend is willing to dereference. Non-http(s) schemes (data:,
    blob:, file:) are rejected before any network layer sees them."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def heuristic_image_score(url: str) -> ImageRisk:
    """Provenance-marker scoring. Deterministic; never inspects pixels."""
    if not is_fetchable(url):
        return ImageRisk(url, SKIPPED_SCORE, BACKEND_NAME, "skipped: not an http(s) image URL")

    low = url.lower()
    hit = next((m for m in GENERATOR_MARKERS if m in low), None)
    if hit:
        return ImageRisk(url, MARKER_SCORE, BACKEND_NAME, f"generator marker in URL: {hit!r}")

    host = host_of(url)
    if any(host == p or host.endswith("." + p) for p in PROVENANCE_HOSTS):
        return ImageRisk(url, PROVENANCE_SCORE, BACKEND_NAME, f"wire/stock provenance host: {host}")

    return ImageRisk(
        url, UNKNOWN_SCORE, BACKEND_NAME,
        "no provenance signal; heuristic mode does not inspect pixels",
    )
