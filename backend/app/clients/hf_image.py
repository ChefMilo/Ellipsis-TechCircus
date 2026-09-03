"""Real Tier 2 image backend: a CNN-class real-vs-synthetic image classifier.

Proposal §2.2 asks for "a CNN (EfficientNet-B4-based **or alternative**)". The default
checkpoint here, `Organika/sdxl-detector`, is a **Swin transformer**, not an EfficientNet —
in spec under "or alternative", but worth stating plainly rather than letting someone
discover it in the config. It does ship a real label map (`{0: artificial, 1: human}`),
so unlike the text checkpoint its "positive" class needs no empirical detective work.

It is also SDXL-specialised, which means per-generator accuracy varies a lot. Report the
breakdown by generator rather than one aggregate — see `ws4_eval.py`.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.clients.hf_loader import get_model, positive_probability, resolve_fake_index
from app.clients.screening_base import FetchedImage, ImageRisk
from app.config import Settings

_TASK = "image-classification"


class HFImageScorer:
    # Real detection needs the actual bytes, so the pipeline will download each image.
    needs_pixels = True

    def __init__(self, settings: Settings) -> None:
        self._loaded = get_model(
            _TASK,
            settings.image_model_name,
            settings.image_revision,
            allow_download=settings.allow_model_download,
        )
        index, provenance = resolve_fake_index(self._loaded.id2label, None)
        if index is None:
            raise RuntimeError(
                f"cannot establish which class means 'synthetic' for {settings.image_model_name}: "
                f"{provenance}. Refusing to guess."
            )
        self.synthetic_index = index
        self.label_provenance = provenance
        self.name = f"hf:{settings.image_model_name}"

    def warmup(self) -> None:
        try:
            from PIL import Image  # noqa: PLC0415
        except ImportError:
            return
        blank = Image.new("RGB", (224, 224), (128, 128, 128))
        with self._loaded.lock:
            self._loaded.pipe(blank)

    def score_batch(self, images: Sequence[FetchedImage]) -> list[ImageRisk]:
        results: list[ImageRisk] = []
        for item in images:
            # No pixels means no evidence. Score 0.0 so it cannot escalate, and say why —
            # this is NOT the same claim as "we looked and it is authentic".
            if not item.ok:
                results.append(
                    ImageRisk(
                        item.url, 0.0, self.name,
                        f"not scored: {item.error or 'no image data'}", scored=False,
                    )
                )
                continue
            try:
                with self._loaded.lock:
                    predictions = self._loaded.pipe(item.image)
                score = positive_probability(predictions, self.synthetic_index, self._loaded.id2label)
            except Exception as exc:
                results.append(
                    ImageRisk(
                        item.url, 0.0, self.name,
                        f"inference failed: {exc.__class__.__name__}", scored=False,
                    )
                )
                continue

            if score is None:
                results.append(
                    ImageRisk(item.url, 0.0, self.name, "unrecognised label scheme; not scored", scored=False)
                )
            else:
                results.append(
                    ImageRisk(item.url, round(float(score), 4), self.name, "classified by real-vs-synthetic CNN")
                )
        return results
