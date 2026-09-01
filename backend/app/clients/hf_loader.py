"""Shared HuggingFace model loading for Tier 2.

Three jobs, all of which matter operationally rather than functionally:

  Caching        A cold BERT load is 2-10 seconds. Without a process-wide cache, every
                 request reloads the weights; with a cache but no lock, ten concurrent
                 first-requests each start their own load and the box runs out of memory.
                 Double-checked locking gives one load per (task, model, revision).

  Serialisation  transformers pipelines are not documented thread-safe, and concurrent
                 CPU inference on one model just oversubscribes cores and makes p95 worse.
                 Each pipeline gets its own inference lock, so text and image still overlap
                 with each other while neither is entered twice.

  Label maps     The loader returns `id2label` alongside the pipeline, because which class
                 index means "fake" cannot be assumed — see `resolve_fake_index`.

Models are loaded explicitly (tokenizer/processor + model) rather than by passing a name
to `pipeline()`, so `local_files_only` can be honoured and the config is inspectable.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

_CACHE: dict[tuple[str, str, str | None], LoadedModel] = {}
_CACHE_LOCK = threading.Lock()

# Label spellings that mean "this is the synthetic / fabricated class".
_POSITIVE_LABELS = {
    "fake", "false", "fake_news", "fakenews", "misinformation", "unreliable",
    "artificial", "ai", "ai-generated", "aigenerated", "synthetic", "generated",
}


@dataclass
class LoadedModel:
    """A ready pipeline plus everything needed to use it safely and honestly."""

    pipe: Any
    lock: threading.Lock
    id2label: dict[int, str] | None
    model_name: str
    revision: str | None


def resolve_fake_index(
    id2label: dict[int, str] | None,
    configured: int | None,
) -> tuple[int | None, str]:
    """Decide which class index means "fake"/"synthetic".

    Returns `(index, provenance)` where provenance is "id2label", "configured", or
    "unresolved". **Never guesses.** Getting this backwards inverts the entire tier —
    fake articles would score as safe — and the failure is completely silent, so an
    unresolved mapping must degrade to the fallback rather than pick a side.

    `id2label` wins when the checkpoint ships one, because artifact metadata beats an
    environment variable. Neither named BERT checkpoint ships one, which is exactly why
    `configured` exists and why it must be established empirically first
    (`ws4_eval.py --verify-labels`).
    """
    if id2label:
        for index, label in id2label.items():
            if str(label).strip().lower().replace(" ", "-") in _POSITIVE_LABELS:
                if configured is not None and int(index) != configured:
                    return int(index), f"id2label (overrides configured={configured})"
                return int(index), "id2label"
        # A map exists but names no recognisable positive class. Do not fall through to
        # the configured index: the map is evidence that our vocabulary is wrong here.
        return None, f"unresolved: id2label={id2label} names no known fake/synthetic class"

    if configured is not None:
        return configured, "configured"

    return None, "unresolved: checkpoint ships no id2label and no index was configured"


def positive_probability(predictions: Any, fake_index: int, id2label: dict[int, str] | None) -> float | None:
    """Pull P(positive class) out of a transformers classification result.

    Returns None when the label scheme cannot be matched. The caller must degrade on None
    rather than substitute a number: an earlier version returned `1 - max(score)` as a
    fallback, which turned a confident "fake" into a confident "pass".
    """
    scores = predictions[0] if predictions and isinstance(predictions[0], list) else predictions
    if not scores:
        return None

    wanted = {f"label_{fake_index}", str(fake_index)}
    if id2label and fake_index in id2label:
        wanted.add(str(id2label[fake_index]).strip().lower())

    for entry in scores:
        label = str(entry.get("label", "")).strip().lower()
        if label in wanted or label in _POSITIVE_LABELS:
            return float(entry["score"])
    return None


def _build(task: str, model_name: str, revision: str | None, allow_download: bool) -> LoadedModel:
    import torch  # noqa: PLC0415
    from transformers import pipeline  # noqa: PLC0415

    kwargs = {"revision": revision, "local_files_only": not allow_download}

    if task == "text-classification":
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

        processor = AutoTokenizer.from_pretrained(model_name, **kwargs)
        model = AutoModelForSequenceClassification.from_pretrained(model_name, **kwargs)
        pipe = pipeline(
            task, model=model, tokenizer=processor, truncation=True, max_length=512,
        )
    elif task == "image-classification":
        from transformers import AutoImageProcessor, AutoModelForImageClassification  # noqa: PLC0415

        processor = AutoImageProcessor.from_pretrained(model_name, **kwargs)
        model = AutoModelForImageClassification.from_pretrained(model_name, **kwargs)
        pipe = pipeline(task, model=model, image_processor=processor)
    else:
        raise ValueError(f"unsupported task {task!r}")

    model.eval()
    torch.set_num_threads(max(1, torch.get_num_threads()))

    raw = getattr(model.config, "id2label", None)
    # transformers synthesises {0: "LABEL_0", 1: "LABEL_1"} when the checkpoint has no
    # map. That is not a label map — treat it as absent so resolve_fake_index does not
    # mistake a placeholder for evidence.
    id2label: dict[int, str] | None = None
    if raw:
        as_dict = {int(k): str(v) for k, v in raw.items()}
        if not all(v.upper().startswith("LABEL_") for v in as_dict.values()):
            id2label = as_dict

    return LoadedModel(pipe=pipe, lock=threading.Lock(), id2label=id2label,
                       model_name=model_name, revision=revision)


def get_model(
    task: str,
    model_name: str,
    revision: str | None = None,
    *,
    allow_download: bool = True,
) -> LoadedModel:
    """Return the cached pipeline for this checkpoint, loading it at most once."""
    key = (task, model_name, revision)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with _CACHE_LOCK:
        cached = _CACHE.get(key)  # re-check: another thread may have loaded it
        if cached is None:
            cached = _build(task, model_name, revision, allow_download)
            _CACHE[key] = cached
        return cached


def clear_cache() -> None:
    """Drop every loaded model. Tests only."""
    with _CACHE_LOCK:
        _CACHE.clear()


def loaded_keys() -> list[tuple[str, str, str | None]]:
    return list(_CACHE)
