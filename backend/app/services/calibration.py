"""Calibration and evaluation statistics for WS4.

Hand-rolled rather than pulled from scikit-learn: each function is a few lines, they are
unit-tested here, and keeping numpy/sklearn out of the runtime path means the deployable
backend stays small. Nothing in this module is imported by the request path.

Why these particular statistics:

  auroc            threshold-free, so it measures whether the model ranks fake above real
                   regardless of how badly calibrated its probabilities are. That makes it
                   the right tool for deciding which class index means "fake".
  wilson_interval  a point estimate on 80 samples is not a claim. At n=80, 90% accuracy is
                   roughly +/-7pp, and every reported figure should carry that.
  ece / brier      fine-tuned classifiers pile their scores at 0.00 and 1.00. If the score
                   distribution is bimodal, "threshold 0.65 vs 0.75" is a distinction
                   without a difference, and the sweep needs to say so out loud.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def _ranks(values: Sequence[float]) -> list[float]:
    """Fractional ranks, averaging over ties (1-based)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def auroc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Area under the ROC curve, via the Mann-Whitney U identity with tie correction.

    Returns 0.5 when either class is empty — "no information", which is the honest answer
    for a degenerate sample rather than 0.0 or 1.0.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")
    positives = sum(1 for y in labels if y)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    ranks = _ranks(scores)
    positive_rank_sum = sum(r for r, y in zip(ranks, labels, strict=True) if y)
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def brier(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Mean squared error between predicted probability and outcome. Lower is better."""
    if not scores:
        return 0.0
    return sum((s - (1.0 if y else 0.0)) ** 2 for s, y in zip(scores, labels, strict=True)) / len(scores)


def ece(scores: Sequence[float], labels: Sequence[bool], *, bins: int = 10) -> float:
    """Expected calibration error: average gap between confidence and observed frequency.

    A model with ECE 0.30 that says "0.9" is right about 60% of the time — its numbers are
    rankings wearing the costume of probabilities.
    """
    if not scores:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for s, y in zip(scores, labels, strict=True):
        index = min(bins - 1, max(0, int(s * bins)))
        buckets[index].append((s, y))

    total = len(scores)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        confidence = sum(s for s, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, y in bucket if y) / len(bucket)
        error += (len(bucket) / total) * abs(confidence - accuracy)
    return error


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials.

    Preferred over the normal approximation because it stays inside [0,1] and behaves at
    the extremes — which is exactly where small evaluation sets live (k=n=20 is common,
    and the normal approximation claims a zero-width interval there).
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def histogram(scores: Sequence[float], *, bins: int = 10) -> list[int]:
    counts = [0] * bins
    for s in scores:
        counts[min(bins - 1, max(0, int(s * bins)))] += 1
    return counts


def is_bimodal(scores: Sequence[float], *, edge_share: float = 0.7) -> bool:
    """True when most scores sit in the extreme bins — the signature of an uncalibrated
    classifier, and a warning that threshold choice within the middle range is arbitrary."""
    if not scores:
        return False
    extreme = sum(1 for s in scores if s < 0.1 or s > 0.9)
    return extreme / len(scores) >= edge_share


def format_histogram(scores: Sequence[float], *, bins: int = 10, width: int = 40) -> str:
    counts = histogram(scores, bins=bins)
    peak = max(counts) or 1
    lines = []
    for i, count in enumerate(counts):
        low, high = i / bins, (i + 1) / bins
        bar = "#" * round(width * count / peak)
        lines.append(f"    {low:.1f}-{high:.1f} {bar:<{width}} {count}")
    return "\n".join(lines)
