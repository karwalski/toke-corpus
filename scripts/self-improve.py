#!/usr/bin/env python3
"""Self-improvement loop: generate candidates via Gate 2 model, filter by compile + test.

Story 71.5.5 — Uses the live Gate 2 model (SageMaker endpoint or API Gateway) to
generate many candidate toke programs, then filters them by:
  1. Compilation  (tkc --check)
  2. Build        (tkc --emit-llvm + clang)
  3. Execution    (run binary, compare stdout to expected)

Saves verified programs to an output directory. Reports statistics.

Usage:
    # Dry run with sample tasks (3 tasks x 3 samples)
    python scripts/self-improve.py --tasks data/self_improve_tasks.jsonl --samples 3 --max-tasks 3

    # Full run (all tasks x 10 samples)
    python scripts/self-improve.py --tasks data/self_improve_tasks.jsonl --samples 10

    # Use API Gateway instead of SageMaker
    python scripts/self-improve.py --tasks data/self_improve_tasks.jsonl --backend api \\
        --api-url https://example.com/generate --api-key tk-xxx

    # Use SageMaker directly
    python scripts/self-improve.py --tasks data/self_improve_tasks.jsonl --backend sagemaker \\
        --endpoint toke-7b-gate2 --region us-east-1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
TKC = os.environ.get("TKC", "/Users/matthew.watt/tk/toke/tkc")

# Locate stdlib C sources — try src/stdlib/ first (has the .c glue files),
# then fall back to stdlib/ (has .tk/.tki interface files only).
def _find_stdlib_dir() -> str:
    if "TKC_STDLIB_DIR" in os.environ:
        return os.environ["TKC_STDLIB_DIR"]
    tkc_dir = Path(TKC).parent
    # Prefer src/stdlib which contains the C runtime sources
    candidate = tkc_dir / "src" / "stdlib"
    if candidate.is_dir() and any(candidate.glob("*.c")):
        return str(candidate)
    candidate2 = tkc_dir / "stdlib"
    if candidate2.is_dir():
        return str(candidate2)
    return str(tkc_dir / "stdlib")

TKC_STDLIB_DIR = _find_stdlib_dir()
CLANG = os.environ.get("CLANG", "clang")

# Timeouts (seconds)
API_TIMEOUT = 60
COMPILE_TIMEOUT = 30
BUILD_TIMEOUT = 30
EXEC_TIMEOUT = 10

logger = logging.getLogger("self-improve")

# Regex to extract toke source from model output (handles markdown fences)
_FENCE_RE = re.compile(r"```(?:toke|tk)?\s*\n(.*?)```", re.DOTALL)

# ---------------------------------------------------------------------------
# System prompt — matches the prompt used to train Gate 2
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a code generation assistant for the toke programming language (v0.3).

toke is a small, structural language with a 59-character alphabet (lowercase a-z, \
digits, and the symbols ( ) { } = : . ; + - * / < > ! | $ @). Every source file \
starts with a module declaration and uses ; as the separator everywhere.

Key syntax:
- Module: m=name;
- Function: f=name(p1:type1;p2:type2):rettype{body};
- Import: i=alias:std.module;
- Types: t=$name{field1:type1;field2:type2};
- Primitives: i64, u64, f64, $str, bool, $void
- Arrays: @i64, @$str — literal: @(1;2;3)
- Array access: arr.get(i) (NOT arr[i]), arr.len (property, NOT .len())
- Let: let x=val; (immutable), mut x=0; (mutable)
- Return: <expr (short), rt expr; (long)
- Loop: lp(let i=0;i<n;i=i+1){body}
- If/else: if(cond){...}el{...}
- Break: br
- Equality: single = (NOT ==)
- Not equal: !(a=b) (NOT !=)
- I/O: i=io:std.io; then io.readln() to read a line, io.println(s) to print
- String to int: i=conv:std.conv; then conv.atoi(s)
- Int to string: conv.itoa(n)
- Env args: i=env:std.env; then env.args() returns @$str

CRITICAL RULES:
- Use SEMICOLONS ; everywhere (NOT commas)
- NO uppercase letters except in match arm heads (Ok, Err, Some, None)
- NO underscores in identifiers
- NO square brackets [ ]
- Every top-level decl ends with ;
- Output ONLY the toke source, no prose, no fences.
- The source must start with m= and compile under tkc --check.\
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A benchmark task with description and I/O test cases."""
    task_id: str
    description: str
    test_cases: list[dict[str, str]]  # [{input: str, expected_output: str}]
    difficulty: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            task_id=d["task_id"],
            description=d["description"],
            test_cases=d["test_cases"],
            difficulty=d.get("difficulty", 1),
        )


@dataclass
class GenerationResult:
    """Result of one generation attempt."""
    task_id: str
    raw_output: str
    source: str  # extracted toke source
    compiles: bool = False
    builds: bool = False
    passes_tests: bool = False
    compile_error: str = ""
    build_error: str = ""
    test_results: list[dict] = field(default_factory=list)


@dataclass
class Stats:
    """Aggregate statistics for the run."""
    total_generated: int = 0
    compile_pass: int = 0
    build_pass: int = 0
    functional_pass: int = 0
    unique_programs: int = 0
    errors: int = 0
    api_errors: int = 0
    tasks_attempted: int = 0


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------

def extract_source(raw: str) -> str:
    """Extract toke source from model output, handling markdown fences."""
    # Try markdown fences first
    match = _FENCE_RE.search(raw)
    if match:
        return match.group(1).strip()

    # If no fences, look for line starting with m=
    lines = raw.strip().split("\n")
    start = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("m="):
            start = i
            break

    if start >= 0:
        # Take everything from the m= line to end, stopping at obvious prose
        source_lines = []
        for line in lines[start:]:
            stripped = line.strip()
            # Stop if we hit obvious non-code prose
            if stripped and not any(c in stripped for c in "=;{}()<>@$!|"):
                if len(stripped.split()) > 4:  # likely prose
                    break
            source_lines.append(line)
        return "\n".join(source_lines).strip()

    # Last resort: return the raw output trimmed
    return raw.strip()


def normalize_source(source: str) -> str:
    """Normalize source for deduplication: strip whitespace, sort lines deterministically."""
    # Remove all whitespace for comparison
    return re.sub(r"\s+", "", source)


def source_hash(source: str) -> str:
    """Hash normalized source for deduplication."""
    return hashlib.sha256(normalize_source(source).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Compilation and execution
# ---------------------------------------------------------------------------

def _tkc_env() -> dict[str, str]:
    """Return env dict with TKC_STDLIB_DIR set."""
    env = os.environ.copy()
    env["TKC_STDLIB_DIR"] = TKC_STDLIB_DIR
    return env


def compile_check(source: str) -> tuple[bool, str]:
    """Run tkc --check on the source. Returns (success, error_output)."""
    with tempfile.NamedTemporaryFile(
        suffix=".tk", mode="w", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(source)
        fname = f.name

    try:
        r = subprocess.run(
            [TKC, "--check", fname],
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            env=_tkc_env(),
        )
        return r.returncode == 0, r.stderr + r.stdout
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT: tkc --check exceeded time limit"
    except Exception as e:
        return False, f"ERROR: {e}"
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def build_binary(source: str) -> tuple[bool, str, str]:
    """Compile source to binary via tkc --emit-llvm + clang.

    Returns (success, binary_path, error_output).
    """
    tmpdir = tempfile.mkdtemp(prefix="toke-si-")
    tk_file = os.path.join(tmpdir, "prog.tk")
    ll_file = os.path.join(tmpdir, "prog.ll")
    bin_file = os.path.join(tmpdir, "prog")

    with open(tk_file, "w") as f:
        f.write(source)

    env = _tkc_env()

    # Step 1: emit LLVM IR (run with cwd=tmpdir so .ll lands alongside .tk)
    try:
        r = subprocess.run(
            [TKC, "--emit-llvm", tk_file],
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            cwd=tmpdir,
            env=env,
        )
        if r.returncode != 0:
            return False, "", f"tkc --emit-llvm failed: {r.stderr}"
    except subprocess.TimeoutExpired:
        return False, "", "TIMEOUT: tkc --emit-llvm"
    except Exception as e:
        return False, "", f"ERROR: {e}"

    # Check that .ll file was created
    if not os.path.exists(ll_file):
        # tkc might output to a different name
        ll_files = [f for f in os.listdir(tmpdir) if f.endswith(".ll")]
        if ll_files:
            ll_file = os.path.join(tmpdir, ll_files[0])
        else:
            return False, "", "No .ll file produced"

    # Step 2: Get stdlib C deps (runtime sources needed for linking)
    try:
        deps_r = subprocess.run(
            [TKC, "--emit-deps", tk_file],
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            env=env,
        )
        # --emit-deps returns one C source file per line
        extra_sources = []
        if deps_r.returncode == 0 and deps_r.stdout.strip():
            extra_sources = [
                s.strip() for s in deps_r.stdout.strip().split("\n")
                if s.strip() and os.path.exists(s.strip())
            ]
    except Exception:
        extra_sources = []

    # Step 3: clang to binary (link .ll + stdlib C sources)
    try:
        clang_cmd = [CLANG, ll_file] + extra_sources + ["-o", bin_file, "-lm"]
        r = subprocess.run(
            clang_cmd,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
        )
        if r.returncode != 0:
            return False, "", f"clang failed: {r.stderr}"
    except subprocess.TimeoutExpired:
        return False, "", "TIMEOUT: clang"
    except FileNotFoundError:
        return False, "", f"clang not found at '{CLANG}'"
    except Exception as e:
        return False, "", f"ERROR: {e}"

    if os.path.exists(bin_file):
        os.chmod(bin_file, 0o755)
        return True, bin_file, ""
    return False, "", "Binary not produced"


def run_test(bin_path: str, stdin_input: str, expected_output: str) -> tuple[bool, str]:
    """Run a binary with given stdin, compare stdout to expected output.

    Returns (passed, actual_output).
    """
    try:
        r = subprocess.run(
            [bin_path],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT,
        )
        actual = r.stdout.strip()
        expected = expected_output.strip()
        return actual == expected, actual
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, f"ERROR: {e}"


def cleanup_tmpdir(path: str) -> None:
    """Remove a temporary directory and all contents."""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Model backends
# ---------------------------------------------------------------------------

class ModelBackend:
    """Abstract base for model inference."""

    def generate(self, prompt: str, temperature: float = 0.7) -> str:
        raise NotImplementedError


class SageMakerBackend(ModelBackend):
    """Call the Gate 2 model via SageMaker endpoint."""

    def __init__(self, endpoint_name: str, region: str):
        import boto3
        self.client = boto3.client("sagemaker-runtime", region_name=region)
        self.endpoint_name = endpoint_name

    def generate(self, prompt: str, temperature: float = 0.7) -> str:
        # Build ChatML prompt format (Qwen expects this)
        chatml = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        payload = json.dumps({
            "inputs": chatml,
            "parameters": {
                "max_new_tokens": 1024,
                "temperature": temperature,
                "top_p": 0.95,
                "return_full_text": False,
                "do_sample": temperature > 0,
            },
        })
        try:
            resp = self.client.invoke_endpoint(
                EndpointName=self.endpoint_name,
                ContentType="application/json",
                Body=payload,
            )
            result = json.loads(resp["Body"].read().decode())
            if isinstance(result, list) and len(result) > 0:
                return result[0].get("generated_text", "")
            if isinstance(result, dict):
                return result.get("generated_text", "")
            return str(result)
        except Exception as e:
            logger.error("SageMaker invoke error: %s", e)
            raise


class SSHSageMakerBackend(ModelBackend):
    """Call SageMaker via SSH tunnel to a remote host that has AWS credentials.

    This is useful when the local machine has no AWS credentials but can SSH
    to a bastion/console host that does.
    """

    def __init__(
        self,
        endpoint_name: str,
        region: str,
        ssh_host: str,
        ssh_key: str,
        ssh_user: str = "admin",
    ):
        self.endpoint_name = endpoint_name
        self.region = region
        self.ssh_host = ssh_host
        self.ssh_key = ssh_key
        self.ssh_user = ssh_user

    def generate(self, prompt: str, temperature: float = 0.7) -> str:
        # Build ChatML prompt format
        chatml = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        payload = {
            "inputs": chatml,
            "parameters": {
                "max_new_tokens": 1024,
                "temperature": temperature,
                "top_p": 0.95,
                "return_full_text": False,
                "do_sample": temperature > 0,
            },
        }
        # Escape payload for shell — use base64 to avoid quoting issues
        import base64
        payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()

        # Python one-liner to invoke SageMaker on remote host
        remote_cmd = (
            f"python3 -c \""
            f"import boto3,json,base64,sys;"
            f"rt=boto3.client('sagemaker-runtime',region_name='{self.region}');"
            f"body=base64.b64decode(sys.stdin.read().strip());"
            f"r=rt.invoke_endpoint(EndpointName='{self.endpoint_name}',"
            f"ContentType='application/json',Body=body);"
            f"print(r['Body'].read().decode())"
            f"\""
        )

        try:
            r = subprocess.run(
                [
                    "ssh", "-i", self.ssh_key,
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=10",
                    f"{self.ssh_user}@{self.ssh_host}",
                    remote_cmd,
                ],
                input=payload_b64,
                capture_output=True,
                text=True,
                timeout=API_TIMEOUT + 30,  # extra time for SSH overhead
            )
            if r.returncode != 0:
                logger.error("SSH SageMaker error: %s", r.stderr[:300])
                raise RuntimeError(f"SSH command failed: {r.stderr[:200]}")

            result = json.loads(r.stdout.strip())
            if isinstance(result, list) and len(result) > 0:
                return result[0].get("generated_text", "")
            if isinstance(result, dict):
                return result.get("generated_text", "")
            return str(result)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse SageMaker response: %s", r.stdout[:200])
            raise
        except subprocess.TimeoutExpired:
            raise RuntimeError("SSH SageMaker call timed out")
        except Exception as e:
            logger.error("SSH SageMaker error: %s", e)
            raise


class APIBackend(ModelBackend):
    """Call a model via a generic OpenAI-compatible HTTP endpoint."""

    def __init__(self, api_url: str, api_key: str, model: str = "toke-7b-gate2"):
        import httpx
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.httpx = httpx

    def generate(self, prompt: str, temperature: float = 0.7) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": temperature,
        }

        try:
            # Try /v1/chat/completions first (OpenAI-compatible)
            resp = self.httpx.post(
                f"{self.api_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            pass

        # Fallback: try bare /generate
        try:
            payload_simple = {
                "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                "max_tokens": 1024,
                "temperature": temperature,
            }
            resp = self.httpx.post(
                f"{self.api_url}/generate",
                json=payload_simple,
                headers=headers,
                timeout=API_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("text", data.get("generated_text", data.get("output", "")))
        except Exception as e:
            logger.error("API error: %s", e)
            raise


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(task: Task) -> str:
    """Build a user prompt for a task, requesting a complete runnable program."""
    parts = [task.description]

    # Add I/O examples
    if task.test_cases:
        parts.append("\nExamples:")
        for i, tc in enumerate(task.test_cases[:3]):  # Show max 3 examples
            inp = tc.get("input", "")
            out = tc.get("expected_output", "")
            parts.append(f"  Input:  {inp}")
            parts.append(f"  Output: {out}")
            if i < min(2, len(task.test_cases) - 1):
                parts.append("")

    parts.append(
        "\nWrite a complete toke program (starting with m=) that reads input from "
        "stdin (one value per line) and prints the output to stdout. The program "
        "must have a f=main():i64 entry point."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def process_task(
    task: Task,
    backend: ModelBackend,
    n_samples: int,
    temperatures: list[float],
    stats: Stats,
    seen_hashes: set[str],
    output_dir: Path,
    check_only: bool = False,
) -> list[GenerationResult]:
    """Generate and evaluate candidates for a single task.

    If check_only is True, only run tkc --check (no build or execution).
    Programs that compile are treated as verified (useful when stdlib C glue
    is incomplete and prevents linking).
    """

    results = []
    verified = []

    for sample_idx in range(n_samples):
        temp = temperatures[sample_idx % len(temperatures)]
        stats.total_generated += 1

        prompt = build_prompt(task)

        # Generate
        try:
            raw = backend.generate(prompt, temperature=temp)
        except Exception as e:
            logger.warning("  [%s] sample %d: API error: %s", task.task_id, sample_idx, e)
            stats.api_errors += 1
            continue

        source = extract_source(raw)
        result = GenerationResult(
            task_id=task.task_id,
            raw_output=raw,
            source=source,
        )

        if not source or not source.startswith("m="):
            logger.debug("  [%s] sample %d: no valid source extracted", task.task_id, sample_idx)
            stats.errors += 1
            results.append(result)
            continue

        # Compile check
        ok, err = compile_check(source)
        result.compiles = ok
        result.compile_error = err
        if not ok:
            logger.debug(
                "  [%s] sample %d: compile fail: %s",
                task.task_id, sample_idx, err[:100],
            )
            results.append(result)
            continue

        stats.compile_pass += 1

        # Dedup check (before expensive build)
        h = source_hash(source)
        if h in seen_hashes:
            logger.debug("  [%s] sample %d: duplicate (hash %s)", task.task_id, sample_idx, h)
            results.append(result)
            continue

        # In check-only mode, treat compilation pass as verified
        if check_only:
            seen_hashes.add(h)
            stats.unique_programs += 1
            verified.append(result)
            logger.info(
                "  [%s] sample %d: COMPILE OK (temp=%.1f, hash=%s)",
                task.task_id, sample_idx, temp, h,
            )
            results.append(result)
            continue

        # Build binary
        built, bin_path, build_err = build_binary(source)
        result.builds = built
        result.build_error = build_err
        if not built:
            logger.debug(
                "  [%s] sample %d: build fail: %s",
                task.task_id, sample_idx, build_err[:100],
            )
            results.append(result)
            continue

        stats.build_pass += 1

        # Run tests
        all_pass = True
        test_details = []
        for tc in task.test_cases:
            stdin_input = tc.get("input", "")
            expected = tc.get("expected_output", "")
            passed, actual = run_test(bin_path, stdin_input, expected)
            test_details.append({
                "input": stdin_input,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            })
            if not passed:
                all_pass = False

        result.passes_tests = all_pass
        result.test_results = test_details

        # Cleanup binary tmpdir
        if bin_path:
            cleanup_tmpdir(os.path.dirname(bin_path))

        if all_pass:
            stats.functional_pass += 1
            seen_hashes.add(h)
            stats.unique_programs += 1
            verified.append(result)
            logger.info(
                "  [%s] sample %d: PASS (temp=%.1f, hash=%s)",
                task.task_id, sample_idx, temp, h,
            )
        else:
            failed_count = sum(1 for t in test_details if not t["passed"])
            logger.debug(
                "  [%s] sample %d: %d/%d tests failed",
                task.task_id, sample_idx, failed_count, len(test_details),
            )

        results.append(result)

    # Save verified programs
    if verified:
        task_dir = output_dir / task.task_id.replace("/", "_")
        task_dir.mkdir(parents=True, exist_ok=True)
        for i, v in enumerate(verified):
            out_file = task_dir / f"solution_{i:03d}.tk"
            out_file.write_text(v.source)

            # Also save metadata
            meta_file = task_dir / f"solution_{i:03d}.json"
            meta_file.write_text(json.dumps({
                "task_id": v.task_id,
                "source_hash": source_hash(v.source),
                "test_results": v.test_results,
            }, indent=2))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_tasks(path: Path, max_tasks: int | None = None) -> list[Task]:
    """Load tasks from a JSONL file."""
    tasks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            tasks.append(Task.from_dict(d))
            if max_tasks and len(tasks) >= max_tasks:
                break
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Self-improvement loop: generate, compile, test, filter."
    )
    parser.add_argument(
        "--tasks", type=Path, required=True,
        help="Path to task JSONL file (self_improve_tasks.jsonl format)",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "self_improve_output",
        help="Output directory for verified programs (default: data/self_improve_output/)",
    )
    parser.add_argument(
        "--samples", type=int, default=10,
        help="Number of generation samples per task (default: 10)",
    )
    parser.add_argument(
        "--max-tasks", type=int, default=None,
        help="Maximum number of tasks to process (default: all)",
    )
    parser.add_argument(
        "--temperatures", type=str, default="0.2,0.5,0.7,0.9,1.0",
        help="Comma-separated list of temperatures to cycle through",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Only run tkc --check, skip build and execution "
             "(useful when stdlib C glue is incomplete)",
    )

    # Backend selection
    parser.add_argument(
        "--backend", choices=["sagemaker", "ssh-sagemaker", "api"], default="ssh-sagemaker",
        help="Model backend: 'sagemaker' (direct boto3), 'ssh-sagemaker' (via SSH tunnel), "
             "or 'api' (HTTP endpoint). Default: ssh-sagemaker",
    )

    # SageMaker options
    parser.add_argument("--endpoint", default="toke-7b-gate2", help="SageMaker endpoint name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")

    # SSH-SageMaker options
    parser.add_argument("--ssh-host", default="3.107.90.156", help="SSH bastion host for SageMaker")
    parser.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/toke-console.pem"),
                        help="SSH private key path")
    parser.add_argument("--ssh-user", default="admin", help="SSH username")

    # API options
    parser.add_argument("--api-url", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--api-model", default="toke-7b-gate2", help="Model name for API calls")

    # Logging
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--log-file", type=Path, default=None,
        help="Write logs to file",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    # Validate tkc exists
    if not os.path.isfile(TKC):
        logger.error("Compiler not found at %s — set TKC env var", TKC)
        sys.exit(1)

    # Load tasks
    if not args.tasks.exists():
        logger.error("Task file not found: %s", args.tasks)
        sys.exit(1)
    tasks = load_tasks(args.tasks, args.max_tasks)
    logger.info("Loaded %d tasks from %s", len(tasks), args.tasks)

    # Parse temperatures
    temperatures = [float(t) for t in args.temperatures.split(",")]

    # Setup output directory
    args.output.mkdir(parents=True, exist_ok=True)

    # Initialize backend
    if args.backend == "sagemaker":
        logger.info("Using SageMaker backend: endpoint=%s region=%s", args.endpoint, args.region)
        backend = SageMakerBackend(args.endpoint, args.region)
    elif args.backend == "ssh-sagemaker":
        logger.info(
            "Using SSH-SageMaker backend: %s@%s -> endpoint=%s",
            args.ssh_user, args.ssh_host, args.endpoint,
        )
        backend = SSHSageMakerBackend(
            endpoint_name=args.endpoint,
            region=args.region,
            ssh_host=args.ssh_host,
            ssh_key=args.ssh_key,
            ssh_user=args.ssh_user,
        )
    else:
        if not args.api_url:
            logger.error("--api-url required for 'api' backend")
            sys.exit(1)
        api_key = args.api_key or os.environ.get("TOKE_API_KEY", "")
        logger.info("Using API backend: %s", args.api_url)
        backend = APIBackend(args.api_url, api_key, args.api_model)

    # Run the loop
    stats = Stats()
    seen_hashes: set[str] = set()
    all_results: list[GenerationResult] = []

    logger.info(
        "Starting self-improvement loop: %d tasks x %d samples = %d generations",
        len(tasks), args.samples, len(tasks) * args.samples,
    )
    logger.info("Temperatures: %s", temperatures)
    logger.info("Mode: %s", "check-only (compile verification)" if args.check_only else "full (compile + build + test)")
    logger.info("Output: %s", args.output)
    logger.info("Stdlib dir: %s", TKC_STDLIB_DIR)
    logger.info("")

    start_time = time.time()

    for task_idx, task in enumerate(tasks):
        stats.tasks_attempted += 1
        logger.info(
            "[%d/%d] Task %s (difficulty=%d)",
            task_idx + 1, len(tasks), task.task_id, task.difficulty,
        )

        results = process_task(
            task=task,
            backend=backend,
            n_samples=args.samples,
            temperatures=temperatures,
            stats=stats,
            seen_hashes=seen_hashes,
            output_dir=args.output,
            check_only=args.check_only,
        )
        all_results.extend(results)

    elapsed = time.time() - start_time

    # Print summary
    print("\n" + "=" * 70)
    print("  Self-Improvement Loop — Results")
    print("=" * 70)
    print()
    print(f"  Tasks attempted:     {stats.tasks_attempted}")
    print(f"  Total generated:     {stats.total_generated}")
    print(f"  API errors:          {stats.api_errors}")
    print(f"  Extraction errors:   {stats.errors}")
    print()
    print(f"  Compile pass:        {stats.compile_pass:>6d}  "
          f"({100 * stats.compile_pass / max(1, stats.total_generated - stats.api_errors):.1f}%)")
    print(f"  Build pass:          {stats.build_pass:>6d}  "
          f"({100 * stats.build_pass / max(1, stats.total_generated - stats.api_errors):.1f}%)")
    print(f"  Functional pass:     {stats.functional_pass:>6d}  "
          f"({100 * stats.functional_pass / max(1, stats.total_generated - stats.api_errors):.1f}%)")
    print(f"  Unique programs:     {stats.unique_programs:>6d}")
    print()
    print(f"  Elapsed time:        {elapsed:.1f}s")
    print(f"  Avg per generation:  {elapsed / max(1, stats.total_generated):.2f}s")
    print()
    print(f"  Output directory:    {args.output}")
    print("=" * 70)

    # Save run summary
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tasks_file": str(args.tasks),
        "tasks_attempted": stats.tasks_attempted,
        "samples_per_task": args.samples,
        "temperatures": temperatures,
        "backend": args.backend,
        "check_only": args.check_only,
        "total_generated": stats.total_generated,
        "api_errors": stats.api_errors,
        "extraction_errors": stats.errors,
        "compile_pass": stats.compile_pass,
        "compile_rate": stats.compile_pass / max(1, stats.total_generated - stats.api_errors),
        "build_pass": stats.build_pass,
        "build_rate": stats.build_pass / max(1, stats.total_generated - stats.api_errors),
        "functional_pass": stats.functional_pass,
        "functional_rate": stats.functional_pass / max(1, stats.total_generated - stats.api_errors),
        "unique_programs": stats.unique_programs,
        "elapsed_seconds": elapsed,
    }
    summary_file = args.output / "run_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    logger.info("Summary written to %s", summary_file)

    # Save detailed results for analysis
    details_file = args.output / "run_details.jsonl"
    with open(details_file, "w") as f:
        for r in all_results:
            f.write(json.dumps({
                "task_id": r.task_id,
                "compiles": r.compiles,
                "builds": r.builds,
                "passes_tests": r.passes_tests,
                "compile_error": r.compile_error[:200] if r.compile_error else "",
                "build_error": r.build_error[:200] if r.build_error else "",
                "test_results": r.test_results,
                "source_hash": source_hash(r.source) if r.source else "",
                "source_len": len(r.source),
            }) + "\n")
    logger.info("Details written to %s", details_file)


if __name__ == "__main__":
    main()
