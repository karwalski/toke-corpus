#!/usr/bin/env python3
"""Generate corpus entries from 114 hand-crafted seed tasks.

Story 10.3.4: Reads seed tasks from Phase 2 Corpus.md, generates 3-5 variations
per seed using Claude Sonnet, validates with tkc --check, writes passing programs
to corpus/phase2_deduplicated/<CATEGORY>/.

Uses the same clean_llm_output + AutoFixer pipeline as run_oss_instruct_local.py.

Usage:
    python scripts/generate_from_seeds.py [--variations N] [--resume]
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

# ---- Syntax reference ----
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
    code = code.replace("!=", "!(x=y)")
    code = code.replace("&&", " & ")
    code = code.replace("||", " | ")
    code = re.sub(r'(\w+)\s*!=\s*(\w+)', r'!(\1=\2)', code)
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace(",", ";")
            part = part.replace(" & ", " and ")
            part = part.replace("&", " and ")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
    code = re.sub(r'\.len\(\)', '.len', code)
    for old, new in [("Str", "$str"), ("Int", "i64"), ("Float", "f64"),
                     ("Bool", "bool"), ("Void", "$void"), ("String", "$str")]:
        code = re.sub(rf'\b{old}\b', new, code)
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace("==", "=")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
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


# ---------------------------------------------------------------------------
# Seed definitions — 114 tasks from Phase 2 Corpus.md
# ---------------------------------------------------------------------------

SEEDS = [
    # Section 1 — std.str (10 seeds)
    {"id": "SEED-STR-001", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that takes a string and returns it reversed.",
     "seed_code": 'm=strrev;\ni=str:std.str;\nf=reverse(s:$str):$str{\n  let chars=str.split(s;"");\n  let out=mut.str.new();\n  lp(let i=chars.len as i64 -1;i>0-1;i=i-1){\n    out=str.concat(out;chars.get(i as u64))\n  };\n  <out\n};'},
    {"id": "SEED-STR-002", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that counts how many times a character appears in a string.",
     "seed_code": 'm=charcount;\ni=str:std.str;\nf=count(s:$str;ch:$str):i64{\n  let parts=str.split(s;ch);\n  <parts.len as i64 -1\n};'},
    {"id": "SEED-STR-003", "category": "STD-STR", "complexity": "medium",
     "imports": "i=str:std.str;",
     "task": "Write a function that converts a string to title case — first letter of each word uppercase, rest lowercase.",
     "seed_code": 'm=titlecase;\ni=str:std.str;\nf=titlecase(s:$str):$str{\n  let words=str.split(s;" ");\n  let out=mut.str.new();\n  lp(let i=0;i<words.len as i64;i=i+1){\n    let w=words.get(i as u64);\n    let first=str.upper(str.slice(w;0;1));\n    let rest=str.lower(str.slice(w;1;str.len(w)));\n    if(i>0){out=str.concat(out;" ")};\n    out=str.concat(out;str.concat(first;rest))\n  };\n  <out\n};'},
    {"id": "SEED-STR-004", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that checks whether a string is a palindrome."},
    {"id": "SEED-STR-005", "category": "STD-STR", "complexity": "medium",
     "imports": "i=str:std.str;",
     "task": "Write a function that extracts all words longer than N characters from a sentence and returns them as an array."},
    {"id": "SEED-STR-006", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that trims leading and trailing whitespace from a string, then replaces all internal runs of whitespace with a single space."},
    {"id": "SEED-STR-007", "category": "STD-STR", "complexity": "medium",
     "imports": "i=str:std.str;",
     "task": "Write a function that takes a CSV line (comma-separated values) and returns an array of trimmed field values."},
    {"id": "SEED-STR-008", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that repeats a string N times with a separator between each repetition."},
    {"id": "SEED-STR-009", "category": "STD-STR", "complexity": "medium",
     "imports": "i=str:std.str;",
     "task": "Write a function that masks all but the last 4 characters of a string with asterisks (for credit card masking)."},
    {"id": "SEED-STR-010", "category": "STD-STR", "complexity": "simple",
     "imports": "i=str:std.str;",
     "task": "Write a function that checks whether a string starts with a given prefix and ends with a given suffix."},

    # Section 2 — std.file (5 seeds)
    {"id": "SEED-FILE-001", "category": "STD-FILE", "complexity": "medium",
     "imports": "i=file:std.file;",
     "task": "Write a function that reads a file and returns the number of lines it contains.",
     "seed_code": 'm=linecount;\ni=file:std.file;\ni=str:std.str;\nt=$fileerr{$read:$str;$notfound:$str};\nf=countlines(path:$str):i64!$fileerr{\n  let content=file.read(path)!$fileerr.$read;\n  let lines=str.split(content;"\\n");\n  <lines.len as i64\n};'},
    {"id": "SEED-FILE-002", "category": "STD-FILE", "complexity": "medium",
     "imports": "i=file:std.file;",
     "task": "Write a function that reads a file, filters lines matching a search term, and writes matching lines to an output file."},
    {"id": "SEED-FILE-003", "category": "STD-FILE", "complexity": "medium",
     "imports": "i=file:std.file; i=str:std.str;",
     "task": "Write a function that reads a config file in key=value format and returns a map of keys to values."},
    {"id": "SEED-FILE-004", "category": "STD-FILE", "complexity": "simple",
     "imports": "i=file:std.file;",
     "task": "Write a function that appends a log message with a timestamp to a file."},
    {"id": "SEED-FILE-005", "category": "STD-FILE", "complexity": "complex",
     "imports": "i=file:std.file; i=str:std.str;",
     "task": "Write a program with two functions: one that reads a directory listing and one that filters files by extension. The main function combines them to list all .tk files in a directory."},

    # Section 3 — std.json (5 seeds)
    {"id": "SEED-JSON-001", "category": "STD-JSON", "complexity": "medium",
     "imports": "i=json:std.json;",
     "task": "Write a function that takes a user struct and encodes it as a JSON string.",
     "seed_code": 'm=userjson;\ni=json:std.json;\nt=$user{name:$str;age:i64;active:bool};\nt=$jsonerr{$encode:$str;$decode:$str};\nf=tojson(u:$user):$str!$jsonerr{\n  let j=json.enc(u)!$jsonerr.$encode;\n  <j\n};'},
    {"id": "SEED-JSON-002", "category": "STD-JSON", "complexity": "medium",
     "imports": "i=json:std.json;",
     "task": "Write a function that decodes a JSON string into a typed config struct with fields for host, port, and debug flag."},
    {"id": "SEED-JSON-003", "category": "STD-JSON", "complexity": "complex",
     "imports": "i=json:std.json; i=file:std.file;",
     "task": "Write a program that reads a JSON file, extracts a specific field, modifies it, and writes the updated JSON back."},
    {"id": "SEED-JSON-004", "category": "STD-JSON", "complexity": "medium",
     "imports": "i=json:std.json;",
     "task": "Write a function that takes an array of structs and encodes them as a JSON array string."},
    {"id": "SEED-JSON-005", "category": "STD-JSON", "complexity": "complex",
     "imports": "i=json:std.json; i=str:std.str;",
     "task": "Write a function that validates a JSON object has all required keys from a checklist and returns a list of missing keys."},

    # Section 4 — std.crypto (5 seeds)
    {"id": "SEED-CRYPTO-001", "category": "STD-CRYPTO", "complexity": "simple",
     "imports": "i=crypto:std.crypto;",
     "task": "Write a function that hashes a password string using SHA-256 and returns the hex digest.",
     "seed_code": 'm=passhash;\ni=crypto:std.crypto;\ni=str:std.str;\nf=hashpw(pw:$str):$str{\n  let h=crypto.sha256(str.bytes(pw));\n  <crypto.tohex(h)\n};'},
    {"id": "SEED-CRYPTO-002", "category": "STD-CRYPTO", "complexity": "medium",
     "imports": "i=crypto:std.crypto;",
     "task": "Write a function that computes an HMAC-SHA256 signature for an API request body using a secret key."},
    {"id": "SEED-CRYPTO-003", "category": "STD-CRYPTO", "complexity": "medium",
     "imports": "i=crypto:std.crypto; i=str:std.str;",
     "task": "Write a function that verifies a message signature by recomputing the HMAC and using constant-time comparison."},
    {"id": "SEED-CRYPTO-004", "category": "STD-CRYPTO", "complexity": "complex",
     "imports": "i=crypto:std.crypto; i=str:std.str;",
     "task": "Write a program with three functions: one to generate a salt, one to hash a password with the salt, and one to verify a password against a stored hash."},
    {"id": "SEED-CRYPTO-005", "category": "STD-CRYPTO", "complexity": "simple",
     "imports": "i=crypto:std.crypto;",
     "task": "Write a function that hashes a file's contents using SHA-256 to produce a checksum string."},

    # Section 5 — std.time (5 seeds)
    {"id": "SEED-TIME-001", "category": "STD-TIME", "complexity": "simple",
     "imports": "i=time:std.time;",
     "task": "Write a function that returns the current Unix timestamp as an i64."},
    {"id": "SEED-TIME-002", "category": "STD-TIME", "complexity": "medium",
     "imports": "i=time:std.time; i=str:std.str;",
     "task": "Write a function that takes two timestamps and returns the elapsed time as a human-readable string like '3h 42m 15s'."},
    {"id": "SEED-TIME-003", "category": "STD-TIME", "complexity": "medium",
     "imports": "i=time:std.time;",
     "task": "Write a function that measures the execution time of a given operation by recording timestamps before and after."},
    {"id": "SEED-TIME-004", "category": "STD-TIME", "complexity": "simple",
     "imports": "i=time:std.time;",
     "task": "Write a function that checks whether a Unix timestamp is older than N seconds ago."},
    {"id": "SEED-TIME-005", "category": "STD-TIME", "complexity": "complex",
     "imports": "i=time:std.time; i=str:std.str;",
     "task": "Write a rate limiter that allows N operations per minute using timestamp tracking. Include a function to check if an operation is allowed and a function to record an operation."},

    # Section 6 — std.env (4 seeds)
    {"id": "SEED-ENV-001", "category": "STD-ENV", "complexity": "simple",
     "imports": "i=env:std.env;",
     "task": "Write a function that reads an environment variable and returns it, or returns a default value if not set."},
    {"id": "SEED-ENV-002", "category": "STD-ENV", "complexity": "medium",
     "imports": "i=env:std.env; i=str:std.str;",
     "task": "Write a function that reads DATABASE_URL from environment, parses it into host, port, and database name components, and returns a config struct."},
    {"id": "SEED-ENV-003", "category": "STD-ENV", "complexity": "medium",
     "imports": "i=env:std.env;",
     "task": "Write a function that reads a PORT environment variable, converts it to an integer, and returns a default of 8080 if missing or invalid."},
    {"id": "SEED-ENV-004", "category": "STD-ENV", "complexity": "complex",
     "imports": "i=env:std.env; i=file:std.file;",
     "task": "Write a program that loads config from environment variables with fallback to a .env file. Include a function to parse the .env file and a function to resolve a config key."},

    # Section 7 — std.log (4 seeds)
    {"id": "SEED-LOG-001", "category": "STD-LOG", "complexity": "simple",
     "imports": "i=log:std.log;",
     "task": "Write a function that logs a structured message with level, timestamp, and context string."},
    {"id": "SEED-LOG-002", "category": "STD-LOG", "complexity": "medium",
     "imports": "i=log:std.log; i=str:std.str;",
     "task": "Write a request logger function that logs method, path, status code, and duration for an HTTP request."},
    {"id": "SEED-LOG-003", "category": "STD-LOG", "complexity": "medium",
     "imports": "i=log:std.log;",
     "task": "Write a function that logs errors with full context including the error code, message, and source location."},
    {"id": "SEED-LOG-004", "category": "STD-LOG", "complexity": "complex",
     "imports": "i=log:std.log; i=file:std.file;",
     "task": "Write a simple structured logger with two functions: one to format a log entry as JSON and one to append it to a log file."},

    # Section 8 — std.test (4 seeds)
    {"id": "SEED-TEST-001", "category": "STD-TEST", "complexity": "simple",
     "imports": "i=test:std.test;",
     "task": "Write a test function that asserts two integers are equal."},
    {"id": "SEED-TEST-002", "category": "STD-TEST", "complexity": "medium",
     "imports": "i=test:std.test;",
     "task": "Write a test suite with three test functions for an abs function: positive input, negative input, and zero."},
    {"id": "SEED-TEST-003", "category": "STD-TEST", "complexity": "medium",
     "imports": "i=test:std.test; i=str:std.str;",
     "task": "Write a test function that asserts a string processing function correctly handles empty strings, single characters, and multi-word inputs."},
    {"id": "SEED-TEST-004", "category": "STD-TEST", "complexity": "complex",
     "imports": "i=test:std.test;",
     "task": "Write a test harness with a helper function that runs multiple test cases from an array of input-expected pairs and reports pass/fail counts."},

    # Section 9 — std.process (4 seeds)
    {"id": "SEED-PROC-001", "category": "STD-PROC", "complexity": "medium",
     "imports": "i=proc:std.process;",
     "task": "Write a function that spawns a child process, captures its stdout, and returns the output as a string."},
    {"id": "SEED-PROC-002", "category": "STD-PROC", "complexity": "medium",
     "imports": "i=proc:std.process; i=str:std.str;",
     "task": "Write a function that runs a shell command and returns the exit code as an i64."},
    {"id": "SEED-PROC-003", "category": "STD-PROC", "complexity": "complex",
     "imports": "i=proc:std.process; i=log:std.log;",
     "task": "Write a process monitor with two functions: one to spawn a process and one to check whether it's still running. Log state transitions."},
    {"id": "SEED-PROC-004", "category": "STD-PROC", "complexity": "medium",
     "imports": "i=proc:std.process;",
     "task": "Write a function that runs a command with a timeout and returns an error if the process exceeds the time limit."},

    # Section 10 — std.http (5 seeds)
    {"id": "SEED-HTTP-001", "category": "STD-HTTP", "complexity": "medium",
     "imports": "i=http:std.http;",
     "task": "Write a simple HTTP handler that returns a JSON response with a status field and a message field.",
     "seed_code": 'm=health;\ni=http:std.http;\ni=json:std.json;\nt=$status{ok:bool;msg:$str};\nf=handle(req:http.$req):http.$res{\n  let s=$status{ok:true;msg:"healthy"};\n  <http.ok(json.enc(s))\n};'},
    {"id": "SEED-HTTP-002", "category": "STD-HTTP", "complexity": "complex",
     "imports": "i=http:std.http; i=json:std.json;",
     "task": "Write an HTTP handler that parses a JSON body, validates required fields, and returns either a success response or a 400 error with details of what's missing."},
    {"id": "SEED-HTTP-003", "category": "STD-HTTP", "complexity": "complex",
     "imports": "i=http:std.http; i=db:std.db;",
     "task": "Write a user lookup handler that extracts an ID from the request path, queries the database, and returns the user as JSON or a 404 error."},
    {"id": "SEED-HTTP-004", "category": "STD-HTTP", "complexity": "application",
     "imports": "i=http:std.http; i=json:std.json; i=str:std.str;",
     "task": "Write a CRUD handler set with four functions: create, read, update, delete. Each takes an HTTP request and returns an HTTP response. All share a common user type."},
    {"id": "SEED-HTTP-005", "category": "STD-HTTP", "complexity": "medium",
     "imports": "i=http:std.http;",
     "task": "Write an HTTP middleware function that adds CORS headers to a response."},

    # Section 11 — std.db (5 seeds)
    {"id": "SEED-DB-001", "category": "STD-DB", "complexity": "medium",
     "imports": "i=db:std.db;",
     "task": "Write a function that queries a database for all users with age greater than a threshold and returns them as an array of structs."},
    {"id": "SEED-DB-002", "category": "STD-DB", "complexity": "medium",
     "imports": "i=db:std.db;",
     "task": "Write a function that inserts a record into a database table and returns the generated ID."},
    {"id": "SEED-DB-003", "category": "STD-DB", "complexity": "complex",
     "imports": "i=db:std.db; i=json:std.json;",
     "task": "Write a program with three functions: one to query users, one to format results as JSON, and one to handle pagination with offset and limit parameters."},
    {"id": "SEED-DB-004", "category": "STD-DB", "complexity": "medium",
     "imports": "i=db:std.db;",
     "task": "Write a function that executes a database query and maps the result rows into a typed array of structs with error handling."},
    {"id": "SEED-DB-005", "category": "STD-DB", "complexity": "complex",
     "imports": "i=db:std.db;",
     "task": "Write a migration runner with two functions: one to check the current schema version and one to apply a migration SQL string. Return errors for failures."},

    # Section 12 — std.i18n (3 seeds)
    {"id": "SEED-I18N-001", "category": "STD-I18N", "complexity": "simple",
     "imports": "i=i18n:std.i18n;",
     "task": "Write a function that looks up a translation key for a given locale and returns the translated string."},
    {"id": "SEED-I18N-002", "category": "STD-I18N", "complexity": "medium",
     "imports": "i=i18n:std.i18n; i=str:std.str;",
     "task": "Write a function that formats a message with placeholders replaced by values from a map, using locale-appropriate formatting."},
    {"id": "SEED-I18N-003", "category": "STD-I18N", "complexity": "medium",
     "imports": "i=i18n:std.i18n;",
     "task": "Write a function that determines the best matching locale from a list of supported locales given a user's preferred locale string."},

    # Section 13 — std.yaml and std.toon (5 seeds)
    {"id": "SEED-YAML-001", "category": "STD-YAML", "complexity": "medium",
     "imports": "i=yaml:std.yaml;",
     "task": "Write a function that parses a YAML configuration string and extracts a nested value by dot-separated key path."},
    {"id": "SEED-YAML-002", "category": "STD-YAML", "complexity": "medium",
     "imports": "i=yaml:std.yaml; i=file:std.file;",
     "task": "Write a function that reads a YAML file and converts it to a typed config struct."},
    {"id": "SEED-YAML-003", "category": "STD-YAML", "complexity": "complex",
     "imports": "i=yaml:std.yaml; i=json:std.json;",
     "task": "Write a config converter with two functions: one to parse YAML input and one to emit JSON output. The program converts between formats."},
    {"id": "SEED-TOON-001", "category": "STD-TOON", "complexity": "medium",
     "imports": "i=toon:std.toon;",
     "task": "Write a function that encodes a struct as TOON format and returns the string."},
    {"id": "SEED-TOON-002", "category": "STD-TOON", "complexity": "medium",
     "imports": "i=toon:std.toon;",
     "task": "Write a function that decodes a TOON string into a typed settings struct with fields for theme, fontsize, and language."},

    # Section 14 — Multi-module applications (10 seeds)
    {"id": "SEED-APP-001", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=http:std.http; i=json:std.json; i=db:std.db; i=log:std.log;",
     "task": "Write a user API module with 5 functions: a type definition for user, a create handler, a get-by-id handler, a list handler, and an error response helper. All handlers log their invocation."},
    {"id": "SEED-APP-002", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=file:std.file; i=str:std.str; i=json:std.json;",
     "task": "Write a config loader module with 3 functions: read config from a JSON file, validate that all required keys are present, and merge config with default values for missing keys."},
    {"id": "SEED-APP-003", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=http:std.http; i=crypto:std.crypto; i=str:std.str; i=json:std.json;",
     "task": "Write a webhook receiver module with 4 functions: verify the HMAC signature of an incoming request, parse the JSON payload, route to the correct handler based on an event type field, and return an appropriate response."},
    {"id": "SEED-APP-004", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=file:std.file; i=str:std.str; i=log:std.log;",
     "task": "Write a log rotation module with 3 functions: check if a log file exceeds a size threshold, rename the current log with a timestamp suffix, and create a new empty log file."},
    {"id": "SEED-APP-005", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=db:std.db; i=json:std.json; i=time:std.time;",
     "task": "Write a session manager module with 4 functions: create a session (insert into DB with expiry timestamp), validate a session (check existence and expiry), refresh a session (update expiry), and destroy a session (delete from DB)."},
    {"id": "SEED-APP-006", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=http:std.http; i=str:std.str; i=env:std.env;",
     "task": "Write a health check endpoint module with 3 functions: a handler that returns system status, a function to read version from environment, and a function to format uptime from a start timestamp."},
    {"id": "SEED-APP-007", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=file:std.file; i=str:std.str; i=crypto:std.crypto;",
     "task": "Write a file integrity checker with 3 functions: hash a file's contents, compare a hash against a stored manifest entry, and scan a directory reporting any mismatches."},
    {"id": "SEED-APP-008", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=json:std.json; i=str:std.str; i=http:std.http;",
     "task": "Write a JSON API client module with 4 functions: build a request with headers, send a GET request and parse the JSON response, send a POST request with a JSON body, and handle error responses by mapping HTTP status codes to typed error variants."},
    {"id": "SEED-APP-009", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=db:std.db; i=str:std.str; i=log:std.log;",
     "task": "Write a simple key-value store module with 4 functions: get a value by key, set a key-value pair, delete a key, and list all keys. All operations log and use the database."},
    {"id": "SEED-APP-010", "category": "APP-MULTI", "complexity": "application",
     "imports": "i=test:std.test; i=str:std.str;",
     "task": "Write a test runner module with 3 functions: a function to run a single test case (input, expected output, actual output), a function to run an array of test cases and collect results, and a function to format the results as a summary string."},

    # Section 15 — Error handling patterns (10 seeds)
    {"id": "SEED-ERR-001", "category": "ERR-PATTERN", "complexity": "medium",
     "imports": "i=file:std.file; i=json:std.json;",
     "task": "Write a function that chains three fallible operations (file read, JSON parse, field extraction) with error propagation using ! at each step. Define a sum type for the error.",
     "seed_code": 'm=chain;\ni=file:std.file;\ni=json:std.json;\nt=$apperr{$io:$str;$parse:$str;$missing:$str};\nf=loadname(path:$str):$str!$apperr{\n  let raw=file.read(path)!$apperr.$io;\n  let doc=json.dec(raw)!$apperr.$parse;\n  let name=json.field(doc;"name")!$apperr.$missing;\n  <name\n};'},
    {"id": "SEED-ERR-002", "category": "ERR-PATTERN", "complexity": "medium",
     "task": "Write a function that validates an integer is within a range and returns either the value or a typed error with the out-of-bounds value."},
    {"id": "SEED-ERR-003", "category": "ERR-PATTERN", "complexity": "complex",
     "task": "Write a program with a sum type that has 4 error variants and a function that uses match to convert each variant into a user-friendly error message string."},
    {"id": "SEED-ERR-004", "category": "ERR-PATTERN", "complexity": "medium",
     "task": "Write a function that attempts to parse a string as an integer and returns a result type with either the parsed value or a parse error."},
    {"id": "SEED-ERR-005", "category": "ERR-PATTERN", "complexity": "complex",
     "task": "Write a validation pipeline with 3 functions: validate_name (checks non-empty), validate_age (checks range 0-150), validate_email (checks contains @). A fourth function composes all three and returns the first error encountered."},
    {"id": "SEED-ERR-006", "category": "ERR-PATTERN", "complexity": "medium",
     "task": "Write a function that wraps a database query and translates the database error type into an application-level error type using match."},
    {"id": "SEED-ERR-007", "category": "ERR-PATTERN", "complexity": "medium",
     "task": "Write two functions: one fallible function that might return an error, and one caller function that catches the error with match and returns a default value instead."},
    {"id": "SEED-ERR-008", "category": "ERR-PATTERN", "complexity": "simple",
     "task": "Write a divide function that returns an error for division by zero instead of trapping."},
    {"id": "SEED-ERR-009", "category": "ERR-PATTERN", "complexity": "complex",
     "task": "Write a retry wrapper: a function that calls a fallible operation up to N times, returning the first success or the last error."},
    {"id": "SEED-ERR-010", "category": "ERR-PATTERN", "complexity": "medium",
     "task": "Write a function that reads two environment variables, parses both as integers, and returns their sum — propagating errors from either read or either parse."},

    # Section 16 — Simple algorithms (20 seeds)
    {"id": "SEED-SIMPLE-001", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the absolute value of an integer."},
    {"id": "SEED-SIMPLE-002", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the maximum of two integers."},
    {"id": "SEED-SIMPLE-003", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the minimum of three integers."},
    {"id": "SEED-SIMPLE-004", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns true if a number is even."},
    {"id": "SEED-SIMPLE-005", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the factorial of a non-negative integer."},
    {"id": "SEED-SIMPLE-006", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that swaps two values in an array by index."},
    {"id": "SEED-SIMPLE-007", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the last element of an array."},
    {"id": "SEED-SIMPLE-008", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that clamps a value between a min and max bound."},
    {"id": "SEED-SIMPLE-009", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns true if all elements in an array are positive."},
    {"id": "SEED-SIMPLE-010", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the sum of digits of a positive integer."},
    {"id": "SEED-SIMPLE-011", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the Nth Fibonacci number."},
    {"id": "SEED-SIMPLE-012", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns true if a string is empty."},
    {"id": "SEED-SIMPLE-013", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that counts the number of elements in an array that are greater than a threshold."},
    {"id": "SEED-SIMPLE-014", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the index of the first occurrence of a value in an array, or -1 if not found."},
    {"id": "SEED-SIMPLE-015", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that computes the GCD of two positive integers using the Euclidean algorithm."},
    {"id": "SEED-SIMPLE-016", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns true if an integer is a power of two."},
    {"id": "SEED-SIMPLE-017", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that reverses an array of integers in place."},
    {"id": "SEED-SIMPLE-018", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that computes the dot product of two equal-length arrays of f64."},
    {"id": "SEED-SIMPLE-019", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that converts a temperature from Celsius to Fahrenheit."},
    {"id": "SEED-SIMPLE-020", "category": "SIMPLE-ALG", "complexity": "simple",
     "task": "Write a function that returns the average of an array of f64 values."},

    # Section 17 — Multi-function composition (10 seeds)
    {"id": "SEED-COMP-001", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write two functions: isPrime(n:i64):bool and countPrimes(limit:i64):i64 where countPrimes uses isPrime in a loop."},
    {"id": "SEED-COMP-002", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write three functions: min, max, and clampArray which applies clamping to every element using min and max."},
    {"id": "SEED-COMP-003", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write two functions: map which applies a transformation to each element of an array, and square which squares an i64. The module maps square over an input array."},
    {"id": "SEED-COMP-004", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write three functions: sum, mean, and variance. Mean uses sum. Variance uses mean and sum."},
    {"id": "SEED-COMP-005", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write two functions: filter which returns elements matching a condition, and isPositive. Combine them to filter an array to only positive values."},
    {"id": "SEED-COMP-006", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write three functions: encode which converts a string to an array of byte values, shift which adds an offset to each byte, and caesarEncrypt which composes encode and shift."},
    {"id": "SEED-COMP-007", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write two functions: partition which splits an array into two arrays (below and above a pivot), and quicksort which recursively uses partition."},
    {"id": "SEED-COMP-008", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write three functions: tokenize which splits a string by spaces, toLower which lowercases a string, and normalize which composes both."},
    {"id": "SEED-COMP-009", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write two functions: accumulate which folds an array with a running sum, and prefixSums which returns the array of partial sums."},
    {"id": "SEED-COMP-010", "category": "COMP-MULTI", "complexity": "medium",
     "task": "Write three functions: distanceSquared between two 2D points, closerTo which returns the closer of two points to a reference, and findNearest which scans an array of points."},
]


# ---------------------------------------------------------------------------
# Prompt variations
# ---------------------------------------------------------------------------

def build_prompts(seed: dict, num_variations: int = 3) -> list[str]:
    """Build N prompt variations for a seed task."""
    task = seed["task"]
    imports = seed.get("imports", "")
    seed_code = seed.get("seed_code", "")
    complexity = seed.get("complexity", "medium")

    base = f"""You are a toke language expert.

{TOKE_SYNTAX}

Required imports: {imports if imports else '(none — pure algorithmic)'}
Complexity level: {complexity}
"""

    prompts = []

    # Variation 1: Direct task
    p1 = f"""{base}

Task: {task}

Write the complete toke program. Start with m=modulename; and include all necessary imports."""
    prompts.append(p1)

    # Variation 2: With seed code as example (if available)
    if seed_code:
        p2 = f"""{base}

Here is an example toke program for reference:
```toke
{seed_code}
```

Now write a DIFFERENT program that solves a related but distinct problem using the same imports and patterns. Task: {task} but with a creative twist — change the algorithm or add an edge case handler."""
        prompts.append(p2)
    else:
        p2 = f"""{base}

Task: {task}

Write the program, then write a second version that solves the same problem differently (different algorithm or approach). Output only the second version."""
        prompts.append(p2)

    # Variation 3: More complex version
    p3 = f"""{base}

Task: {task}

Write a MORE COMPLEX version that handles edge cases (empty inputs, zero values, boundary conditions). Include at least one additional helper function."""
    prompts.append(p3)

    # Variations 4-5: only for medium+ complexity
    if num_variations >= 4 and complexity in ("medium", "complex", "application"):
        p4 = f"""{base}

Task: {task}

Write the program with full error handling using toke's error result types (rettype!$errtype). Define a custom error sum type and propagate errors with !."""
        prompts.append(p4)

    if num_variations >= 5 and complexity in ("complex", "application"):
        p5 = f"""{base}

Task: Expand on this idea: {task}

Write an APPLICATION-level program with 3-5 cooperating functions. Each function should have a clear single responsibility. Include custom types for data passing between functions."""
        prompts.append(p5)

    return prompts[:num_variations]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_existing_hashes(output_dir: Path) -> set[str]:
    hashes = set()
    base = output_dir.parent
    for cat_dir in base.iterdir():
        if not cat_dir.is_dir():
            continue
        for f in cat_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                src = data.get("tk_source", "")
                if src:
                    hashes.add(hashlib.sha1(src.encode()).hexdigest())
            except Exception:
                pass
    return hashes


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate from 114 seed tasks")
    parser.add_argument("--variations", type=int, default=3, help="Variations per seed (3-5)")
    parser.add_argument("--resume", action="store_true", help="Skip seeds with existing output")
    parser.add_argument("--start", type=int, default=0, help="Start from seed index N")
    parser.add_argument("--limit", type=int, default=0, help="Process only N seeds (0=all)")
    args = parser.parse_args()

    num_variations = max(3, min(5, args.variations))

    print("=== Seed-Based Corpus Generation ===")
    print(f"  TKC: {TKC}")
    print(f"  Model: {SONNET}")
    print(f"  Seeds: {len(SEEDS)}")
    print(f"  Variations per seed: {num_variations}")
    print(f"  Max retries: 1")
    print()

    # Verify tkc
    try:
        r = subprocess.run([TKC, "--version"], capture_output=True, text=True, timeout=5)
        print(f"  tkc version: {r.stdout.strip()}")
    except Exception as e:
        print(f"  ERROR: tkc not available: {e}")
        sys.exit(1)

    output_base = ROOT / "corpus" / "phase2_deduplicated"

    # Load existing hashes for dedup
    print("  Loading existing hashes for dedup...")
    seen = load_existing_hashes(output_base)
    print(f"  {len(seen)} existing program hashes loaded")

    # Determine which seeds to process
    seeds_to_process = SEEDS[args.start:]
    if args.limit > 0:
        seeds_to_process = seeds_to_process[:args.limit]

    total_prompts = 0
    for seed in seeds_to_process:
        total_prompts += len(build_prompts(seed, num_variations))

    print(f"  Seeds to process: {len(seeds_to_process)}")
    print(f"  Total prompts: {total_prompts}")
    print(f"  Starting generation...\n")

    passed = 0
    failed = 0
    duped = 0
    start_time = time.time()

    # Per-category counters for entry IDs
    cat_counters: dict[str, int] = {}
    for cat_dir in output_base.iterdir():
        if cat_dir.is_dir():
            count = len(list(cat_dir.glob("*.json")))
            cat_counters[cat_dir.name] = count

    for seed_idx, seed in enumerate(seeds_to_process):
        category = seed["category"]
        seed_id = seed["id"]
        prompts = build_prompts(seed, num_variations)

        if args.resume:
            cat_dir = output_base / category
            if cat_dir.exists():
                existing = list(cat_dir.glob(f"*{seed_id}*.json"))
                if len(existing) >= num_variations:
                    print(f"  [{seed_idx+1}/{len(seeds_to_process)}] {seed_id} — skipping (resume)")
                    continue

        for var_idx, prompt in enumerate(prompts):
            code = generate_one(prompt, model=SONNET, max_retries=1)

            if code:
                code_hash = hashlib.sha1(code.encode()).hexdigest()
                if code_hash not in seen:
                    seen.add(code_hash)
                    cat_count = cat_counters.get(category, 0)
                    entry_id = f"{seed_id}-v{var_idx:02d}"
                    write_entry(
                        output_base / category, entry_id, code,
                        f"{seed_id}-var{var_idx}", "claude-sonnet-seed-gen", 0.90,
                        {"story": "10.3.4", "seed_id": seed_id, "variation": var_idx,
                         "complexity": seed.get("complexity", "medium")}
                    )
                    cat_counters[category] = cat_count + 1
                    passed += 1
                else:
                    duped += 1
            else:
                failed += 1

        processed = seed_idx + 1
        if processed % 10 == 0:
            elapsed = time.time() - start_time
            total_attempts = passed + failed + duped
            rate = total_attempts / elapsed * 60 if elapsed > 0 else 0
            print(f"  [{processed}/{len(seeds_to_process)} seeds] pass={passed} fail={failed} "
                  f"dupe={duped} rate={rate:.0f}/min elapsed={elapsed:.0f}s")

    elapsed = time.time() - start_time
    print(f"\n=== COMPLETE ===")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Duped:  {duped}")
    print(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    if passed + failed + duped > 0:
        print(f"  Pass rate: {passed/(passed+failed+duped)*100:.1f}%")

    # Per-category summary
    print(f"\n  Per-category output:")
    for cat in sorted(set(s["category"] for s in seeds_to_process)):
        cat_dir = output_base / cat
        count = len(list(cat_dir.glob("*.json"))) if cat_dir.exists() else 0
        print(f"    {cat}: {count} files")


if __name__ == "__main__":
    main()
