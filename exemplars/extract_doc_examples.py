#!/usr/bin/env python3
"""
Extract validated toke code examples from the documentation and save them
as corpus exemplars for the next training run.

Usage:
    python3 extract_doc_examples.py [--dry-run]

Output: exemplars/doc_examples.jsonl
Each line is a JSON exemplar with: id, category, features, source, annotation,
tk_tokens, doc_source (file path + block index).
"""

import sys
import re
import json
import hashlib
import subprocess
import tempfile
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TKC = "/Users/matthew.watt/tk/toke/tkc"
DOCS_ROOT = Path("/Users/matthew.watt/tk/toke-web/src/content/docs")
OUTPUT = Path(__file__).parent / "doc_examples.jsonl"
DRY_RUN = "--dry-run" in sys.argv

# ---------------------------------------------------------------------------
# Token counting (reuse corpus utility)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from store.writer import count_tokens
except ImportError:
    try:
        import tiktoken
        _enc = tiktoken.get_encoding("cl100k_base")
        def count_tokens(text: str) -> int:
            return len(_enc.encode(text))
    except ImportError:
        def count_tokens(text: str) -> int:
            return len(text.split())


# ---------------------------------------------------------------------------
# Feature detection
# ---------------------------------------------------------------------------

FEATURE_PATTERNS = [
    (r'\bf=\w+\s*\(', 'function_def'),
    (r'\bt=\$\w+\{', 'type_decl'),
    (r'\bi=\w+:std\.', 'stdlib_import'),
    (r'\bi=\w+:ooke\.', 'ooke_import'),
    (r'\blp\s*\(', 'loop'),
    (r'\bif\s*\(', 'conditional'),
    (r'\|{Ok:', 'error_match'),
    (r'!\$\w+', 'error_propagate'),
    (r'@\(', 'array_or_map_literal'),
    (r'\.get\(', 'subscript'),
    (r'\$str\b', 'string_type'),
    (r'\blet\s+\w+=mut\.', 'mutable_binding'),
    (r'\blet\s+\w+=', 'immutable_binding'),
    (r'<[^<]', 'return_op'),
    (r'as\s+\w+\d*', 'cast'),
    (r'\barena\s*{', 'arena_block'),
]


def detect_features(source: str) -> list[str]:
    seen = set()
    for pattern, name in FEATURE_PATTERNS:
        if re.search(pattern, source) and name not in seen:
            seen.add(name)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Category from file path
# ---------------------------------------------------------------------------

def category_from_path(md_path: Path) -> str:
    rel = md_path.relative_to(DOCS_ROOT)
    parts = list(rel.parts)
    # Strip .md extension from last part
    parts[-1] = parts[-1].replace('.md', '').replace('.mdx', '')
    # Strip numeric prefix from learn lessons: 04-collections -> collections
    parts[-1] = re.sub(r'^\d+-', '', parts[-1])
    # Join with underscore
    return '_'.join(parts).replace('-', '_')


# ---------------------------------------------------------------------------
# Context extraction: grab surrounding prose for annotation
# ---------------------------------------------------------------------------

def extract_context(md_content: str, block_start: int, max_chars: int = 300) -> str:
    """Find the nearest preceding heading and paragraph before this block."""
    before = md_content[:block_start]
    # Find last heading
    heading_match = list(re.finditer(r'^#{1,3}\s+(.+)$', before, re.MULTILINE))
    heading = heading_match[-1].group(1).strip() if heading_match else ''
    # Find last non-empty paragraph (not a heading, not code)
    paras = [p.strip() for p in re.split(r'\n\n+', before) if p.strip()
             and not p.strip().startswith('#')
             and not p.strip().startswith('```')
             and not p.strip().startswith('|')]
    prose = paras[-1][:max_chars] if paras else ''
    if heading and prose:
        return f"{heading} — {prose}"
    return heading or prose or 'Toke documentation example.'


# ---------------------------------------------------------------------------
# Compile check
# ---------------------------------------------------------------------------

def passes_check(source: str) -> bool:
    code = source.strip()
    if not code.startswith('m='):
        code = 'm=test;\n' + code
    with tempfile.NamedTemporaryFile(suffix='.tk', mode='w', delete=False) as f:
        f.write(code)
        fname = f.name
    try:
        r = subprocess.run([TKC, '--check', fname],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        os.unlink(fname)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def make_id(category: str, source: str, index: int) -> str:
    h = hashlib.sha1(source.encode()).hexdigest()[:8]
    safe_cat = re.sub(r'[^a-z0-9_]', '_', category)[:30]
    return f"doc_{safe_cat}_{index:04d}_{h}"


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_all():
    entries = []
    total_blocks = 0
    skipped_fail = 0
    skipped_short = 0

    for md_path in sorted(DOCS_ROOT.rglob('*.md')) + sorted(DOCS_ROOT.rglob('*.mdx')):
        content = md_path.read_text(encoding='utf-8', errors='replace')
        category = category_from_path(md_path)

        # Find all ```toke blocks (not ```toke-legacy or ```text)
        for m in re.finditer(r'```toke\n(.*?)```', content, re.DOTALL):
            total_blocks += 1
            source = m.group(1).strip()

            # Skip trivially short snippets (single line, < 20 chars) — not useful for training
            if len(source) < 20 or '\n' not in source:
                skipped_short += 1
                continue

            # Only gate: does it compile? <!-- skip-check --> is a validate-script
            # concern (intentional error examples); the corpus only needs compilable code.
            # Intentional-error blocks in errors.md will still be excluded because they
            # genuinely fail tkc --check.
            if not passes_check(source):
                skipped_fail += 1
                continue

            # Ensure m= header for canonical form
            if not source.startswith('m='):
                source = 'm=test;\n' + source

            annotation = extract_context(content, m.start(), max_chars=400)
            features = detect_features(source)
            idx = len(entries)
            entry = {
                'id': make_id(category, source, idx),
                'category': category,
                'features': features,
                'source': source,
                'annotation': annotation,
                'tk_tokens': count_tokens(source),
                'doc_source': str(md_path.relative_to(DOCS_ROOT)),
            }
            entries.append(entry)

    return entries, total_blocks, skipped_fail, skipped_short


def main():
    print(f"Scanning {DOCS_ROOT} ...")
    entries, total, fail, short = extract_all()

    print(f"\nResults:")
    print(f"  Total ```toke blocks found : {total}")
    print(f"  Skipped (too short)        : {short}")
    print(f"  Skipped (compile fail)     : {fail}")
    print(f"  Saved as exemplars         : {len(entries)}")

    if DRY_RUN:
        print(f"\n[dry-run] Would write {len(entries)} entries to {OUTPUT}")
        if entries:
            print("\nSample entry:")
            print(json.dumps(entries[0], indent=2)[:600])
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"\nWritten to {OUTPUT}")
    print(f"Token stats:")
    tokens = [e['tk_tokens'] for e in entries]
    if tokens:
        print(f"  Min: {min(tokens)}, Max: {max(tokens)}, "
              f"Avg: {sum(tokens)//len(tokens)}, Total: {sum(tokens)}")

    # Category breakdown
    from collections import Counter
    cats = Counter(e['category'] for e in entries)
    print(f"\nTop categories:")
    for cat, count in cats.most_common(15):
        print(f"  {cat:40s} {count}")


if __name__ == '__main__':
    main()
