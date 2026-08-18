#!/usr/bin/env python3
"""Package error triple corpus entries as instruction-tuning examples.

Story 10.2.4: Reads ERR-TRIPLE-*/ and BIFI-*/ entries from
corpus/phase2_deduplicated/ and produces two instruction-tuning examples
per entry (generation + repair), written to data/error_triplets_instruct.jsonl.
"""

import json
import glob
import os
import re
import sys
from collections import Counter
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parent.parent
DEDUP_DIR = CORPUS_ROOT / "corpus" / "phase2_deduplicated"
OUTPUT_FILE = CORPUS_ROOT / "data" / "error_triplets_instruct.jsonl"


def camel_to_words(name: str) -> str:
    """Convert camelCase or PascalCase to lowercase words."""
    # Insert space before uppercase letters preceded by lowercase
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    # Insert space before sequences of uppercase followed by lowercase
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return s.lower().strip()


def derive_task_description(tk_source: str) -> str:
    """Derive a human-readable task description from the fixed toke source.

    Uses the module name (m=...) and function names (f=...) to build
    a description like "Write a toke program that computes a sum".
    """
    mod_match = re.search(r"m=(\w+)", tk_source)
    fn_matches = re.findall(r"f=(\w+)", tk_source)

    module_name = mod_match.group(1) if mod_match else ""
    fn_names = fn_matches if fn_matches else []

    # Use the module name as the primary descriptor
    if module_name:
        words = camel_to_words(module_name)
    elif fn_names:
        words = camel_to_words(fn_names[0])
    else:
        return "Write a toke program"

    # Build a description; use function names for additional context
    # if they differ from the module name
    extra_fns = [camel_to_words(fn) for fn in fn_names
                 if camel_to_words(fn) != words]

    desc = f"Write a toke program that implements {words}"
    if extra_fns:
        desc += f" (with {', '.join(extra_fns)})"

    return desc


def process_entry(filepath: str):
    """Load a single corpus JSON and return (generation_example, repair_example) or None."""
    with open(filepath) as f:
        entry = json.load(f)

    tk_source = entry.get("tk_source", "")
    refs = entry.get("references", {})
    broken_source = refs.get("broken_source", "")
    diagnostic = refs.get("diagnostic", "")
    injection_type = refs.get("injection_type", "")

    if not tk_source or not broken_source or not diagnostic:
        return None

    task_desc = derive_task_description(tk_source)

    generation = {
        "instruction": task_desc,
        "input": "",
        "output": tk_source,
        "type": "generation",
        "injection_type": injection_type,
    }

    repair = {
        "instruction": f"Fix this toke program. Error: {diagnostic}",
        "input": broken_source,
        "output": tk_source,
        "type": "repair",
        "injection_type": injection_type,
    }

    return generation, repair


def main():
    # Collect all source directories
    err_dirs = sorted(glob.glob(str(DEDUP_DIR / "ERR-TRIPLE-*")))
    bifi_dirs = sorted(glob.glob(str(DEDUP_DIR / "BIFI-*")))
    all_dirs = err_dirs + bifi_dirs

    if not all_dirs:
        print("ERROR: No ERR-TRIPLE-* or BIFI-* directories found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(err_dirs)} ERR-TRIPLE dirs, {len(bifi_dirs)} BIFI dirs")

    # Collect all JSON files
    all_files = []
    for d in all_dirs:
        all_files.extend(sorted(glob.glob(os.path.join(d, "*.json"))))

    print(f"Total JSON files: {len(all_files)}")

    # Process
    gen_count = 0
    repair_count = 0
    skipped = 0
    type_counts = Counter()  # injection_type -> count of pairs

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w") as out:
        for fpath in all_files:
            result = process_entry(fpath)
            if result is None:
                skipped += 1
                continue

            gen_ex, rep_ex = result
            inj_type = gen_ex["injection_type"]

            out.write(json.dumps(gen_ex, ensure_ascii=False) + "\n")
            out.write(json.dumps(rep_ex, ensure_ascii=False) + "\n")

            gen_count += 1
            repair_count += 1
            type_counts[inj_type] += 1

    # Summary
    total = gen_count + repair_count
    print(f"\n{'='*60}")
    print(f"Instruction-tuning examples written to:")
    print(f"  {OUTPUT_FILE}")
    print(f"{'='*60}")
    print(f"Total examples:      {total}")
    print(f"  Generation:        {gen_count}")
    print(f"  Repair:            {repair_count}")
    print(f"  Skipped entries:   {skipped}")
    print(f"\nPer injection type (pairs):")
    for itype, count in sorted(type_counts.items()):
        print(f"  {itype:30s}  {count:>6} pairs  ({count*2:>6} examples)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
