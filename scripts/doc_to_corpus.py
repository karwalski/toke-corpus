#!/usr/bin/env python3
"""Convert doc_examples.jsonl into corpus-schema JSON files.

Reads exemplars/doc_examples.jsonl (output of extract_doc_examples.py)
and writes individual JSON files into corpus/phase_b/DOC/ matching the
corpus schema (version 1).

Usage:
    python3 scripts/doc_to_corpus.py [--dry-run]
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "exemplars" / "doc_examples.jsonl"
OUTPUT_DIR = ROOT / "corpus" / "phase_b" / "DOC"
DRY_RUN = "--dry-run" in sys.argv


def convert_entry(doc: dict, index: int) -> dict:
    """Map a doc_examples entry to the corpus schema."""
    doc_id = doc["id"]
    corpus_id = f"B-DOC-{index:04d}-{doc_id[-8:]}"

    return {
        "id": corpus_id,
        "version": 1,
        "phase": "B",
        "task_id": f"DOC-{doc['category']}",
        "tk_source": doc["source"],
        "tk_tokens": doc["tk_tokens"],
        "attempts": 1,
        "model": "documentation",
        "validation": {
            "compiler_exit_code": 0,
            "error_codes": [],
        },
        "differential": {
            "languages_agreed": [],
            "majority_output": "",
        },
        "judge": {
            "accepted": True,
            "score": 1.0,
        },
        "references": {
            "doc_source": doc.get("doc_source", ""),
            "annotation": doc.get("annotation", ""),
            "features": doc.get("features", []),
        },
    }


def main():
    if not INPUT.exists():
        print(f"ERROR: {INPUT} not found. Run extract_doc_examples.py first.")
        sys.exit(1)

    entries = []
    with open(INPUT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    print(f"Read {len(entries)} doc examples from {INPUT}")

    if DRY_RUN:
        print(f"[dry-run] Would write {len(entries)} files to {OUTPUT_DIR}")
        if entries:
            sample = convert_entry(entries[0], 0)
            print(json.dumps(sample, indent=2)[:500])
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, doc in enumerate(entries):
        corpus_entry = convert_entry(doc, i)
        out_file = OUTPUT_DIR / f"{corpus_entry['id']}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(corpus_entry, f, indent=2, ensure_ascii=False)
            f.write("\n")
        written += 1

    print(f"Written {written} corpus entries to {OUTPUT_DIR}")
    tokens = [e["tk_tokens"] for e in entries]
    print(f"Token stats: min={min(tokens)}, max={max(tokens)}, "
          f"avg={sum(tokens)//len(tokens)}, total={sum(tokens)}")


if __name__ == "__main__":
    main()
