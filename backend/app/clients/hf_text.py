"""Real Tier 2 text backend: a fine-tuned BERT fake-news classifier (proposal §2.2).

Construction is eager and fails loudly: if the weights will not load, or the "fake" class
index cannot be established, `__init__` raises and the factory decides what to do about it
(degrade in `auto` mode, refuse in `huggingface`/strict mode). That is deliberate — a
scorer that silently produced heuristic numbers under a `hf:` backend name would make every
downstream metric a lie.
"""
from __future__ import annotations

from app.clients.hf_loader import get_model, positive_probability, resolve_fake_index
from app.clients.screening_base import TextRisk
from app.config import Settings
from app.services.text_classifier import MIN_SCOREABLE_WORDS, NEUTRAL_PRIOR, word_count

_TASK = "text-classification"


class HFTextScorer:
    needs_pixels = False

    def __init__(self, settings: Settings) -> None:
        self._loaded = get_model(
            _TASK,
            settings.bert_model_name,
            settings.bert_revision,
            allow_download=settings.allow_model_download,
        )
        index, provenance = resolve_fake_index(self._loaded.id2label, settings.bert_fake_label_index)
        if index is None:
            raise RuntimeError(
                f"cannot establish which class means 'fake' for {settings.bert_model_name}: "
                f"{provenance}. Run `python ws4_eval.py --verify-labels` and set "
                f"DASFAX_BERT_FAKE_LABEL_INDEX. Refusing to guess — a wrong mapping scores "
                f"fake articles as safe, silently."
            )
        self.fake_index = index
        self.label_provenance = provenance
        self.name = f"hf:{settings.bert_model_name}"

    def warmup(self) -> None:
        """One real forward pass. Loading weights is not enough — torch allocates and
        specialises on the first inference, and that cost would otherwise land in the
        first user request's latency_ms and poison the p95 sample."""
        self.score(title=None, text="warm up the graph with a sentence of ordinary length. " * 3)

    def score(self, *, title: str | None, text: str) -> TextRisk:
        blob = f"{title}\n\n{text}" if title else text

        # Shared floor with the heuristic backend. BERT will happily return a confident
        # score for six words; that confidence is not information.
        if word_count(blob) < MIN_SCOREABLE_WORDS:
            return TextRisk(
                score=NEUTRAL_PRIOR,
                backend=self.name,
                degraded_reason=f"under {MIN_SCOREABLE_WORDS} words; returned the neutral prior",
            )

        with self._loaded.lock:
            predictions = self._loaded.pipe(blob, top_k=None)

        score = positive_probability(predictions, self.fake_index, self._loaded.id2label)
        if score is None:
            return TextRisk(
                score=NEUTRAL_PRIOR,
                backend=self.name,
                degraded_reason=(
                    f"unrecognised label scheme in {predictions!r}; returned the neutral prior "
                    "rather than inferring a probability"
                ),
            )
        return TextRisk(score=round(float(score), 4), backend=self.name)
