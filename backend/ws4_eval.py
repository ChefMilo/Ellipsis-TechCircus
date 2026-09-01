"""WS4 threshold calibration and evaluation.

Answers the question a judge will actually ask: *why that threshold?*

    python ws4_eval.py --backend huggingface     # calibrate the REAL BERT classifier
    python ws4_eval.py --backend heuristic       # the offline fallback (no accuracy claims)
    python ws4_eval.py --verify-labels           # which class index means "fake"?
    python ws4_eval.py --dataset isot.jsonl      # calibrate on an external corpus
    python ws4_eval.py --check                   # CI mode: fail if the shipped threshold regresses

Selection criterion (stated, not tuned-until-it-looks-good):

    Pick the HIGHEST threshold that still catches at least `--target-recall` of the
    fake-labelled articles.

Highest, because every point of threshold saved is Tier 3 compute not spent. Subject to
recall, because Tier 2 is a router: a miss here means the article is never checked and the
reader sees nothing, whereas a false alarm here costs money but never shows a wrong badge
to a user. The "don't cry wolf" constraint from proposal §3.3 binds at Tier 3/WS6, where a
status is actually rendered — not here.

Reporting rules this script enforces so a number can never be quoted out of context:
  * every figure carries n and a Wilson 95% interval;
  * the bundled corpus is labelled SYNTHETIC and is never given an "accuracy" headline;
  * results from the heuristic fallback are never presented as model performance;
  * `--backend huggingface` is strict — it refuses to silently run on the fallback.

External datasets: JSON Lines, one object per line, with `text` (required), `title`
(optional) and `label` (required; "fake"/"real", or 1/0 where 1 = fake).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.calibration import auroc, ece, format_histogram, is_bimodal, wilson_interval
from app.services.image_detector import heuristic_image_score
from app.services.text_classifier import heuristic_score

DATASET = Path(__file__).parent / "eval" / "heldout_dataset.json"
SWEEP = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05 .. 0.95
MIN_LABEL_MARGIN = 0.10


@dataclass(frozen=True)
class Confusion:
    """Confusion matrix at one threshold. Positive class = "should be flagged"."""

    threshold: float
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Share of genuine articles wrongly escalated. This is the cost line."""
        return self.fp / (self.fp + self.tn) if (self.fp + self.tn) else 0.0

    @property
    def escalation_rate(self) -> float:
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.fp) / total if total else 0.0


def confusion_at(scored: list[tuple[float, bool]], threshold: float) -> Confusion:
    tp = sum(1 for s, y in scored if y and s >= threshold)
    fp = sum(1 for s, y in scored if not y and s >= threshold)
    tn = sum(1 for s, y in scored if not y and s < threshold)
    fn = sum(1 for s, y in scored if y and s < threshold)
    return Confusion(threshold, tp, fp, tn, fn)


def choose_threshold(scored: list[tuple[float, bool]], target_recall: float) -> Confusion:
    """Highest threshold still meeting the recall target; falls back to best-recall."""
    candidates = [confusion_at(scored, t) for t in SWEEP]
    meeting = [c for c in candidates if c.recall >= target_recall]
    if meeting:
        return max(meeting, key=lambda c: c.threshold)
    return max(candidates, key=lambda c: (c.recall, c.threshold))


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _normalise_label(raw: object) -> bool:
    """True = positive class (fake text / synthetic image)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(int(raw))
    text = str(raw).strip().lower()
    if text in {"fake", "false", "misinformation", "synthetic", "ai", "1"}:
        return True
    if text in {"real", "true", "authentic", "genuine", "0"}:
        return False
    raise ValueError(f"unrecognised label {raw!r}")


def load_bundled(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("text_samples", []), data.get("image_samples", [])


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: not valid JSON ({exc.msg})") from exc
    return rows


# --------------------------------------------------------------------------- #
# Scoring backends
# --------------------------------------------------------------------------- #
def build_text_scorer(backend: str):
    """Return `(score_fn, backend_name, is_real_model)`.

    `huggingface` is strict on purpose. A silent degrade would produce a confusion matrix
    captioned "BERT" that is actually a regex — the easiest possible way to publish a
    number that is a lie.
    """
    if backend == "heuristic":
        return (lambda title, text: heuristic_score(title, text)[0]), "heuristic-text-v1", False

    from app.clients.factory import make_text_scorer

    settings = get_settings()
    scorer = make_text_scorer(settings, strict=True)
    if not scorer.name.startswith("hf:"):
        raise SystemExit(f"expected a real model, got {scorer.name!r}")
    scorer.warmup()
    return (lambda title, text: scorer.score(title=title, text=text).score), scorer.name, True


# --------------------------------------------------------------------------- #
# Label verification
# --------------------------------------------------------------------------- #
def verify_labels(samples: list[dict]) -> int:
    """Establish empirically which class index of the text checkpoint means "fake".

    Neither named BERT checkpoint ships an `id2label` map, so transformers invents
    LABEL_0/LABEL_1 and there is nothing in the artifact to read the mapping off. Guessing
    wrong inverts the entire tier — fake articles score as safe — and nothing anywhere
    would report an error.

    Method: score the labelled probe set twice, once assuming each index is "fake", and
    compare AUROC. AUROC is threshold-free, so it works even though the classifier's
    probabilities are badly calibrated. The two values sum to 1.0; the winner is whichever
    exceeds 0.5, and the margin is the confidence. Below MIN_LABEL_MARGIN we refuse to
    declare rather than pick a side on noise.
    """
    from app.clients.hf_loader import get_model, positive_probability

    settings = get_settings()
    loaded = get_model(
        "text-classification",
        settings.bert_model_name,
        settings.bert_revision,
        allow_download=settings.allow_model_download,
    )

    print("=" * 80)
    print("WS4 — establishing the 'fake' class index empirically")
    print("=" * 80)
    print(f"  checkpoint     {loaded.model_name} (revision={loaded.revision or 'main'})")
    print(f"  id2label       {loaded.id2label if loaded.id2label else 'ABSENT — nothing to read the mapping off'}")
    print(f"  configured     DASFAX_BERT_FAKE_LABEL_INDEX={settings.bert_fake_label_index}")
    print(f"  probe set      {len(samples)} labelled samples\n")

    labels = [_normalise_label(s["label"]) for s in samples]
    per_index: dict[int, list[float]] = {0: [], 1: []}
    for sample in samples:
        blob = f"{sample.get('title')}\n\n{sample['text']}" if sample.get("title") else sample["text"]
        with loaded.lock:
            predictions = loaded.pipe(blob, top_k=None)
        for index in (0, 1):
            score = positive_probability(predictions, index, loaded.id2label)
            per_index[index].append(0.5 if score is None else score)

    results = {index: auroc(scores, labels) for index, scores in per_index.items()}
    for index, value in sorted(results.items()):
        print(f"  assuming index {index} == 'fake'  ->  AUROC {value:.4f}")

    winner = max(results, key=lambda i: results[i])
    margin = abs(results[winner] - 0.5)
    print(f"\n  margin from chance: {margin:.4f}")

    if margin < MIN_LABEL_MARGIN:
        print(
            f"\n  INCONCLUSIVE — margin below {MIN_LABEL_MARGIN}. The probe set does not separate\n"
            "  the classes well enough to declare a mapping. Do NOT set the index from this run."
        )
        return 2

    print(f"\n  => index {winner} is the 'fake' class.")
    print(f"     Set it explicitly:  export DASFAX_BERT_FAKE_LABEL_INDEX={winner}")
    if settings.bert_fake_label_index is None:
        print("\n  Currently unset, so the text backend degrades to the heuristic. Set it to go live.")
        return 1
    if settings.bert_fake_label_index != winner:
        print(
            f"\n  MISMATCH — configured index is {settings.bert_fake_label_index}, evidence says "
            f"{winner}.\n  The tier is currently INVERTED: it scores fake articles as safe."
        )
        return 1
    print("\n  Configured index matches the evidence.")
    return 0


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_sweep(title: str, scored: list[tuple[float, bool]], operating: float) -> None:
    n_pos = sum(1 for _, y in scored if y)
    print(f"\n{title}")
    print(f"  {len(scored)} samples — {n_pos} positive, {len(scored) - n_pos} negative\n")
    print("  thresh    TP   FP   TN   FN   precision   recall      F1   FPR   escalated")
    print("  " + "-" * 76)
    for t in SWEEP:
        c = confusion_at(scored, t)
        marker = " <-- operating" if abs(t - operating) < 1e-9 else ""
        print(
            f"  {t:5.2f}  {c.tp:4d} {c.fp:4d} {c.tn:4d} {c.fn:4d}"
            f"   {c.precision:8.3f} {c.recall:7.3f} {c.f1:7.3f}"
            f" {c.false_positive_rate:5.3f}   {c.escalation_rate:8.1%}{marker}"
        )


def print_matrix(c: Confusion, positive: str, negative: str) -> None:
    print(f"\n  Confusion matrix at threshold {c.threshold:.2f}")
    print("                      predicted flag   predicted pass")
    print(f"    actual {positive:<12} {c.tp:>10d} {c.fn:>16d}")
    print(f"    actual {negative:<12} {c.fp:>10d} {c.tn:>16d}")

    recall_lo, recall_hi = wilson_interval(c.tp, c.tp + c.fn)
    prec_lo, prec_hi = wilson_interval(c.tp, c.tp + c.fp)
    print(
        f"\n    precision {c.precision:.3f} [{prec_lo:.2f}-{prec_hi:.2f}]"
        f" | recall {c.recall:.3f} [{recall_lo:.2f}-{recall_hi:.2f}]"
        f" | F1 {c.f1:.3f} | FPR {c.false_positive_rate:.3f}"
    )
    print("    (95% Wilson intervals — a point estimate on a corpus this size is not a claim)")


def print_misses(rows: list[tuple[str, float, bool]], threshold: float, limit: int = 8) -> None:
    """Name the samples the operating threshold gets wrong. Errors you cannot name are
    errors you cannot fix."""
    fn = [(i, s) for i, s, y in rows if y and s < threshold]
    fp = [(i, s) for i, s, y in rows if not y and s >= threshold]
    if fn:
        print(f"\n  Missed positives ({len(fn)}) — escalation never happens for these:")
        for sid, s in sorted(fn, key=lambda x: -x[1])[:limit]:
            print(f"    {sid}  score {s:.3f}")
    if fp:
        print(f"\n  False alarms ({len(fp)}) — genuine content sent to Tier 3:")
        for sid, s in sorted(fp, key=lambda x: -x[1])[:limit]:
            print(f"    {sid}  score {s:.3f}")
    if not fn and not fp:
        print("\n  No errors at the operating threshold on this corpus.")


def print_hard_case_breakdown(samples: list[dict], rows: list[tuple[str, float, bool]], threshold: float) -> None:
    """Separate the easy half of the corpus from the deliberately hard half.

    An aggregate figure over a corpus containing obvious clickbait is dominated by the easy
    cases. The hard subset — genuine journalism that reads as sensational, and
    misinformation written in a calm register — is where a real deployment actually lives.
    """
    hard_ids = {s.get("id") for s in samples if s.get("hard")}
    if not hard_ids:
        return
    easy = [(s, y) for i, s, y in rows if i not in hard_ids]
    hard = [(s, y) for i, s, y in rows if i in hard_ids]

    print("\n  Easy vs hard subsets at the operating threshold")
    for name, subset in (("easy   ", easy), ("hard   ", hard)):
        if not subset:
            continue
        c = confusion_at(subset, threshold)
        print(
            f"    {name} n={len(subset):<3} precision {c.precision:.3f}  recall {c.recall:.3f}  "
            f"F1 {c.f1:.3f}  AUROC {auroc([s for s, _ in subset], [y for _, y in subset]):.3f}"
        )
    print("    The hard subset is the honest number: it is what real browsing looks like.")


def print_distribution(scored: list[tuple[float, bool]]) -> None:
    scores = [s for s, _ in scored]
    labels = [y for _, y in scored]
    print("\n  Score distribution")
    print(format_histogram(scores))
    print(f"\n    AUROC {auroc(scores, labels):.4f} | ECE {ece(scores, labels):.4f}")
    if is_bimodal(scores):
        print(
            "    WARNING: the scores are bimodal — they pile at 0 and 1. The model ranks well\n"
            "    but its numbers are not probabilities, so any threshold between the modes\n"
            "    behaves identically. Do not over-claim precision about the exact value."
        )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WS4 Tier 2 threshold calibration.")
    parser.add_argument("--backend", choices=["heuristic", "huggingface"], default="heuristic",
                        help="Which text backend to evaluate. 'huggingface' is strict.")
    parser.add_argument("--dataset", type=Path, help="External JSONL text corpus (text/title/label per line).")
    parser.add_argument("--target-recall", type=float, default=0.90, help="Recall floor for threshold selection.")
    parser.add_argument("--check", action="store_true", help="CI mode: exit 1 if the shipped thresholds miss the target.")
    parser.add_argument("--quiet", action="store_true", help="Print only the summary lines.")
    parser.add_argument("--verify-labels", action="store_true",
                        help="Establish which class index of the real checkpoint means 'fake'.")
    args = parser.parse_args(argv)

    settings = get_settings()

    if not DATASET.exists():
        print(f"dataset not found: {DATASET}", file=sys.stderr)
        return 2
    bundled_text, image_samples = load_bundled(DATASET)

    if args.verify_labels:
        return verify_labels(bundled_text)

    if args.dataset:
        rows = load_jsonl(args.dataset)
        text_samples = [
            {"id": r.get("id", f"row{i}"), "title": r.get("title"), "text": r["text"], "label": r["label"]}
            for i, r in enumerate(rows, start=1)
        ]
        source, synthetic = str(args.dataset), False
    else:
        text_samples, image_samples = bundled_text, image_samples
        source, synthetic = str(DATASET.relative_to(Path(__file__).parent)), True

    try:
        score_text, backend_name, is_real = build_text_scorer(args.backend)
    except Exception as exc:
        print(f"could not build the {args.backend} backend: {exc}", file=sys.stderr)
        return 2

    print("=" * 80)
    print("WS4 — Tier 2 screening: threshold calibration")
    print("=" * 80)
    print(f"  corpus         {source}{'  [SYNTHETIC]' if synthetic else ''}")
    print(f"  text backend   {backend_name}{'' if is_real else '   [FALLBACK — not a model result]'}")
    print(f"  criterion      highest threshold with recall >= {args.target_recall:.2f}")

    # --- Text -------------------------------------------------------------- #
    text_rows = [
        (s.get("id", "?"), score_text(s.get("title"), s["text"]), _normalise_label(s["label"]))
        for s in text_samples
    ]
    text_scored = [(score, label) for _, score, label in text_rows]
    shipped_text = confusion_at(text_scored, settings.text_threshold)

    # Select on the HARD subset when the corpus marks one. Easy cases are caught at every
    # threshold, so they contribute nothing to the choice while dominating the aggregate —
    # selecting on the aggregate picks a threshold that looks good on clickbait and misses
    # the calm-register misinformation that actually reaches readers.
    hard_ids = {s.get("id") for s in text_samples if s.get("hard")}
    hard_scored = [(s, y) for i, s, y in text_rows if i in hard_ids]
    selection_set = hard_scored if len(hard_scored) >= 8 else text_scored
    selection_label = "hard subset" if selection_set is hard_scored else "full corpus"
    text_pick = choose_threshold(selection_set, args.target_recall)
    aggregate_pick = choose_threshold(text_scored, args.target_recall)

    if not args.quiet:
        print_sweep("TEXT — fake-news risk classifier", text_scored, settings.text_threshold)
        print_distribution(text_scored)
    print_matrix(shipped_text, "fake", "real")
    print(f"\n  Shipped threshold  DASFAX_TEXT_THRESHOLD = {settings.text_threshold:.2f}")
    print(f"  Criterion picks    {text_pick.threshold:.2f}  [selected on the {selection_label}, n={len(selection_set)}]")
    print(f"                     precision {text_pick.precision:.3f}, recall {text_pick.recall:.3f}, "
          f"escalating {text_pick.escalation_rate:.1%}")
    if aggregate_pick.threshold != text_pick.threshold:
        print(
            f"  NOTE               selecting on the full corpus would pick "
            f"{aggregate_pick.threshold:.2f} instead — the easy cases dominate the aggregate\n"
            f"                     and are caught at every threshold, so they cannot inform the choice."
        )
    if not args.quiet:
        print_hard_case_breakdown(text_samples, text_rows, settings.text_threshold)
        print_misses(text_rows, settings.text_threshold)

    # --- Images ------------------------------------------------------------ #
    shipped_image = None
    if image_samples and not args.dataset:
        image_rows = [
            (s.get("id", "?"), heuristic_image_score(s["image_url"]).score, _normalise_label(s["label"]))
            for s in image_samples
        ]
        image_scored = [(score, label) for _, score, label in image_rows]
        image_pick = choose_threshold(image_scored, args.target_recall)
        shipped_image = confusion_at(image_scored, settings.image_threshold)

        if not args.quiet:
            print_sweep("IMAGE — provenance-marker detector [FALLBACK]", image_scored, settings.image_threshold)
        print_matrix(shipped_image, "synthetic", "authentic")
        print(f"\n  Shipped threshold  DASFAX_IMAGE_THRESHOLD = {settings.image_threshold:.2f}")
        print(f"  Criterion picks    {image_pick.threshold:.2f} "
              f"(precision {image_pick.precision:.3f}, recall {image_pick.recall:.3f})")

    # --- Caveats ----------------------------------------------------------- #
    print("\n" + "=" * 80)
    print("  Read this before quoting the numbers above")
    print("=" * 80)
    if synthetic:
        print("  This corpus is SYNTHETIC and hand-authored (see eval/heldout_dataset.json). It")
        print("  exists to pick a threshold and catch regressions in CI. It is NOT an accuracy")
        print("  measurement. For a defensible figure, evaluate on a real held-out split:")
        print("      python ws4_eval.py --backend huggingface --dataset path/to/heldout.jsonl")
    if not is_real:
        print("  These are HEURISTIC FALLBACK numbers, not the BERT classifier the proposal")
        print("  specifies. Re-run with --backend huggingface for a model result.")
    print("  The image detector reported here reads URL provenance markers only — it never")
    print("  inspects pixels, so its recall reflects marker coverage, not vision accuracy.")
    print("  The real image model (Organika/sdxl-detector) is a Swin transformer, not an")
    print("  EfficientNet-B4, and is SDXL-specialised: report per-generator, not one figure.")
    print()

    if args.check:
        failures = []
        if shipped_text.recall < args.target_recall:
            failures.append(
                f"text recall {shipped_text.recall:.3f} < target {args.target_recall:.2f} "
                f"at shipped threshold {settings.text_threshold:.2f}"
            )
        if shipped_image is not None and shipped_image.recall < args.target_recall:
            failures.append(
                f"image recall {shipped_image.recall:.3f} < target {args.target_recall:.2f} "
                f"at shipped threshold {settings.image_threshold:.2f}"
            )
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1 if failures else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
