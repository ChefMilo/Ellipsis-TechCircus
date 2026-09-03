"""Fine-tune a Tier 2 text classifier that is actually usable as a gate.

    python train_ws4.py                     # WELFake only
    python train_ws4.py --augment-agnews    # + real news across content types
    python train_ws4.py --unfreeze 4        # adapt more of the encoder

Why train at all: every off-the-shelf checkpoint we measured flags most REAL news as
fake — omykhailiv 83.1%, Pulk17 61.7%, hamzab 55.2% on 480 AG News articles. They have
learned "attributed institutional hard news = real, everything else = fake", so sport,
business and lifestyle reporting all read as fabricated. A gate like that escalates
everything and destroys the cascade's cost rationale.

Three things this does that a naive fine-tune does not:

1. VERIFIES THE LABEL DIRECTION EMPIRICALLY. The widely-shared recipe for this dataset
   sets id2label={0:"Fake", 1:"Real"}. In the HuggingFace copy used here that is
   BACKWARDS — label 0 carries Reuters/NYT bylines (79.6% vs 0.3%) while label 1 carries
   clickbait markers (24.3% vs 1.5%) and ALL-CAPS headlines (38.0% vs 0.2%). Following
   the recipe literally trains a perfectly inverted classifier that reports high accuracy
   the whole way. We assert the direction from the data instead of trusting a constant.

2. STRIPS THE DATELINE ARTIFACT. WELFake absorbs ISOT, whose real class is Reuters wire
   copy: "(Reuters)" appears in 60.4% of real articles and 0.1% of fake ones. Left in, the
   model learns "says Reuters -> real" and posts a near-perfect score that means nothing
   off-distribution. Outlet bylines are stripped for the same reason.

3. TRAINS ON RANDOM WINDOWS, not always the first 256 tokens. The deployed path scores
   overlapping windows, so the model should be robust to where in an article it lands.

Ships a real id2label so nothing downstream ever has to guess the mapping again.
"""
from __future__ import annotations

import argparse
import random
import re
from pathlib import Path

import numpy as np
import torch
from datasets import Dataset, load_dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
)

BASE_MODEL = "google-bert/bert-base-uncased"
OUT_DIR = Path(__file__).parent / "models" / "dasfax-tier2-text"
MAX_LEN = 256
SEED = 42

# Artifacts that identify the SOURCE rather than the truthfulness of an article.
_DATELINE = re.compile(r"^\s*[A-Z][A-Za-z .'-]{2,30}\s*\((Reuters|AP|AFP)\)\s*[-–—]\s*")
_WIRE_TAG = re.compile(r"\((Reuters|AP|AFP)\)")
_OUTLET_SUFFIX = re.compile(
    r"\s*[-–—|]\s*(The New York Times|Reuters|Breitbart|The Guardian|CNN|Fox News|NPR|BBC)\s*$",
    re.I,
)
_WS = re.compile(r"\s+")


def scrub(title: str | None, text: str | None) -> str:
    """Remove source-identifying artifacts, then join title and body."""
    title = _OUTLET_SUFFIX.sub("", (title or "").strip())
    body = _DATELINE.sub("", (text or "").strip())
    body = _WIRE_TAG.sub("", body)
    return _WS.sub(" ", f"{title}. {body}").strip()


def verify_label_direction(rows: list[dict]) -> int:
    """Return which raw label means FAKE, established from the data.

    Uses signals with known polarity rather than a documented constant, because the
    documentation for this dataset is wrong in at least one widely-copied recipe.
    """
    byline = re.compile(r"- The New York Times|\(Reuters\)|\(AP\)", re.I)
    clickbait = re.compile(r"\bBREAKING:|\bWATCH:|SHOCKING|\[VIDEO\]", re.I)
    caps = re.compile(r"\b[A-Z]{4,}\b")

    score = {0: 0.0, 1: 0.0}
    counts = {0: 0, 1: 0}
    for r in rows[:8000]:
        lab = r["label"]
        counts[lab] += 1
        blob = f"{r.get('title') or ''} {r.get('text') or ''}"
        # positive = looks fake
        s = 0.0
        s -= 1.0 if byline.search(blob) else 0.0
        s += 1.0 if clickbait.search(blob) else 0.0
        s += 1.0 if len(caps.findall(r.get("title") or "")) >= 2 else 0.0
        score[lab] += s

    means = {k: score[k] / max(1, counts[k]) for k in (0, 1)}
    fake_label = max(means, key=lambda k: means[k])
    print(f"  label direction probe: mean fake-ness  label0={means[0]:+.3f}  label1={means[1]:+.3f}")
    print(f"  => raw label {fake_label} means FAKE")
    if abs(means[0] - means[1]) < 0.2:
        raise SystemExit("label direction is ambiguous; refusing to train on a guess")
    return fake_label


def build_liar2(limit: int) -> tuple[Dataset, Dataset, Dataset]:
    """LIAR2: 18k statements with PolitiFact truthfulness ratings.

    Why this corpus at all: WELFake's label answers "did this come from a site someone
    tagged as fake", which is source reputation. LIAR2's answers "did fact-checkers rate
    this claim false", which is veracity. Training on the first and evaluating on the
    second is what capped every model we have measured.

    0=pants-fire, 1=false -> FAKE. 4=mostly-true, 5=true -> REAL. The middle two
    (barely-true, half-true) are dropped: they are genuinely mixed verdicts and forcing
    them to a side teaches the model that ambiguity has a correct answer.

    Splits are LIAR2's OWN train/validation. The test split is NEVER loaded here -- it is
    the held-out benchmark every number in this workstream is quoted against, and reading
    it during training would silently invalidate all of them.
    """
    def rows(split: str) -> list[dict]:
        d = load_dataset("chengxuphd/liar2", split=split)
        out = []
        for r in d:
            if r["label"] in (0, 1, 4, 5):
                text = _WS.sub(" ", (r["statement"] or "")).strip()
                if text:
                    out.append({"content": text, "label": 1 if r["label"] in (0, 1) else 0})
        return out

    print("Loading LIAR2 (PolitiFact verdicts)…")
    train = rows("train")[:limit]
    val = rows("validation")
    random.Random(SEED).shuffle(train)
    half = len(val) // 2
    print(f"  train {len(train)}  val {len(val) - half}  val-as-test {half}")
    print(f"  train class balance: fake {sum(r['label'] for r in train)} / real {len(train) - sum(r['label'] for r in train)}")
    print("  NOTE: LIAR2 test split deliberately NOT loaded — it is the held-out benchmark.")
    return Dataset.from_list(train), Dataset.from_list(val[half:]), Dataset.from_list(val[:half])


def build_dataset(augment_agnews: bool, limit: int) -> tuple[Dataset, Dataset, Dataset]:
    rng = random.Random(SEED)
    print("Loading WELFake…")
    wel = load_dataset("davanstrien/WELFake", split="train")
    rows = [
        {"title": t, "text": x, "label": lab}
        for t, x, lab in zip(wel["title"], wel["text"], wel["label"], strict=True)
        if x and len(x.split()) >= 30
    ]
    fake_label = verify_label_direction(rows)

    examples = []
    seen: set[str] = set()
    for r in rows:
        content = scrub(r["title"], r["text"])
        key = content[:200]
        if len(content.split()) < 30 or key in seen:
            continue
        seen.add(key)
        examples.append({"content": content, "label": 1 if r["label"] == fake_label else 0})
    rng.shuffle(examples)
    examples = examples[:limit]
    print(f"  {len(examples)} WELFake examples after scrubbing and dedup")

    if augment_agnews:
        # Real news across content types (World/Sports/Business/Sci-Tech). The TRAIN split
        # only — the TEST split is the held-out false-positive benchmark and must stay unseen.
        ag = load_dataset("fancyzhx/ag_news", split="train")
        picks = rng.sample(range(len(ag)), min(8000, len(ag)))
        added = [{"content": _WS.sub(" ", ag[i]["text"]).strip(), "label": 0} for i in picks]
        examples += added
        rng.shuffle(examples)
        print(f"  + {len(added)} AG News real articles (register diversity)")

    n = len(examples)
    train, val, test = examples[: int(n * 0.9)], examples[int(n * 0.9) : int(n * 0.95)], examples[int(n * 0.95) :]
    print(f"  split: train {len(train)}  val {len(val)}  test {len(test)}")
    return Dataset.from_list(train), Dataset.from_list(val), Dataset.from_list(test)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fine-tune the WS4 Tier 2 text classifier.")
    ap.add_argument("--augment-agnews", action="store_true",
                    help="Add real news across content types to counter the register bias.")
    ap.add_argument("--unfreeze", type=int, default=2,
                    help="Trainable top encoder layers. -1 = full fine-tune (everything).")
    ap.add_argument("--base-model", default=BASE_MODEL,
                    help="Any HF sequence-classification encoder (bert/roberta/deberta-v3).")
    ap.add_argument("--max-len", type=int, default=MAX_LEN, help="Token window.")
    ap.add_argument("--corpus", choices=("welfake", "liar2"), default="welfake",
                    help="welfake = source-reputation labels; liar2 = PolitiFact verdicts.")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=40000, help="Max WELFake examples.")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    torch.manual_seed(SEED)
    if args.corpus == "liar2":
        train_ds, val_ds, test_ds = build_liar2(args.limit)
    else:
        train_ds, val_ds, test_ds = build_dataset(args.augment_agnews, args.limit)

    tok = AutoTokenizer.from_pretrained(args.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model, num_labels=2,
        id2label={0: "real", 1: "fake"},   # shipped, so nothing downstream has to guess
        label2id={"real": 0, "fake": 1},
        # Apple's MPS backend has no dropout in fused scaled-dot-product attention, so the
        # default SDPA path raises during training. Eager attention is slower but portable.
        attn_implementation="eager",
    )

    # Partial fine-tuning: the lower layers already model English; only the top layers
    # carry the task-specific signal we need to change. Fewer trainable parameters also
    # means less capacity to memorise whatever shortcut survives scrubbing.
    encoder = model.base_model
    layers = None
    for path in ("encoder.layer", "transformer.layer", "layers"):
        obj = encoder
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None:
            layers = obj
            break
    if layers is None:
        raise SystemExit(f"cannot locate encoder layers on {type(encoder).__name__}")
    if args.unfreeze < 0 or args.unfreeze >= len(layers):
        scope = f"FULL fine-tune (all {len(layers)} layers + embeddings)"
    else:
        for p in encoder.parameters():
            p.requires_grad = False
        for layer in layers[-args.unfreeze:]:
            for p in layer.parameters():
                p.requires_grad = True
        scope = f"top {args.unfreeze} of {len(layers)} encoder layers + head"
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  trainable params: {trainable/1e6:.1f}M of {sum(p.numel() for p in model.parameters())/1e6:.1f}M "
          f"({scope})")

    def tokenize(batch):
        # Random window rather than always the first MAX_LEN tokens: the deployed path
        # scores overlapping windows, so position robustness is part of the task.
        out = []
        rng = random.Random(SEED)
        for content in batch["content"]:
            words = content.split()
            span = args.max_len  # generous: tokens <= words is false, but truncation handles it
            if len(words) > span:
                start = rng.randrange(0, len(words) - span // 2)
                content = " ".join(words[start : start + span])
            out.append(content)
        return tok(out, truncation=True, max_length=args.max_len)

    train_t = train_ds.map(tokenize, batched=True, remove_columns=["content"])
    val_t = val_ds.map(tokenize, batched=True, remove_columns=["content"])
    test_t = test_ds.map(tokenize, batched=True, remove_columns=["content"])

    def metrics(pred):
        logits, labels = pred
        p = np.argmax(logits, axis=1)
        acc = float((p == labels).mean())
        tp = int(((p == 1) & (labels == 1)).sum())
        fp = int(((p == 1) & (labels == 0)).sum())
        fn = int(((p == 0) & (labels == 1)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        return {"accuracy": round(acc, 4), "precision": round(prec, 4), "recall": round(rec, 4)}

    args_tr = TrainingArguments(
        output_dir=str(args.out / "checkpoints"),
        eval_strategy="epoch",
        save_strategy="no",
        learning_rate=2e-5,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch * 2,
        num_train_epochs=args.epochs,
        weight_decay=0.01,
        warmup_ratio=0.1,
        logging_steps=100,
        report_to=[],
        # fp16 is not supported on MPS; leaving it off keeps this portable.
    )
    trainer = Trainer(
        model=model, args=args_tr, train_dataset=train_t, eval_dataset=val_t,
        processing_class=tok, data_collator=DataCollatorWithPadding(tokenizer=tok),
        compute_metrics=metrics,
    )
    trainer.train()

    print(f"\nHeld-out {args.corpus} test split:")
    print(" ", trainer.evaluate(eval_dataset=test_t))

    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"\nsaved -> {args.out}")
    print("Evaluate it against the real-news benchmark that motivated this:")
    print(f"  DASFAX_BERT_MODEL_NAME={args.out} DASFAX_SCREENING_MODE=huggingface \\")
    print("      python ws4_eval.py --backend huggingface")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
