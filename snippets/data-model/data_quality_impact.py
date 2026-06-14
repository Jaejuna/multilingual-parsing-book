#!/usr/bin/env python3
"""Quantify the data -> model relationship: label noise vs classifier accuracy.

WHY THIS EXISTS
---------------
Every other tool in this book treats data as the product. But the reason data
quality matters is downstream: a model trained on it. "Clean your labels" is
advice; "10% label noise costs 8 points of accuracy on this task" is a result a
team can weigh against the cost of cleaning. This chapter makes that link
measurable by training an actual (small) classifier on data we corrupt by a
known amount, and plotting the degradation.

It also earns the book a real ML model — a multinomial Naive Bayes text
classifier, implemented in the standard library (it is just smoothed word
counts in log space), so the data->model story isn't hand-waved.

THE EXPERIMENT
--------------
1. synthesize a labelled intent-classification set (two intents, characteristic
   vocab + shared filler), with a CLEAN held-out test set
2. for a sweep of noise rates, flip that fraction of TRAINING labels
3. train Naive Bayes on the noisy training set, evaluate on the CLEAN test set
4. report accuracy vs noise -> the data-quality/performance curve

The test set is never corrupted: we are measuring how bad training data hurts a
model judged against the truth, which is the question that actually matters.

USAGE
-----
    python data_quality_impact.py
    python data_quality_impact.py --rates 0,0.1,0.2,0.4 --n 1200 --seed 7

Stdlib only (math, random, collections). Python 3.10+.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict


# --------------------------------------------------------------------------
# Synthetic data: two intents with signal words, plus shared filler noise.
# --------------------------------------------------------------------------

SIGNAL = {
    "buy": ["buy", "purchase", "order", "checkout", "cart", "price"],
    "cancel": ["cancel", "refund", "stop", "remove", "undo", "return"],
}
FILLER = ["the", "a", "please", "now", "my", "this", "it", "to", "for", "want"]

# How often a "signal" word is actually drawn from the WRONG class. With weak,
# confusable signal the model leans harder on label correctness, so label noise
# bites visibly — a clean task would shrug noise off and teach nothing.
CONFUSE = 0.15


def make_dataset(n: int, rng: random.Random) -> list[tuple[list[str], str]]:
    rows = []
    classes = list(SIGNAL)
    for _ in range(n):
        intent = rng.choice(classes)
        other = [c for c in classes if c != intent][0]
        words = rng.choices(FILLER, k=rng.randint(4, 7))
        for _ in range(rng.randint(1, 2)):                 # 1-2 weak signal words
            src = other if rng.random() < CONFUSE else intent
            words.append(rng.choice(SIGNAL[src]))
        rng.shuffle(words)
        rows.append((words, intent))
    return rows


def add_label_noise(rows, rate: float, rng: random.Random):
    """Flip `rate` of labels to the other class (training-time corruption)."""
    classes = list(SIGNAL)
    out = []
    for words, label in rows:
        if rng.random() < rate:
            label = rng.choice([c for c in classes if c != label])
        out.append((words, label))
    return out


# --------------------------------------------------------------------------
# Multinomial Naive Bayes — stdlib, log-space, Laplace smoothing
# --------------------------------------------------------------------------


class NaiveBayes:
    def __init__(self) -> None:
        self.logprior: dict[str, float] = {}
        self.loglik: dict[str, dict[str, float]] = {}
        self.vocab: set[str] = set()

    def fit(self, rows: list[tuple[list[str], str]]) -> "NaiveBayes":
        by_class: dict[str, list[str]] = defaultdict(list)
        for words, label in rows:
            by_class[label] += words
            self.vocab.update(words)
        n_docs = len(rows)
        v = len(self.vocab) or 1
        for cls, words in by_class.items():
            docs = sum(1 for _, l in rows if l == cls)
            self.logprior[cls] = math.log(docs / n_docs)
            counts: dict[str, int] = defaultdict(int)
            for w in words:
                counts[w] += 1
            total = len(words)
            # Laplace smoothing so unseen words don't zero out a class.
            self.loglik[cls] = {
                w: math.log((counts[w] + 1) / (total + v)) for w in self.vocab
            }
            self.loglik[cls]["__total__"] = total
        self._v = v
        return self

    def predict(self, words: list[str]) -> str:
        best, best_score = None, -math.inf
        for cls in self.logprior:
            score = self.logprior[cls]
            total = self.loglik[cls]["__total__"]
            for w in words:
                if w in self.vocab:
                    score += self.loglik[cls][w]
                else:
                    score += math.log(1 / (total + self._v))   # unseen word
            if score > best_score:
                best, best_score = cls, score
        return best


def accuracy(model: NaiveBayes, rows) -> float:
    correct = sum(1 for words, label in rows if model.predict(words) == label)
    return correct / len(rows) if rows else 0.0


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


def run(n: int, rates: list[float], seed: int) -> list[dict]:
    rng = random.Random(seed)
    data = make_dataset(n, rng)
    split = int(len(data) * 0.7)
    train, test = data[:split], data[split:]   # test set stays CLEAN
    out = []
    for rate in rates:
        noisy = add_label_noise(train, rate, random.Random(seed + 1))
        model = NaiveBayes().fit(noisy)
        out.append({"noise": rate, "test_acc": round(accuracy(model, test), 4)})
    return out


def render(rows: list[dict]) -> str:
    base = rows[0]["test_acc"]
    out = ["# Label noise vs test accuracy\n",
           "Training labels corrupted by `noise`; the test set stays clean.\n",
           "| train label noise | test accuracy | drop from clean |",
           "|------------------:|--------------:|----------------:|"]
    for r in rows:
        out.append(f"| {r['noise']:.0%} | {r['test_acc']:.1%} | "
                   f"{(base - r['test_acc']):.1%} |")
    out.append(
        "\nThe shape is the lesson: Naive Bayes aggregates word counts over the "
        "whole training set, so moderate label noise mostly averages out — "
        "accuracy barely moves up to ~40%. Past that the wrong labels start to "
        "win the vote and accuracy collapses toward chance (50%) as the labels "
        "approach 50% wrong. So 'clean your labels' is real but the budget is "
        "nuanced: a robust model tolerates some noise, and this curve says how "
        "much — in points of accuracy, not adjectives.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Measure label noise vs model accuracy.")
    p.add_argument("--n", type=int, default=1500, help="total examples")
    p.add_argument("--rates", default="0,0.1,0.2,0.3,0.4,0.45,0.5",
                   help="comma-separated training-label noise rates")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    rates = [float(x) for x in args.rates.split(",")]
    print(render(run(args.n, rates, args.seed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
