"""Bounded, parallel image fetching for Tier 2 image screening.

Images come from arbitrary third-party pages, so every fetch is treated as hostile input
(proposal §2.5): http(s) only, a per-socket timeout, a hard byte cap, a content-type check,
and an absolute wall-clock deadline across the whole batch.

Fetching is parallel because it is the dominant cost of Tier 2. Six images fetched one
after another at a 4s timeout is a 24-second worst case — longer than any reader will wait
and far outside any budget worth stating.

On the deadline: a thread that has already started cannot be killed. The per-socket timeout
bounds how long an abandoned fetch can linger, and the pool is shared and bounded so
stragglers cannot accumulate without limit, but `fetch_images` returning does NOT guarantee
every worker has stopped. That is the honest caveat of a thread-based deadline.
"""
from __future__ import annotations

import io
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

from app.clients.screening_base import FetchedImage
from app.services.image_detector import is_fetchable

# Some hosts refuse unrecognised agents outright — Wikimedia returns HTTP 400 without a
# contact URL, and several news CDNs block non-browser agents. Overridable so an operator
# can comply with a specific host's policy without a code change.
_USER_AGENT = os.environ.get(
    "DASFAX_IMAGE_USER_AGENT", "Dasfax-WS4/0.1 (Tier 2 screening; contact: set DASFAX_IMAGE_USER_AGENT)"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "image/*,*/*;q=0.8"}

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _build_ssl_context() -> ssl.SSLContext:
    """TLS context with an explicit CA bundle.

    `urlopen` with no context uses OpenSSL's compiled-in CA paths, which on a python.org
    macOS build point at a directory that does not exist until someone runs
    "Install Certificates.command". The result is that EVERY https image fetch fails with
    CERTIFICATE_VERIFY_FAILED — and because a failed fetch is (correctly) fail-closed, the
    whole image half of Tier 2 goes dark while the response still reads "nothing flagged".
    Pinning certifi's bundle makes the behaviour identical on a laptop and in Docker.
    """
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()

# Decode target. Both candidate image checkpoints take 224x224 input, so decoding a 4000px
# hero image at full resolution is pure waste — and it is real waste: full-size JPEG decode
# runs 100ms+, which is a large share of the entire tier's budget.
_DECODE_HINT = (448, 448)
_MAX_EDGE = 512

# SVG is markup, not a photograph: nothing to detect, and an XML parser is an attack
# surface we have no reason to expose.
_REJECTED_TYPES = ("image/svg+xml",)


def _decode(body: bytes) -> object:
    """Decode bytes to a downscaled RGB image. Raises if Pillow is missing or the bytes
    are not a valid image."""
    from PIL import Image  # noqa: PLC0415 — optional dependency, only on the real path

    image = Image.open(io.BytesIO(body))
    # draft() picks a DCT-scaled JPEG decode, which is close to free compared with
    # decoding at full size and then resampling.
    try:
        image.draft("RGB", _DECODE_HINT)
    except (AttributeError, ValueError):
        pass  # not a format that supports draft (PNG, WebP); decode normally
    image = image.convert("RGB")
    image.thumbnail((_MAX_EDGE, _MAX_EDGE))
    return image


def fetch_one(url: str, *, timeout_s: float, max_bytes: int) -> FetchedImage:
    """Fetch and decode a single image. Never raises."""
    started = time.perf_counter()

    def done(image: object | None, error: str | None, size: int = 0) -> FetchedImage:
        return FetchedImage(
            url=url,
            image=image,
            error=error,
            bytes_read=size,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    if not is_fetchable(url):
        return done(None, "not an http(s) URL")

    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(  # noqa: S310 — scheme checked above
            request, timeout=timeout_s, context=_SSL_CONTEXT
        ) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type in _REJECTED_TYPES:
                return done(None, f"rejected content-type {content_type}")
            if content_type and not content_type.startswith("image/"):
                return done(None, f"not an image ({content_type})")

            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                return done(None, f"declared size {declared} exceeds cap")

            # Read one byte past the cap so an undeclared oversize body is still caught.
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return done(None, f"fetch failed: HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # Name the underlying cause. "URLError" alone hides whether this was a dead host,
        # a timeout, or a TLS trust-store misconfiguration that breaks every fetch at once.
        cause = getattr(exc, "reason", None)
        detail = cause.__class__.__name__ if isinstance(cause, Exception) else exc.__class__.__name__
        return done(None, f"fetch failed: {detail}")

    if len(body) > max_bytes:
        return done(None, f"body exceeds {max_bytes} byte cap", len(body))

    try:
        return done(_decode(body), None, len(body))
    except ImportError:
        return done(None, "Pillow not installed", len(body))
    except Exception as exc:
        return done(None, f"decode failed: {exc.__class__.__name__}", len(body))


def fetch_images(
    urls: Sequence[str],
    *,
    deadline: float,
    per_request_timeout_s: float,
    max_bytes: int,
    pool: ThreadPoolExecutor,
    clock=time.monotonic,
) -> list[FetchedImage]:
    """Fetch every URL in parallel, abandoning whatever has not finished by `deadline`.

    `deadline` is an absolute `clock()` value. Order matches `urls`. Unfinished entries
    come back with `error="deadline exceeded"` rather than being waited on, so one slow
    CDN cannot hold the whole tier open.
    """
    if not urls:
        return []

    futures: list[Future[FetchedImage]] = [
        pool.submit(fetch_one, url, timeout_s=per_request_timeout_s, max_bytes=max_bytes)
        for url in urls
    ]
    remaining = deadline - clock()
    if remaining > 0:
        futures_wait(futures, timeout=remaining)

    results: list[FetchedImage] = []
    for url, future in zip(urls, futures, strict=True):
        if future.done() and not future.cancelled():
            results.append(future.result())
        else:
            future.cancel()  # only succeeds if it never started
            results.append(FetchedImage(url=url, error="deadline exceeded"))
    return results
