#!/usr/bin/env python3
"""Local OSS-Instruct generation using 150 diverse seed programs.

Loads seeds from the deduplicated corpus, generates 3 prompt variants
per seed (450 total), validates with tkc --check, writes passing
programs to corpus/phase2_deduplicated/OSS-INST/.

Uses Claude Sonnet for quality. max_retries=1 to avoid wasting credits.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TKC = os.environ.get("TKC", "/Users/matthew.watt/tk/toke/tkc")
SONNET = "claude-sonnet-4-6"

# Import autofixer
try:
    from validate.autofixer import AutoFixer
    FIXER = AutoFixer()
except ImportError:
    FIXER = None
    print("WARNING: AutoFixer not available, skipping autofixer step")

import anthropic
client = anthropic.Anthropic()

# ---- Syntax reference (same as ec2 script) ----
TOKE_SYNTAX = """## toke Phase 2 syntax (MUST follow exactly)
- Module: m=name;
- Function: f=name(param1:type1;param2:type2):rettype{body};
- Import: i=alias:std.module;
- Custom type: t=$name{field1:type1;field2:type2};
- Primitive types: i64, u64, f64, $str, bool, $void
- Array type: @i64, @$str (NOT [i64] or @(i64))
- Array access: arr.get(i) (NOT arr[i]), arr.len (NOT arr.len())
- Map type: @($str:i64)
- Let: let x=val; (immutable), let x=mut.val; (mutable)
- Return: <expr (the < character IS the return keyword)
- Loop: lp(let i=0;i<n;i=i+1){body}
- If/else: if(cond){...}el{...}
- Break: br
- Equality: = (NOT ==)
- Not equal: !(a=b) (NOT a!=b, NOT a<>b)
- Error result type: rettype!$errtype
- Error propagation: expr!$errtype
- Cast: expr as type

## CRITICAL RULES (violations = compile failure)
- Use SEMICOLONS ; as separators everywhere (NOT commas)
- Function params separated by ; (NOT ,)
- NO uppercase letters anywhere (no A-Z at all)
- NO underscores (use camelCase: myVar not my_var)
- NO square brackets [ ] (use @type for arrays, .get(i) for access)
- .len is a PROPERTY not a method (arr.len NOT arr.len())
- Strings use double quotes only
- Every function body must end with };
- Output ONLY a single fenced ```toke code block, nothing else."""


def compile_check(source: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    try:
        r = subprocess.run([TKC, "--check", fname], capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr
    except Exception as e:
        return False, str(e)
    finally:
        os.unlink(fname)


def clean_llm_output(code: str) -> str:
    """Fix common LLM mistakes before compile check."""
    code = code.replace("!=", "!(x=y)")
    code = code.replace("&&", " & ")
    code = code.replace("||", " | ")
    code = re.sub(r'(\w+)\s*!=\s*(\w+)', r'!(\1=\2)', code)
    # Comma -> semicolon outside strings
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace(",", ";")
            part = part.replace(" & ", " and ")
            part = part.replace("&", " and ")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
    # .len() -> .len
    code = re.sub(r'\.len\(\)', '.len', code)
    # Fix uppercase type names
    for old, new in [("Str", "$str"), ("Int", "i64"), ("Float", "f64"),
                     ("Bool", "bool"), ("Void", "$void"), ("String", "$str")]:
        code = re.sub(rf'\b{old}\b', new, code)
    # == -> = outside strings
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace("==", "=")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
    # Apply autofixer
    if FIXER:
        code, _fixes = FIXER.fix(code)
    return code


def extract_toke(response: str) -> str:
    m = re.search(r"```(?:toke|tk)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    if response.strip().startswith("m="):
        return response.strip()
    return response.strip()


def generate_one(prompt: str, model: str = SONNET, max_retries: int = 1) -> str | None:
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_code = extract_toke(resp.content[0].text)
            code = clean_llm_output(raw_code)
            ok, err = compile_check(code)
            if ok:
                return code
            if attempt < max_retries:
                prompt = f"{prompt}\n\nYour previous attempt had this compiler error:\n{err}\n\nCommon mistakes: use ; not , as separator, use .len not .len(), use = not == for equality, use !(a=b) not a!=b, no uppercase letters, no & or | characters. Please fix and try again."
        except anthropic.RateLimitError:
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f"  API error: {e}")
            time.sleep(2)
    return None


def write_entry(output_dir: Path, entry_id: str, source: str, task_id: str,
                model_name: str, score: float, references: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": entry_id,
        "version": 1,
        "phase": "B",
        "task_id": task_id,
        "tk_source": source,
        "tk_tokens": len(source.split()),
        "attempts": 1,
        "model": model_name,
        "validation": {"compiler_exit_code": 0, "error_codes": []},
        "differential": {"languages_agreed": [], "majority_output": ""},
        "judge": {"accepted": True, "score": score},
        "references": references,
    }
    h = hashlib.sha1(source.encode()).hexdigest()[:8]
    out_file = output_dir / f"{entry_id}-{h}.json"
    with open(out_file, "w") as fh:
        json.dump(entry, fh, ensure_ascii=False)
        fh.write("\n")
    return out_file


def load_seed_programs(n: int = 150) -> list[str]:
    seeds = []
    seed_dirs = ["A-ARR", "A-CND", "A-MTH", "A-SRT", "A-STR", "B-CMP",
                 "COMPOSE-C", "COMPOSE-D", "DOC-EXP",
                 "EC2-D-CFG", "EC2-D-CLI", "EC2-D-CRY", "EC2-D-DAT",
                 "EC2-D-FIO", "EC2-D-NET", "EC2-D-TST", "EC2-D-WEB",
                 "ERR-GOLD", "COMPOSE-NEW"]
    dedup_dir = ROOT / "corpus" / "phase2_deduplicated"
    import random
    per_dir = max(10, n // len(seed_dirs))

    for d in seed_dirs:
        cat_dir = dedup_dir / d
        if not cat_dir.exists():
            continue
        all_files = sorted(cat_dir.glob("*.json"))
        # Random sample for diversity
        files = random.sample(all_files, min(per_dir, len(all_files)))
        for f in files:
            try:
                data = json.loads(f.read_text())
                src = data.get("tk_source", "")
                if src.strip() and len(src) > 50:
                    seeds.append(src)
            except Exception:
                pass
    return seeds[:n]


def load_existing_hashes(output_dir: Path) -> set[str]:
    """Load tk_source hashes from existing OSS-INST files to skip duplicates."""
    hashes = set()
    if not output_dir.exists():
        return hashes
    for f in output_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            src = data.get("tk_source", "")
            if src:
                hashes.add(hashlib.sha1(src.encode()).hexdigest())
        except Exception:
            pass
    print(f"  Loaded {len(hashes)} existing program hashes for dedup")
    return hashes


def main():
    print("=== OSS-Instruct Local Generation ===")
    print(f"  TKC: {TKC}")
    print(f"  Model: {SONNET}")
    print(f"  Max retries: 1")
    print()

    # Verify tkc works
    try:
        r = subprocess.run([TKC, "--version"], capture_output=True, text=True, timeout=5)
        print(f"  tkc version: {r.stdout.strip()}")
    except Exception as e:
        print(f"  ERROR: tkc not available: {e}")
        sys.exit(1)

    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "OSS-INST"

    # Load existing to avoid duplicates
    existing_hashes = load_existing_hashes(output_dir)

    # Count existing files to set starting ID
    existing_count = len(list(output_dir.glob("*.json"))) if output_dir.exists() else 0
    print(f"  Existing OSS-INST files: {existing_count}")

    # Load seeds
    seeds = load_seed_programs(150)
    print(f"  Loaded {len(seeds)} seed programs")

    if len(seeds) == 0:
        print("  ERROR: No seed programs found!")
        sys.exit(1)

    # Build prompts: 3 variants per seed
    preamble = f"""You are a toke language expert. Given a toke code snippet as inspiration, create a COMPLETELY DIFFERENT program that solves a different problem but demonstrates similar or more advanced toke concepts.

{TOKE_SYNTAX}

Requirements:
- The new program must solve a DIFFERENT problem than the seed
- It should be of equal or greater complexity
- Include meaningful function and variable names"""

    prompts = []
    for seed_idx, seed in enumerate(seeds):
        prompts.append((seed_idx, f"Inspired by this toke program, write a different program of similar complexity:\n\n```toke\n{seed}\n```"))
        prompts.append((seed_idx, f"This toke program does one thing. Write a more complex program that solves a harder problem using similar patterns:\n\n```toke\n{seed}\n```"))
        prompts.append((seed_idx, f"Study this toke code and write a new program that uses the same language features but for a completely unrelated task:\n\n```toke\n{seed}\n```"))

    total_prompts = len(prompts)
    print(f"  Total prompts: {total_prompts}")
    print(f"  Starting generation...\n")

    passed = 0
    failed = 0
    seen = set(existing_hashes)  # Include existing hashes in dedup set
    next_id = existing_count
    start_time = time.time()

    for i, (seed_idx, prompt_text) in enumerate(prompts):
        full_prompt = f"{preamble}\n\n{prompt_text}"
        code = generate_one(full_prompt, model=SONNET, max_retries=1)

        if code:
            code_hash = hashlib.sha1(code.encode()).hexdigest()
            if code_hash not in seen:
                seen.add(code_hash)
                entry_id = f"OSS-{next_id:05d}"
                write_entry(
                    output_dir, entry_id, code,
                    f"OSS-INST-{i}", "claude-sonnet-oss-instruct", 0.90,
                    {"story": "10.4.4", "prompt_index": i, "seed_index": seed_idx}
                )
                passed += 1
                next_id += 1
            else:
                failed += 1  # duplicate
        else:
            failed += 1

        processed = passed + failed
        if processed % 50 == 0 and processed > 0:
            elapsed = time.time() - start_time
            rate = processed / elapsed * 60 if elapsed > 0 else 0
            print(f"  [{processed}/{total_prompts}] pass={passed} fail={failed} "
                  f"rate={rate:.0f}/min elapsed={elapsed:.0f}s")

    elapsed = time.time() - start_time
    print(f"\n=== COMPLETE ===")
    print(f"  New programs: {passed}")
    print(f"  Failed/dupes: {failed}")
    print(f"  Total in OSS-INST: {next_id}")
    print(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  Pass rate: {passed/(passed+failed)*100:.1f}%" if (passed+failed) > 0 else "")


if __name__ == "__main__":
    main()
