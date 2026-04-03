"""Focused generation round 2: retry all 120 on DeepSeek only (it's working well).
Also run failed ones with a shorter prompt on Anthropic."""
import asyncio
import json
import hashlib
import logging
import os
import sys
import uuid
import subprocess
import tempfile
import re
from pathlib import Path
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("focused")

TKC = "/opt/toke-corpus/bin/tkc"
CORPUS_DIR = Path("/opt/toke-corpus/corpus/phase_a")
PROMPTS_DIR = Path("/opt/toke-corpus/prompts")

system_base = (PROMPTS_DIR / "system_base.md").read_text()
system_prompt = (PROMPTS_DIR / "system.md").read_text()
FULL_SYSTEM = system_base + "\n\n" + system_prompt

with open("/opt/toke-corpus/generator/new_tasks.json") as f:
    new_tasks_raw = json.load(f)

# Build tasks
tasks = []
idx = 0
for category, items in new_tasks_raw.items():
    for name, signature, description in items:
        tasks.append({"id": f"{category}-N{idx:04d}", "category": category, "name": name,
                       "signature": signature, "description": description})
        idx += 1

# Load existing task_ids to skip already-done ones
existing_tids = set()
existing_hashes = set()
for f in CORPUS_DIR.rglob("*.json"):
    try:
        with open(f) as fh:
            d = json.load(fh)
        existing_tids.add(d.get("task_id", ""))
        existing_hashes.add(hashlib.md5(d.get("tk_source", "").encode()).hexdigest())
    except: pass

remaining = [t for t in tasks if t["id"] not in existing_tids]
logger.info("Total: %d, Already done: %d, Remaining: %d", len(tasks), len(tasks)-len(remaining), len(remaining))

import httpx

def autofix(source):
    s = source
    if re.search(r'\bfor\s*\(', s): s = re.sub(r'\bfor\s*\(', 'lp(', s)
    if re.search(r'\bwhile\s*\(', s): s = re.sub(r'\bwhile\s*\(', 'lp(', s)
    if re.search(r'\belse\s*\{', s): s = re.sub(r'\belse\s*\{', 'el{', s)
    if re.search(r'\belif\s*\(', s): s = re.sub(r'\belif\s*\(', 'el{if(', s)
    if '==' in s: s = s.replace('==', '=')
    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    if re.search(r'\breturn\s+', s): s = re.sub(r'\breturn\s+', '<', s)
    s = re.sub(r'\bString\b', 'Str', s)
    s = re.sub(r'//.*$', '', s, flags=re.MULTILINE)
    if '{{' in s and '}}' in s: s = s.replace('{{', '{').replace('}}', '}')
    return s.strip()

def compile_check(source):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tk", delete=False) as f:
        f.write(source); path = f.name
    try:
        r = subprocess.run([TKC, "--check", path], capture_output=True, text=True, timeout=5)
        return r.returncode == 0, r.stderr or r.stdout
    except subprocess.TimeoutExpired:
        return False, "timeout"
    finally:
        os.unlink(path)

def extract_source(text):
    m = re.search(r'```(?:toke|tk)?\s*\n(.*?)```', text, re.DOTALL)
    if m: return m.group(1).strip()
    lines = text.strip().split('\n')
    src_lines = []
    in_code = False
    for line in lines:
        if line.strip().startswith('M=') or line.strip().startswith('F=') or line.strip().startswith('T='):
            in_code = True
        if in_code: src_lines.append(line)
    if src_lines: return '\n'.join(src_lines).strip()
    return text.strip()

def write_entry(task, source, model):
    cat_dir = CORPUS_DIR / task["category"]
    cat_dir.mkdir(parents=True, exist_ok=True)
    entry_id = f"{task['id']}-{uuid.uuid4().hex[:8]}"
    entry = {"id": entry_id, "version": "1.0", "phase": "A", "task_id": task["id"],
             "tk_source": source, "tk_tokens": len(source), "attempts": 1, "model": model,
             "validation": {"compiler_exit_code": 0, "error_codes": []},
             "differential": {}, "judge": {}, "references": {}}
    path = cat_dir / f"{entry_id}.json"
    with open(path, "w") as f:
        json.dump(entry, f, ensure_ascii=False)
    return path

async def call_llm(client, prompt, provider):
    if provider == "deepseek":
        r = await client.post("https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
            json={"model": "deepseek-chat", "messages": [
                {"role": "system", "content": FULL_SYSTEM},
                {"role": "user", "content": prompt}
            ], "temperature": 0.7, "max_tokens": 1024}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], "deepseek-chat"
    elif provider == "anthropic":
        # Use shorter system prompt for Anthropic
        short_sys = system_base[:3000]  # Truncate to avoid 400
        r = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "system": short_sys,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7, "max_tokens": 1024}, timeout=30)
        r.raise_for_status()
        return r.json()["content"][0]["text"], "claude-haiku-4-5-20251001"
    elif provider == "openai":
        r = await client.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": "gpt-4.1-mini", "messages": [
                {"role": "system", "content": FULL_SYSTEM},
                {"role": "user", "content": prompt}
            ], "temperature": 0.7, "max_tokens": 1024}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], "gpt-4.1-mini"

async def main():
    client = httpx.AsyncClient(timeout=60)
    sem = asyncio.Semaphore(8)
    accepted = 0
    failed_tasks = []
    results = Counter()

    async def process(task, provider):
        nonlocal accepted
        async with sem:
            prompt = (
                f"Write a toke function with this signature: {task['signature']}\n"
                f"Description: {task['description']}\n"
                f"Module name: {task['name']}\n\n"
                f"Output ONLY the toke source code. Start with M= for the module declaration.\n"
                f"Key rules: loops=`lp`, else=`el`, return=`<`, equality=`=` not `==`, "
                f"params separated by `;`, no `for`/`while`/`else`/`return` keywords."
            )
            try:
                text, model = await call_llm(client, prompt, provider)
                source = extract_source(text)
                ok, err = compile_check(source)
                if not ok:
                    fixed = autofix(source)
                    ok, err = compile_check(fixed)
                    if ok: source = fixed
                if not ok:
                    return task, False, err[:80]
                h = hashlib.md5(source.encode()).hexdigest()
                if h in existing_hashes:
                    return task, False, "duplicate"
                existing_hashes.add(h)
                write_entry(task, source, model)
                accepted += 1
                results[task["category"]] += 1
                logger.info("OK %s (%s)", task["id"], provider)
                return task, True, ""
            except Exception as e:
                return task, False, str(e)[:80]

    # Round 1: DeepSeek for all remaining
    logger.info("=== Round 1: DeepSeek (%d tasks) ===", len(remaining))
    round1 = await asyncio.gather(*[process(t, "deepseek") for t in remaining])
    
    still_failed = [task for task, ok, _ in round1 if not ok]
    logger.info("Round 1: %d/%d accepted, %d failed", accepted, len(remaining), len(still_failed))

    # Round 2: Retry failures with OpenAI
    if still_failed:
        r1_accepted = accepted
        logger.info("=== Round 2: OpenAI (%d tasks) ===", len(still_failed))
        round2 = await asyncio.gather(*[process(t, "openai") for t in still_failed])
        still_failed2 = [task for task, ok, _ in round2 if not ok]
        logger.info("Round 2: %d more accepted, %d still failed", accepted - r1_accepted, len(still_failed2))

    # Round 3: Try Anthropic with shorter prompt for remaining
    if still_failed2:
        r2_accepted = accepted
        logger.info("=== Round 3: Anthropic (%d tasks) ===", len(still_failed2))
        round3 = await asyncio.gather(*[process(t, "anthropic") for t in still_failed2])
        still_failed3 = [task for task, ok, _ in round3 if not ok]
        logger.info("Round 3: %d more accepted, %d still failed", accepted - r2_accepted, len(still_failed3))
        if still_failed3:
            logger.info("Still failed:")
            for task, _, err in round3:
                if not _:
                    logger.info("  %s: %s", task["id"], err)

    await client.aclose()
    logger.info("=== FINAL ===")
    logger.info("Accepted: %d / %d (%.1f%%)", accepted, len(remaining), accepted/len(remaining)*100 if remaining else 0)
    for cat in sorted(results):
        logger.info("  %s: %d", cat, results[cat])

asyncio.run(main())
