"""In-memory, TTL'd cache for assembled AnalysisResponses, keyed on a hash of
(url, text).

Deliberately the smallest thing that could work: no Redis, no new dependency, no
persistence across process restarts, no cross-process sharing across multiple uvicorn
workers -- all explicitly out of scope for this task, and all real limitations worth
knowing about rather than hiding. The interface (`get`/`set`) is the only thing
anything else in the orchestrator depends on, so swapping this for a Redis- or
SQLite-backed cache later is a one-file change behind those two methods.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


def cache_key(url: str, text: str) -> str:
    """sha256 of (url, text). NUL-separated so ("ab", "c") and ("a", "bc") can't
    collide the way plain string concatenation would risk."""
    return hashlib.sha256(f"{url}\x00{text}".encode("utf-8")).hexdigest()


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class InMemoryTTLCache(Generic[T]):
    """Not thread-safe beyond what CPython's GIL gives plain dict ops for free -- fine
    here because nothing in this codebase runs Tier 3 concurrently against the same
    cache from multiple real OS threads today. `time.monotonic()` on purpose (not
    `time.time()`): TTL expiry must not be perturbed by wall-clock/NTP adjustments."""

    def __init__(self, *, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._store: dict[str, _Entry[T]] = {}

    def get(self, key: str) -> T | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: T) -> None:
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl_s)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
