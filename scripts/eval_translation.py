#!/usr/bin/env python3
"""Evaluate translation model on the test split.

Loads the test split, runs predictions through the model (or a placeholder),
checks each with `tkc --check`, and computes Pass@1, exact match rate, and
BLEU score.

Usage::

    python scripts/eval_translation.py \\
        --test-file data/translation_test.jsonl \\
        --adapter-path adapters/translation-lora

Story 9.3.3 -- Python-to-toke translation fine-tuning stage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def check_compiles(toke_source: str) -> bool:
    """Check if toke source compiles using tkc --check."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toke", delete=False
        ) as f:
            f.write(toke_source)
            f.flush()
            result = subprocess.run(
                ["tkc", "--check", f.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  tkc check error: {e}", file=sys.stderr)
        return False


def exact_match(predicted: str, reference: str) -> bool:
    """Check exact match after normalising whitespace."""
    return predicted.strip() == reference.strip()


def compute_bleu(predictions: list[str], references: list[str]) -> float | None:
    """Compute corpus BLEU using sacrebleu if available."""
    try:
        import sacrebleu

        bleu = sacrebleu.corpus_bleu(predictions, [references])
        return round(bleu.score, 2)
    except ImportError:
        # Fallback: simple 4-gram BLEU approximation
        try:
            return _simple_bleu(predictions, references)
        except Exception:
            return None


def _simple_bleu(predictions: list[str], references: list[str]) -> float:
    """Minimal BLEU approximation when sacrebleu is unavailable."""
    from collections import Counter
    import math

    total_bp_penalty = 0.0
    ngram_precisions = [0.0, 0.0, 0.0, 0.0]
    ngram_totals = [0, 0, 0, 0]

    ref_len = 0
    pred_len = 0

    for pred, ref in zip(predictions, references):
        pred_tokens = pred.split()
        ref_tokens = ref.split()
        pred_len += len(pred_tokens)
        ref_len += len(ref_tokens)

        for n in range(1, 5):
            pred_ngrams = Counter(
                tuple(pred_tokens[i : i + n]) for i in range(len(pred_tokens) - n + 1)
            )
            ref_ngrams = Counter(
                tuple(ref_tokens[i : i + n]) for i in range(len(ref_tokens) - n + 1)
            )
            clipped = sum(
                min(count, ref_ngrams[ng]) for ng, count in pred_ngrams.items()
            )
            total = sum(pred_ngrams.values())
            if total > 0:
                ngram_precisions[n - 1] += clipped
                ngram_totals[n - 1] += total

    # Brevity penalty
    if pred_len == 0:
        return 0.0
    bp = min(1.0, math.exp(1 - ref_len / pred_len)) if pred_len > 0 else 0.0

    # Geometric mean of precisions
    log_avg = 0.0
    for i in range(4):
        if ngram_totals[i] == 0 or ngram_precisions[i] == 0:
            return 0.0
        log_avg += 0.25 * math.log(ngram_precisions[i] / ngram_totals[i])

    return round(bp * math.exp(log_avg) * 100, 2)


# ---------------------------------------------------------------------------
# Prediction (placeholder)
# ---------------------------------------------------------------------------


def predict_translation(
    python_source: str,
    adapter_path: str | None = None,
) -> str:
    """Generate toke translation from Python source.

    This is a placeholder that returns an empty string. Replace with
    actual model inference using mlx-lm or another backend:

        from mlx_lm import load, generate
        model, tokenizer = load("model-path", adapter_path=adapter_path)
        prompt = format_prompt(python_source)
        return generate(model, tokenizer, prompt=prompt, max_tokens=2048)
    """
    # TODO: integrate with mlx-lm inference once model is trained
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate translation model on test split."
    )
    parser.add_argument(
        "--test-file",
        type=Path,
        default=Path("data/translation_test.jsonl"),
        help="Test split JSONL file",
    )
    parser.add_argument(
        "--adapter-path",
        type=str,
        default=None,
        help="Path to LoRA adapter (optional, placeholder mode if absent)",
    )
    parser.add_argument(
        "--skip-compile-check",
        action="store_true",
        help="Skip tkc --check compilation testing",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=None,
        help="Limit evaluation to N examples (for quick testing)",
    )
    args = parser.parse_args()

    if not args.test_file.is_file():
        print(f"Error: test file not found: {args.test_file}", file=sys.stderr)
        sys.exit(1)

    # Load test examples
    examples = []
    with open(args.test_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            examples.append(json.loads(line))

    if args.max_examples:
        examples = examples[: args.max_examples]

    print(f"Evaluating {len(examples)} test examples...", file=sys.stderr)

    predictions = []
    references = []
    compile_results = []
    exact_matches = 0

    for i, ex in enumerate(examples):
        messages = ex["messages"]
        # Extract user content (Python source) and reference (toke source)
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        reference = next(m["content"] for m in messages if m["role"] == "assistant")

        # Extract Python source from markdown block
        python_source = user_msg
        if "```python" in user_msg:
            start = user_msg.index("```python") + len("```python\n")
            end = user_msg.index("```", start)
            python_source = user_msg[start:end].strip()

        # Generate prediction
        predicted = predict_translation(python_source, args.adapter_path)
        predictions.append(predicted)
        references.append(reference)

        # Check compilation
        if not args.skip_compile_check and predicted.strip():
            compiles = check_compiles(predicted)
            compile_results.append(compiles)
        elif predicted.strip():
            compile_results.append(None)

        # Check exact match
        if exact_match(predicted, reference):
            exact_matches += 1

        if (i + 1) % 10 == 0:
            print(f"  Processed {i + 1}/{len(examples)}...", file=sys.stderr)

    # Compute metrics
    total = len(examples)
    n_compiled = sum(1 for c in compile_results if c is True)
    n_checked = sum(1 for c in compile_results if c is not None)

    pass_at_1 = (n_compiled / n_checked * 100) if n_checked > 0 else 0.0
    exact_rate = (exact_matches / total * 100) if total > 0 else 0.0
    bleu = compute_bleu(predictions, references)

    # Report
    print("\n=== Translation Evaluation Results ===")
    print(f"Total examples:    {total}")
    print(f"Pass@1 (compiles): {n_compiled}/{n_checked} ({pass_at_1:.1f}%)")
    print(f"Exact match:       {exact_matches}/{total} ({exact_rate:.1f}%)")
    if bleu is not None:
        print(f"BLEU score:        {bleu}")
    else:
        print("BLEU score:        N/A (sacrebleu not installed)")
    print(f"\nTarget: Pass@1 >= 70%")

    if pass_at_1 >= 70.0:
        print("STATUS: PASS")
    elif n_checked == 0:
        print("STATUS: NO PREDICTIONS (placeholder model)")
    else:
        print("STATUS: BELOW TARGET")

    # Write results JSON
    results = {
        "total_examples": total,
        "pass_at_1_count": n_compiled,
        "pass_at_1_checked": n_checked,
        "pass_at_1_pct": round(pass_at_1, 2),
        "exact_match_count": exact_matches,
        "exact_match_pct": round(exact_rate, 2),
        "bleu": bleu,
    }
    print(f"\n{json.dumps(results)}")


if __name__ == "__main__":
    main()
