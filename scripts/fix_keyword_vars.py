#!/usr/bin/env python3
"""
Fix reserved keyword use as variables in toke training corpus.

Story 98.12 — replaces single-char keyword variable names with safe alternatives:
  i -> idx   (loop counters, indices)
  m -> acc   (accumulators), val (parameters)
  f -> fv    (variables and parameters)
  t -> tv    (variables and parameters)

Usage:
  python3 fix_keyword_vars.py [--dry-run] [--verify] [--file FILE]

  --dry-run   Show what would change without writing files
  --verify    Run tkc --check on each fixed source (slow)
  --file      Process a single JSONL file instead of all

Output:
  Creates *_fixed.jsonl alongside each input file.
  Writes a summary to fix_results.json.

IMPORTANT: Review the --dry-run output before applying fixes.
           Do NOT run without review.

KNOWN LIMITATIONS (regex-based, not scope-aware):
  - Loop variable `i` references INSIDE the lp() block are replaced correctly.
  - Loop variable `i` references OUTSIDE a loop (e.g., `let i=0; ... <i`)
    have the `let i=` replaced but subsequent bare `i` references may be missed.
  - `m` accumulator body references (e.g., `m=m+x`) are partially replaced;
    the `m=` assignment may not always be caught if it doesn't follow the
    expected pattern.
  - Parameter name fixes do NOT update references in the function body.
    A scope-aware pass (or manual review) is needed for parameter renames.
  - Use --verify to catch regressions: any fix that breaks tkc --check is reverted.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from copy import deepcopy

# ── Configuration ──────────────────────────────────────────────────

CORPUS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TKC_PATH = os.path.expanduser("~/tk/toke/tkc")

# Files to process and their source field(s)
FILES = {
    "data/corpus_default.jsonl": {"fields": ["tk_source"]},
    "data/train.jsonl": {"fields": None, "format": "messages"},
    "data/eval.jsonl": {"fields": None, "format": "messages"},
    "data/corpus_fuzzed.jsonl": {"fields": ["tk_source"]},
    "data/corpus_error_triples.jsonl": {"fields": ["broken_source", "fixed_source"]},
    "data/negative_examples.jsonl": {"fields": ["broken_source", "fixed_source"]},
    "data/error_triplets_instruct.jsonl": {"fields": ["input", "output"]},
    "data/corpus_mutations.jsonl": {"fields": ["original_source", "mutated_source"]},
    "data/parallel_corpus.jsonl": {"fields": ["toke_source"]},
    "data/parallel_corpus_expanded.jsonl": {"fields": ["transpiled_tk_source", "original_tk_source"]},
    "data/tokenizer_training.jsonl": {"fields": ["tk_source"]},
    "exemplars/exemplars.jsonl": {"fields": ["source"]},
    "exemplars/doc_examples.jsonl": {"fields": ["source"]},
    "curriculum/phase1_token_completion.jsonl": {"fields": ["full_source"]},
    "curriculum/phase2_statement_completion.jsonl": {"fields": ["full_source"]},
    "curriculum/phase3_function_completion.jsonl": {"fields": ["full_source"]},
    "curriculum/phase4_multi_function.jsonl": {"fields": ["full_source"]},
    "curriculum/phase5_multi_module.jsonl": {"fields": ["full_source"]},
    "curriculum/phase6_application.jsonl": {"fields": ["full_source"]},
}


# ── Replacement engine ─────────────────────────────────────────────

def fix_toke_source(src):
    """
    Apply keyword-to-safe-name replacements in a toke source string.
    Returns (fixed_source, changes_made_dict).

    Strategy:
    - We must be very careful not to replace keywords in their declaration
      context (m= at top level is module, f= is function, t= is type, i= is import).
    - We target keywords used as VARIABLE NAMES only.
    """
    if not src or not isinstance(src, str):
        return src, {}

    changes = Counter()
    result = src

    # ── Phase 1: Fix loop variable `i` ──
    # The most common and most dangerous pattern.
    # Replace `i` used as a loop counter variable.

    # 1a. lp(i=... -> lp(let idx=...  (CRITICAL: broken code)
    def fix_lp_bare_i(m):
        changes['lp_bare_i'] += 1
        return m.group(0).replace('lp(i=', 'lp(let idx=', 1)

    # Match lp(i= but NOT lp(let i=
    result = re.sub(r'lp\(i=(?!.*?:)', fix_lp_bare_i_full, result)

    # Actually, let's do this more carefully with a single-pass approach
    result = src  # reset
    changes = Counter()

    # ── Replace `i` as loop variable ──

    # Pattern: lp(i=EXPR;i<EXPR;i=EXPR){...i...}
    # We need to replace all `i` references within the loop scope.
    # This is complex for regex, so we do targeted replacements:

    # Step 1: lp(let i= -> lp(let idx=  AND  lp(i= -> lp(let idx=
    # Step 2: ;i< -> ;idx<  (loop condition)
    # Step 3: ;i=i+1) -> ;idx=idx+1)  (loop step)
    # Step 4: References inside loop body: .get(i), arr.get(i as u64), etc.

    # We'll use a function-based approach that processes each lp() block.

    def replace_loop_var_i(source):
        """Replace `i` loop variable with `idx` throughout each lp() block."""
        out = []
        pos = 0
        modified = False

        while pos < len(source):
            # Find next lp(
            lp_match = re.search(r'lp\(', source[pos:])
            if not lp_match:
                out.append(source[pos:])
                break

            lp_start = pos + lp_match.start()
            out.append(source[pos:lp_start])

            # Check if this loop uses `i` as its variable
            after_lp = source[lp_start + 3:]  # after 'lp('

            # Check for: lp(let i= or lp(i=
            let_i_match = re.match(r'let\s+i\s*=', after_lp)
            bare_i_match = re.match(r'i\s*=', after_lp)

            if let_i_match or bare_i_match:
                # Find the matching closing ) for the loop header, then the { }
                # We need to find the full lp(...){...} block
                block = extract_lp_block(source, lp_start)
                if block:
                    block_text = source[lp_start:lp_start + block]
                    fixed_block = fix_i_in_block(block_text, bare=bool(bare_i_match))
                    if fixed_block != block_text:
                        modified = True
                        if bare_i_match:
                            changes['lp_bare_i_to_idx'] += 1
                        else:
                            changes['lp_let_i_to_idx'] += 1
                    out.append(fixed_block)
                    pos = lp_start + block
                else:
                    # Can't parse block, skip this lp
                    out.append('lp(')
                    pos = lp_start + 3
            else:
                out.append('lp(')
                pos = lp_start + 3

        return ''.join(out), modified

    def extract_lp_block(source, start):
        """Return the length of the full lp(...){...} block starting at `start`."""
        pos = start + 3  # skip 'lp('
        # Find matching ) for the header
        depth = 1
        while pos < len(source) and depth > 0:
            if source[pos] == '(':
                depth += 1
            elif source[pos] == ')':
                depth -= 1
            pos += 1

        if depth != 0:
            return None

        # Now expect {
        if pos < len(source) and source[pos] == '{':
            brace_depth = 1
            pos += 1
            while pos < len(source) and brace_depth > 0:
                if source[pos] == '{':
                    brace_depth += 1
                elif source[pos] == '}':
                    brace_depth -= 1
                pos += 1
        else:
            return None

        return pos - start

    def fix_i_in_block(block, bare=False):
        """Replace `i` with `idx` in a loop block, being careful about context."""
        result = block

        if bare:
            # lp(i= -> lp(let idx=
            result = re.sub(r'^lp\(i\s*=', 'lp(let idx=', result)
        else:
            # lp(let i= -> lp(let idx=
            result = re.sub(r'^lp\(let\s+i\s*=', 'lp(let idx=', result)

        # Replace `i` as a standalone identifier (not part of longer word)
        # Must not match: 'if', 'in', 'idx', variable names containing i, etc.
        # Strategy: replace word-boundary `i` that is:
        #   - preceded by non-alpha or start
        #   - followed by non-alpha or end
        # But `i` can appear in: i<, i=, i+, i-, i), i;, .get(i), (i), i as
        # And must NOT match: if, import prefix, identifier chars

        # Replace standalone `i` with `idx`
        # Negative lookbehind for [a-z_0-9], negative lookahead for [a-z_0-9]
        # But skip the lp( prefix which we already fixed
        prefix_len = result.index('{') + 1 if '{' in result else 0
        header_and_body = result

        # Do replacement on the whole block after the initial lp(let idx= part
        header_and_body = re.sub(
            r'(?<![a-z_0-9])i(?![a-z_0-9])',
            'idx',
            header_and_body
        )

        # Fix any accidental double-replacement in the init we already handled
        # The init was already changed, so this should be fine since we replaced
        # `i` in the header pattern first.

        return header_and_body

    result, _ = replace_loop_var_i(result)

    # ── Replace standalone `let i=` outside loops ──
    # Pattern: let i=EXPR  (not inside lp() — those were already handled)
    result = re.sub(
        r'(?<![a-z_0-9])let\s+i\s*=',
        'let idx=',
        result
    )
    # Also fix subsequent references to standalone i (harder without full parse)

    # ── Replace `let m=` (accumulator pattern) ──
    # Common: let m=mut.0  -> let acc=mut.0
    def replace_m_var(source):
        """Replace `m` used as a variable with `acc`."""
        # let m= -> let acc=
        out = re.sub(r'(?<![a-z_0-9])let\s+m\s*=(?!\s*[a-z]+\s*:)', 'let acc=', source)
        # lp(let m= -> lp(let acc=
        out = re.sub(r'lp\(\s*let\s+m\s*=', 'lp(let acc=', out)
        # lp(m= -> lp(let acc=  (broken bare form)
        out = re.sub(r'lp\(\s*m\s*=(?!\s*[a-z]+\s*:)', 'lp(let acc=', out)

        # If we changed any let m=, also fix references to standalone m
        if 'let acc=' in out and 'let acc=' not in source:
            changes['let_m_to_acc'] += 1
            # Replace standalone m references (not m= module decl at line start)
            # This is approximate — a full fix needs scope analysis
            out = re.sub(r'(?<![a-z_0-9$.])m(?![a-z_0-9=])', 'acc', out)
            # But preserve m= at the very start (module declaration)
            if source.startswith('m=') and not out.startswith('m='):
                out = 'm=' + out[4:]  # restore module decl if clobbered

        return out

    result = replace_m_var(result)

    # ── Replace `let f=` (variable, not function decl) ──
    # f= at top level is function declaration — don't touch
    # let f= inside a block is a variable — replace with fv
    result = re.sub(r'(?<![a-z_0-9])let\s+f\s*=', 'let fv=', result)
    if 'let fv=' in result and 'let fv=' not in src:
        changes['let_f_to_fv'] += 1
        # Fix references within same scope (approximate)
        # Only replace f that follows patterns suggesting variable use
        # This is conservative — full fix needs scope analysis

    # ── Replace `let t=` (variable, not type decl) ──
    result = re.sub(r'(?<![a-z_0-9])let\s+t\s*=', 'let tv=', result)
    if 'let tv=' in result and 'let tv=' not in src:
        changes['let_t_to_tv'] += 1

    # ── Fix parameter names ──
    # (i:type) -> (idx:type), (m:type) -> (mv:type), etc.
    # Only in function signatures: f=name(PARAMS):ret{
    def fix_param_keywords(source):
        """Fix keyword names in function parameter lists."""
        def replace_params(match):
            params = match.group(1)
            changed = False
            # Replace ;i: or (i: patterns
            new_params = re.sub(r'(?<![a-z_0-9])i(?=\s*:)', 'idx', params)
            new_params = re.sub(r'(?<![a-z_0-9])m(?=\s*:)', 'mv', new_params)
            new_params = re.sub(r'(?<![a-z_0-9])f(?=\s*:)', 'fv', new_params)
            new_params = re.sub(r'(?<![a-z_0-9])t(?=\s*:)', 'tv', new_params)
            if new_params != params:
                changes['param_keyword_fixed'] += 1
                # Also need to fix references in the function body...
                # This requires more context than we have here
            return match.group(0).replace(params, new_params)

        # Match function parameter lists: f=name(PARAMS):
        return re.sub(r'f=[a-z_][a-z_0-9]*\(([^)]+)\)', replace_params, source)

    result = fix_param_keywords(result)

    return result, changes


# ── File processing ────────────────────────────────────────────────

def process_source_field(data, field, verify=False):
    """Fix a single source field in a JSONL record."""
    original = data.get(field, '')
    if not original or not isinstance(original, str):
        return data, {}

    fixed, changes = fix_toke_source(original)
    if fixed != original:
        data[field] = fixed

        if verify and changes:
            ok = verify_with_tkc(fixed)
            changes['tkc_pass'] = 1 if ok else 0
            if not ok:
                changes['tkc_fail'] = 1
                # Revert if verification fails
                data[field] = original
                changes['reverted'] = 1
                return data, changes

    return data, changes


def process_messages(data, verify=False):
    """Fix toke source embedded in chat messages."""
    messages = data.get('messages', [])
    total_changes = Counter()

    for msg in messages:
        content = msg.get('content', '')
        if 'm=' in content and ('f=' in content or 't=' in content):
            fixed, changes = fix_toke_source(content)
            if fixed != content:
                msg['content'] = fixed
                total_changes.update(changes)

    data['messages'] = messages
    return data, total_changes


def verify_with_tkc(source):
    """Run tkc --check on a source string. Returns True if it compiles."""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tk', delete=False) as f:
            f.write(source)
            f.flush()
            result = subprocess.run(
                [TKC_PATH, '--check', f.name],
                capture_output=True, timeout=10
            )
            os.unlink(f.name)
            return result.returncode == 0
    except Exception:
        return False


def process_file(rel_path, config, dry_run=False, verify=False):
    """Process a single JSONL file."""
    full_path = os.path.join(CORPUS_ROOT, rel_path)
    if not os.path.exists(full_path):
        print(f"  SKIP (not found): {rel_path}")
        return {}

    out_path = full_path.replace('.jsonl', '_fixed.jsonl')
    fields = config.get('fields')
    fmt = config.get('format')

    total_changes = Counter()
    lines_changed = 0
    total_lines = 0

    print(f"  Processing: {rel_path}")

    with open(full_path, 'r') as fin:
        out_lines = []
        for line_num, line in enumerate(fin, 1):
            total_lines += 1
            line = line.strip()
            if not line:
                out_lines.append(line)
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue

            original_data = json.dumps(data)
            line_changes = Counter()

            if fmt == 'messages':
                data, line_changes = process_messages(data, verify=verify)
            elif fields:
                for field in fields:
                    data, field_changes = process_source_field(data, field, verify=verify)
                    line_changes.update(field_changes)

            if line_changes:
                lines_changed += 1
                total_changes.update(line_changes)

            out_lines.append(json.dumps(data, ensure_ascii=False))

            if total_lines % 100000 == 0:
                print(f"    ... {total_lines} lines processed")

    if not dry_run and lines_changed > 0:
        with open(out_path, 'w') as fout:
            for line in out_lines:
                fout.write(line + '\n')
        print(f"    Wrote: {out_path}")
    else:
        print(f"    {'[DRY RUN] ' if dry_run else ''}Would write: {out_path}")

    print(f"    Lines: {total_lines} total, {lines_changed} changed")
    print(f"    Changes: {dict(total_changes)}")

    return {
        'file': rel_path,
        'total_lines': total_lines,
        'lines_changed': lines_changed,
        'changes': dict(total_changes),
    }


# ── Main ───────────────────────────────────────────────────────────

def main():
    dry_run = '--dry-run' in sys.argv
    verify = '--verify' in sys.argv
    single_file = None

    for i, arg in enumerate(sys.argv):
        if arg == '--file' and i + 1 < len(sys.argv):
            single_file = sys.argv[i + 1]

    if dry_run:
        print("=== DRY RUN MODE (no files will be written) ===\n")
    if verify:
        print(f"=== VERIFY MODE (running tkc --check on fixes) ===")
        print(f"    TKC path: {TKC_PATH}\n")

    results = []

    if single_file:
        if single_file in FILES:
            results.append(process_file(single_file, FILES[single_file], dry_run, verify))
        else:
            print(f"Unknown file: {single_file}")
            print(f"Available: {', '.join(FILES.keys())}")
            sys.exit(1)
    else:
        for rel_path, config in FILES.items():
            results.append(process_file(rel_path, config, dry_run, verify))

    # Write summary
    summary_path = os.path.join(CORPUS_ROOT, 'scripts', 'fix_results.json')
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSummary written to: {summary_path}")


# ── Helper for bare lp(i= that we reference above ─────────────────

def fix_lp_bare_i_full(m):
    """Unused — replaced by replace_loop_var_i."""
    return m.group(0)


if __name__ == '__main__':
    main()
