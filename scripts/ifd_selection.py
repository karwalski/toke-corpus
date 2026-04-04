#!/usr/bin/env python3
"""IFD-based data selection for toke training corpus.

Implements Instruction-Following Difficulty (IFD) scoring and K-Means
clustering to select high-value training samples at various retention
rates.

IFD (Lv et al. 2025):
    IFD(x) = loss(response | instruction) / loss(response)

Higher IFD means the instruction adds more value -- the response is
harder to produce without the instruction, so the sample teaches
the model more about instruction following.

Modes:
  --dry-run     Proxy IFD from heuristics (code complexity, instruction
                length ratio, keyword diversity).  No model required.
  --scores-file Load pre-computed IFD scores from a JSONL file with
                columns: task_id, ifd_score.

Usage::

    python scripts/ifd_selection.py --dry-run --seed 42 \
        --corpus-dir corpus --output-dir data

    python scripts/ifd_selection.py --scores-file data/precomputed_ifd.jsonl \
        --corpus-dir corpus --output-dir data

Story 9.7.1 -- IFD-based data selection.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def load_corpus(corpus_dir: Path) -> list[dict[str, Any]]:
    """Walk *corpus_dir* and load every .json corpus entry."""
    entries: list[dict[str, Any]] = []
    for root, _dirs, files in os.walk(corpus_dir):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            fpath = Path(root) / fname
            try:
                with open(fpath) as f:
                    entry = json.load(f)
                if "task_id" in entry and "tk_source" in entry:
                    entries.append(entry)
            except (json.JSONDecodeError, OSError):
                pass
    return entries


# ---------------------------------------------------------------------------
# Proxy IFD heuristics (dry-run mode)
# ---------------------------------------------------------------------------

# Toke keywords -- used for keyword diversity scoring.
TOKE_KEYWORDS = {
    "let", "mut", "fn", "lp", "if", "el", "mp", "rt",
    "true", "false", "nil", "as",
}

TOKE_CONSTRUCTS = {
    "M=", "F=", "T=", "I=",           # declarations
    "lp(", "if(", "el{", "mp(",       # control flow
    "let ", "mut.",                     # bindings
    ".[", ".get(", ".len",             # member access
}


def _code_complexity(source: str) -> float:
    """Estimate syntactic complexity of toke source (0..1 range)."""
    score = 0.0
    # Nesting depth (curly braces)
    max_depth = 0
    depth = 0
    for ch in source:
        if ch == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "}":
            depth = max(0, depth - 1)
    score += min(max_depth / 6.0, 1.0) * 0.3

    # Construct diversity
    constructs_used = sum(1 for c in TOKE_CONSTRUCTS if c in source)
    score += min(constructs_used / len(TOKE_CONSTRUCTS), 1.0) * 0.3

    # Length (longer code tends to be harder to generate without instruction)
    score += min(len(source) / 500.0, 1.0) * 0.2

    # Statement count (semicolons)
    stmt_count = source.count(";")
    score += min(stmt_count / 15.0, 1.0) * 0.2

    return score


def _instruction_length_ratio(entry: dict[str, Any]) -> float:
    """Ratio of instruction context to response length.

    We treat the python reference as the "instruction" (natural-language
    equivalent) and the toke source as the "response".  A high ratio
    means the instruction carries a lot of information relative to
    the response -- approximating high IFD.
    """
    refs = entry.get("references", {})
    py_source = refs.get("python_source", "")
    tk_source = entry.get("tk_source", "")
    if not tk_source:
        return 0.5
    return min(len(py_source) / max(len(tk_source), 1), 3.0) / 3.0


def _keyword_diversity(source: str) -> float:
    """Fraction of toke keywords present in source."""
    tokens = set(re.findall(r"[a-z]+", source.lower()))
    hits = tokens & TOKE_KEYWORDS
    return len(hits) / max(len(TOKE_KEYWORDS), 1)


def compute_proxy_ifd(entry: dict[str, Any]) -> float:
    """Compute a heuristic proxy IFD score in [0, 1].

    Combines code complexity, instruction-length ratio, and keyword
    diversity.  Not a real IFD, but correlated enough for dry-run
    experiments.
    """
    source = entry.get("tk_source", "")
    cx = _code_complexity(source)
    ilr = _instruction_length_ratio(entry)
    kd = _keyword_diversity(source)
    # Weighted combination -- complexity and instruction ratio matter most.
    return 0.45 * cx + 0.35 * ilr + 0.20 * kd


# ---------------------------------------------------------------------------
# K-Means clustering (no external dependency)
# ---------------------------------------------------------------------------

def _kmeans_1d(values: list[float], k: int, seed: int, max_iter: int = 100
               ) -> list[int]:
    """Simple 1-D K-Means returning cluster labels."""
    rng = random.Random(seed)
    n = len(values)
    if n == 0:
        return []
    if k >= n:
        return list(range(n))

    # Initialise centroids via k evenly spaced quantiles + jitter
    sorted_vals = sorted(values)
    centroids = [
        sorted_vals[int(i * (n - 1) / (k - 1))] + rng.gauss(0, 1e-6)
        for i in range(k)
    ]

    labels = [0] * n
    for _ in range(max_iter):
        # Assignment
        changed = False
        for i, v in enumerate(values):
            best = min(range(k), key=lambda c: abs(v - centroids[c]))
            if best != labels[i]:
                labels[i] = best
                changed = True
        if not changed:
            break
        # Update centroids
        for c in range(k):
            members = [values[i] for i in range(n) if labels[i] == c]
            if members:
                centroids[c] = sum(members) / len(members)

    return labels


def _try_sklearn_kmeans(values: list[float], k: int, seed: int
                        ) -> list[int] | None:
    """Attempt K-Means via scikit-learn; return None on import failure."""
    try:
        from sklearn.cluster import KMeans  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None
    X = np.array(values).reshape(-1, 1)
    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    km.fit(X)
    return km.labels_.tolist()


def cluster_samples(values: list[float], k: int, seed: int) -> list[int]:
    """Cluster 1-D IFD scores into *k* groups.

    Uses scikit-learn when available, falls back to manual implementation.
    """
    labels = _try_sklearn_kmeans(values, k, seed)
    if labels is not None:
        return labels
    return _kmeans_1d(values, k, seed)


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------

def select_top_m_per_cluster(
    entries: list[dict[str, Any]],
    labels: list[int],
    scores: list[float],
    rate: float,
) -> list[dict[str, Any]]:
    """Select top *rate* fraction from each cluster for balanced coverage."""
    clusters: dict[int, list[tuple[float, int]]] = collections.defaultdict(list)
    for idx, (lbl, sc) in enumerate(zip(labels, scores)):
        clusters[lbl].append((sc, idx))

    selected_indices: set[int] = set()
    for _lbl, members in clusters.items():
        members.sort(key=lambda x: x[0], reverse=True)
        take = max(1, int(math.ceil(len(members) * rate)))
        for _, idx in members[:take]:
            selected_indices.add(idx)

    return [entries[i] for i in sorted(selected_indices)]


# ---------------------------------------------------------------------------
# Diversity / quality metrics for a subset
# ---------------------------------------------------------------------------

def compute_diversity_metrics(entries: list[dict[str, Any]],
                              scores: list[float]) -> dict[str, Any]:
    """Compute diversity metrics for a subset of entries."""
    if not entries:
        return {"count": 0}

    # Task-ID diversity
    task_ids = {e["task_id"] for e in entries}

    # Phase distribution
    phases: dict[str, int] = collections.Counter(
        e.get("phase", "?") for e in entries
    )

    # Score statistics
    s_mean = sum(scores) / len(scores)
    s_var = sum((s - s_mean) ** 2 for s in scores) / len(scores)
    s_std = math.sqrt(s_var)

    # Token count stats
    tk_counts = [e.get("tk_tokens", 0) for e in entries]
    tk_mean = sum(tk_counts) / len(tk_counts) if tk_counts else 0

    # Judge score stats
    j_scores = [e.get("judge", {}).get("score", 0) for e in entries]
    j_mean = sum(j_scores) / len(j_scores) if j_scores else 0

    return {
        "count": len(entries),
        "unique_task_ids": len(task_ids),
        "phase_distribution": dict(phases),
        "ifd_mean": round(s_mean, 4),
        "ifd_std": round(s_std, 4),
        "ifd_min": round(min(scores), 4),
        "ifd_max": round(max(scores), 4),
        "tk_tokens_mean": round(tk_mean, 1),
        "judge_score_mean": round(j_mean, 4),
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    corpus_dir = Path(args.corpus_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load corpus -------------------------------------------------------
    print(f"Loading corpus from {corpus_dir} ...")
    entries = load_corpus(corpus_dir)
    if not entries:
        print("ERROR: no corpus entries found.", file=sys.stderr)
        sys.exit(1)
    print(f"  Loaded {len(entries)} entries.")

    # --- Compute / load IFD scores ----------------------------------------
    scores: list[float] = []
    if args.dry_run:
        print("Computing proxy IFD scores (dry-run heuristics) ...")
        scores = [compute_proxy_ifd(e) for e in entries]
    elif args.scores_file:
        print(f"Loading pre-computed IFD scores from {args.scores_file} ...")
        score_map: dict[str, float] = {}
        with open(args.scores_file) as f:
            for line in f:
                rec = json.loads(line)
                score_map[rec["task_id"]] = float(rec["ifd_score"])
        for e in entries:
            tid = e["task_id"]
            if tid not in score_map:
                print(f"  WARNING: no score for {tid}, using 0.5",
                      file=sys.stderr)
            scores.append(score_map.get(tid, 0.5))
    else:
        print("ERROR: specify --dry-run or --scores-file.", file=sys.stderr)
        sys.exit(1)

    # --- K-Means clustering ------------------------------------------------
    k = args.k_clusters
    print(f"Clustering into {k} groups ...")
    labels = cluster_samples(scores, k, args.seed)

    # --- Write scored entries -----------------------------------------------
    scores_path = output_dir / "ifd_scores.jsonl"
    with open(scores_path, "w") as f:
        for entry, sc, lbl in zip(entries, scores, labels):
            rec = {
                "task_id": entry["task_id"],
                "entry_id": entry.get("id", entry["task_id"]),
                "ifd_score": round(sc, 6),
                "cluster_id": lbl,
            }
            f.write(json.dumps(rec) + "\n")
    print(f"  Wrote {len(entries)} scores to {scores_path}")

    # --- Selection experiments ---------------------------------------------
    selection_rates = [float(r) for r in args.selection_rates.split(",")]
    report: dict[str, Any] = {
        "total_entries": len(entries),
        "k_clusters": k,
        "seed": args.seed,
        "mode": "dry-run" if args.dry_run else "precomputed",
        "subsets": {},
    }

    best_rate: float | None = None
    best_diversity_score: float = -1.0

    for rate in selection_rates:
        tag = f"{int(rate * 100)}"
        print(f"\n--- Selection rate {tag}% ---")
        selected = select_top_m_per_cluster(entries, labels, scores, rate)
        sel_scores = [scores[entries.index(e)] for e in selected] if rate < 1.0 else scores

        # If 100%, just use all
        if rate >= 1.0:
            selected = entries
            sel_scores = scores

        out_path = output_dir / f"ifd_selected_{tag}.jsonl"
        with open(out_path, "w") as f:
            for e in selected:
                f.write(json.dumps({
                    "task_id": e["task_id"],
                    "entry_id": e.get("id", e["task_id"]),
                    "tk_source": e["tk_source"],
                    "references": e.get("references", {}),
                }) + "\n")
        print(f"  Selected {len(selected)}/{len(entries)} "
              f"({len(selected)/len(entries)*100:.1f}%), wrote {out_path}")

        metrics = compute_diversity_metrics(selected, sel_scores)
        report["subsets"][tag] = metrics

        # Heuristic: prefer subsets that retain high IFD mean with decent
        # task-ID coverage.  Normalise both to [0,1] and combine.
        full_task_ids = len({e["task_id"] for e in entries})
        coverage = metrics["unique_task_ids"] / max(full_task_ids, 1)
        div_score = 0.5 * metrics["ifd_mean"] + 0.5 * coverage
        if rate < 1.0 and div_score > best_diversity_score:
            best_diversity_score = div_score
            best_rate = rate

        print(f"  Metrics: {json.dumps(metrics, indent=2)}")

    # --- Recommendation ---------------------------------------------------
    if best_rate is not None:
        rec_tag = f"{int(best_rate * 100)}%"
        report["recommendation"] = {
            "optimal_rate": best_rate,
            "rationale": (
                f"The {rec_tag} subset maximises the trade-off between "
                f"IFD quality (high-value samples) and task-ID coverage."
            ),
        }
        print(f"\nRecommendation: use the {rec_tag} subset.")
    else:
        report["recommendation"] = {
            "optimal_rate": 1.0,
            "rationale": "Only full corpus was evaluated.",
        }

    # --- Write report ------------------------------------------------------
    report_path = output_dir / "ifd_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"\nReport written to {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="IFD-based data selection for toke training corpus.",
    )
    parser.add_argument(
        "--corpus-dir", required=True,
        help="Root directory of the toke corpus.",
    )
    parser.add_argument(
        "--scores-file", default=None,
        help="JSONL file with pre-computed IFD scores (task_id, ifd_score).",
    )
    parser.add_argument(
        "--output-dir", default="data",
        help="Directory for output files (default: data).",
    )
    parser.add_argument(
        "--selection-rates", default="1.0,0.6,0.4,0.2",
        help="Comma-separated selection rates (default: 1.0,0.6,0.4,0.2).",
    )
    parser.add_argument(
        "--k-clusters", type=int, default=5,
        help="Number of K-Means clusters (default: 5).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Use proxy IFD heuristics instead of real model scores.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42).",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
