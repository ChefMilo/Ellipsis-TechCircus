"""WS4 latency benchmark — derives the budget instead of assuming one.

WS4's acceptance criterion is "p95 latency is inside a stated budget". A budget picked
before any model ran is not a budget, it is a wish, so this measures the real thing and
prints what the configuration should be.

    python ws4_bench.py                       # heuristic + real text, local text only
    python ws4_bench.py --with-images         # include real image fetch + CNN inference
    python ws4_bench.py --runs 50

Report both numbers you might be asked for, and say which one you are quoting: a
single-user p95 and a concurrent p95 are different measurements, and only one of them
describes a live demo.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings, get_settings
from app.models.screening import ScreeningInput
from app.pipeline.ws4 import run_ws4

DATASET = Path(__file__).parent / "eval" / "heldout_dataset.json"

# Small, stable, permissively-served images for the image path.
BENCH_IMAGES = [
    "https://picsum.photos/seed/dasfax1/400/300",
    "https://picsum.photos/seed/dasfax2/400/300",
    "https://picsum.photos/seed/dasfax3/400/300",
]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarise(name: str, latencies: list[float], budget: float) -> dict:
    p50, p95, p99 = percentile(latencies, 50), percentile(latencies, 95), percentile(latencies, 99)
    verdict = "OK" if p95 <= budget else "OVER BUDGET"
    print(
        f"  {name:<34} n={len(latencies):<4} "
        f"p50 {p50:7.1f}  p95 {p95:7.1f}  p99 {p99:7.1f}  max {max(latencies):7.1f} ms   [{verdict}]"
    )
    return {"name": name, "n": len(latencies), "p50": p50, "p95": p95, "p99": p99,
            "max": max(latencies), "within_budget": p95 <= budget}


def run_config(pages: list[ScreeningInput], settings: Settings, runs: int) -> list[float]:
    # One untimed pass so model load / first-forward specialisation never lands in the sample.
    run_ws4(pages[0], settings=settings)
    latencies = []
    for i in range(runs):
        page = pages[i % len(pages)]
        started = time.perf_counter()
        run_ws4(page, settings=settings)
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WS4 Tier 2 latency benchmark.")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--with-images", action="store_true", help="Include real image fetch + CNN inference.")
    parser.add_argument("--concurrency", type=int, default=1, help="Simultaneous screens (>1 measures load).")
    parser.add_argument("--json", type=Path, help="Write the measurements to a file.")
    args = parser.parse_args(argv)

    samples = json.loads(DATASET.read_text(encoding="utf-8"))["text_samples"]
    images = BENCH_IMAGES if args.with_images else []
    pages = [
        ScreeningInput.model_validate(
            {"url": f"https://news.example.org/{s['id']}", "title": s.get("title"),
             "text": s["text"], "images": images}
        )
        for s in samples[:12]
    ]

    base = get_settings()
    print("=" * 92)
    print("WS4 — Tier 2 latency")
    print("=" * 92)
    print(f"  machine        {platform.machine()} / {platform.system()} {platform.release()}, "
          f"python {platform.python_version()}")
    print(f"  runs           {args.runs} per config, concurrency {args.concurrency}")
    print(f"  images         {'REAL fetch + CNN' if args.with_images else 'none (text path only)'}")
    print(f"  stated budget  DASFAX_SCREEN_BUDGET_MS = {base.screen_budget_ms:.0f} ms\n")

    # Derive from get_settings() so every env knob (budgets, thresholds, checkpoints) is
    # honoured; only the backend mode is varied. Building a bare Settings() here would
    # silently benchmark the dataclass defaults instead of the configuration under test.
    configs = [
        ("heuristic (offline fallback)", "heuristic"),
        ("huggingface (real models)", "huggingface"),
    ]

    report = []
    for label, mode in configs:
        settings = Settings(**{**base.__dict__, "screening_mode": mode})
        try:
            if args.concurrency > 1:
                with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    futures = [
                        pool.submit(run_config, pages, settings, max(1, args.runs // args.concurrency))
                        for _ in range(args.concurrency)
                    ]
                    latencies = [v for f in futures for v in f.result()]
            else:
                latencies = run_config(pages, settings, args.runs)
        except Exception as exc:
            print(f"  {label:<34} SKIPPED ({exc.__class__.__name__}: {exc})")
            continue
        report.append(summarise(label, latencies, base.screen_budget_ms))

    real = next((r for r in report if r["name"].startswith("huggingface")), None)
    if real:
        suggested = max(50, int(real["p95"] * 1.3 + 0.5))
        print(f"\n  Measured p95 for the real models: {real['p95']:.1f} ms")
        print("  Suggested budget (p95 x 1.3, headroom for a cold cache and a busier machine):")
        print(f"      DASFAX_SCREEN_BUDGET_MS={suggested}")
        if not real["within_budget"]:
            print("  The current budget is TOO TIGHT — screens are being cut short at the deadline.")

    print("\n  Quote these as single-user figures unless --concurrency was set. A demo on one")
    print("  laptop and a tier under concurrent load are different measurements.\n")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
