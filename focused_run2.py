"""Focused generation: 120 new tasks on DeepSeek + Anthropic.
Standalone script that calls LLM APIs directly, validates with tkc, writes corpus entries.
"""
import asyncio
import json
import hashlib
import logging
import os
import sys
import uuid
import subprocess
import tempfile
import time
from pathlib import Path
from collections import Counter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("focused")

# --- Config ---
TKC = "/opt/toke-corpus/bin/tkc"
CORPUS_DIR = Path("/opt/toke-corpus/corpus/phase_a")
PROMPTS_DIR = Path("/opt/toke-corpus/prompts")

# Load prompts
system_base = (PROMPTS_DIR / "system_base.md").read_text()
system_prompt = (PROMPTS_DIR / "system.md").read_text()
gen_prompt = (PROMPTS_DIR / "generate_toke.md").read_text()

FULL_SYSTEM = system_base + "\n\n" + system_prompt

# Load tasks
with open("/opt/toke-corpus/generator/new_tasks.json") as f:
    new_tasks_raw = json.load(f)

tasks = []
idx = 0
for category, items in new_tasks_raw.items():
    for name, signature, description in items:
        tasks.append({
            "id": f"{category}-N{idx:04d}",
            "category": category,
            "name": name,
            "signature": signature,
            "description": description,
        })
        idx += 1

logger.info("Loaded %d tasks", len(tasks))

# Load existing hashes for dedup
existing_hashes = set()
for f in CORPUS_DIR.rglob("*.json"):
    try:
        with open(f) as fh:
            d = json.load(fh)
        src = d.get("tk_source", "")
        existing_hashes.add(hashlib.md5(src.encode()).hexdigest())
    except:
        pass
logger.info("Loaded %d existing source hashes", len(existing_hashes))

# --- LLM clients ---
import httpx

PROVIDERS = {
    "deepseek": {
        "url": "https://api.deepseek.com/chat/completions",
        "key": os.environ["DEEPSEEK_API_KEY"],
        "model": "deepseek-chat",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "key": os.environ["ANTHROPIC_API_KEY"],
        "model": "claude-haiku-4-5-20251001",
    },
}

async def call_deepseek(client, prompt):
    resp = await client.post(
        PROVIDERS["deepseek"]["url"],
        headers={"Authorization": f"Bearer {PROVIDERS['deepseek']['key']}"},
        json={
            "model": PROVIDERS["deepseek"]["model"],
            "messages": [
                {"role": "system", "content": FULL_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"], "deepseek-chat"

async def call_anthropic(client, prompt):
    resp = await client.post(
        PROVIDERS["anthropic"]["url"],
        headers={
            "x-api-key": PROVIDERS["anthropic"]["key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": PROVIDERS["anthropic"]["model"],
            "system": FULL_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1024,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"], "claude-haiku-4-5-20251001"

# --- Autofixer ---
import re

def autofix(source):
    """Fix common LLM mistakes."""
    fixes = []
    s = source
    # for -> lp
    if re.search(r'\bfor\s*\(', s):
        s = re.sub(r'\bfor\s*\(', 'lp(', s)
        fixes.append("for->lp")
    # while -> lp
    if re.search(r'\bwhile\s*\(', s):
        s = re.sub(r'\bwhile\s*\(', 'lp(', s)
        fixes.append("while->lp")
    # else -> el
    if re.search(r'\belse\s*\{', s):
        s = re.sub(r'\belse\s*\{', 'el{', s)
        fixes.append("else->el")
    # elif -> el{if(
    if re.search(r'\belif\s*\(', s):
        s = re.sub(r'\belif\s*\(', 'el{if(', s)
        fixes.append("elif->el{if")
    # == -> = (in conditions, not in assignments)
    if '==' in s:
        s = s.replace('==', '=')
        fixes.append("==->= ")
    # True/False -> true/false
    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    if 'True' in source or 'False' in source:
        fixes.append("bool case")
    # return -> <
    if re.search(r'\breturn\s+', s):
        s = re.sub(r'\breturn\s+', '<', s)
        fixes.append("return-><")
    # String -> Str
    s = re.sub(r'\bString\b', 'Str', s)
    # // comments
    s = re.sub(r'//.*$', '', s, flags=re.MULTILINE)
    # {{ -> {  (double braces from f-string prompts)
    if '{{' in s and '}}' in s:
        s = s.replace('{{', '{').replace('}}', '}')
        fixes.append("double-braces")
    return s.strip(), fixes

# --- Compiler ---
def compile_check(source):
    """Run tkc --check. Return (success, error)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tk", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        result = subprocess.run(
            [TKC, "--check", path],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "timeout"
    finally:
        os.unlink(path)

# --- Extract source ---
def extract_source(text):
    """Extract toke source from LLM response."""
    # Try fenced block first
    m = re.search(r'```(?:toke|tk)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try finding M= line
    lines = text.strip().split('\n')
    src_lines = []
    in_code = False
    for line in lines:
        if line.strip().startswith('M=') or line.strip().startswith('F=') or line.strip().startswith('T='):
            in_code = True
        if in_code:
            src_lines.append(line)
    if src_lines:
        return '\n'.join(src_lines).strip()
    return text.strip()

# --- Write corpus entry ---
def write_entry(task, source, model):
    cat_dir = CORPUS_DIR / task["category"]
    cat_dir.mkdir(parents=True, exist_ok=True)
    
    entry_id = f"{task['id']}-{uuid.uuid4().hex[:8]}"
    entry = {
        "id": entry_id,
        "version": "1.0",
        "phase": "A",
        "task_id": task["id"],
        "tk_source": source,
        "tk_tokens": len(source),
        "attempts": 1,
        "model": model,
        "validation": {"compiler_exit_code": 0, "error_codes": []},
        "differential": {},
        "judge": {},
        "references": {},
    }
    
    path = cat_dir / f"{entry_id}.json"
    with open(path, "w") as f:
        json.dump(entry, f, ensure_ascii=False, indent=None)
    return path

# --- Main ---
async def main():
    client = httpx.AsyncClient(timeout=60)
    
    sem = asyncio.Semaphore(8)
    accepted = 0
    failed = 0
    results = Counter()
    
    async def process(task, provider_name, call_fn):
        nonlocal accepted, failed
        async with sem:
            prompt = gen_prompt.replace("{description}", task["description"])
            prompt = prompt.replace("{signature}", task["signature"])
            prompt = prompt.replace("{category}", task["category"])
            # Simple prompt
            prompt = (
                f"Write a toke function with this signature: {task['signature']}\n"
                f"Description: {task['description']}\n"
                f"Module name: {task['name']}\n\n"
                f"Output ONLY the toke source code. Start with M= for the module declaration. "
                f"Remember: loops use `lp`, else uses `el`, return uses `<`, equality is `=` not `==`, "
                f"parameters separated by `;` not `,`."
            )
            
            try:
                text, model = await call_fn(client, prompt)
                source = extract_source(text)
                
                # Compile check
                ok, err = compile_check(source)
                
                if not ok:
                    # Try autofix
                    fixed, fixes = autofix(source)
                    if fixes:
                        ok2, err2 = compile_check(fixed)
                        if ok2:
                            source = fixed
                            ok = True
                            logger.info("Autofixed %s: %s", task["id"], fixes)
                
                if not ok:
                    failed += 1
                    logger.warning("FAIL %s (%s): %s", task["id"], provider_name, err[:80])
                    return False
                
                # Dedup check
                h = hashlib.md5(source.encode()).hexdigest()
                if h in existing_hashes:
                    failed += 1
                    logger.warning("DUP %s", task["id"])
                    return False
                existing_hashes.add(h)
                
                # Write
                path = write_entry(task, source, model)
                accepted += 1
                results[task["category"]] += 1
                logger.info("OK %s (%s) -> %s", task["id"], provider_name, path.name)
                return True
                
            except Exception as e:
                failed += 1
                logger.error("ERR %s: %s", task["id"], e)
                return False
    
    # Alternate between providers for diversity
    coros = []
    for i, task in enumerate(tasks):
        if i % 2 == 0:
            coros.append(process(task, "deepseek", call_deepseek))
        else:
            coros.append(process(task, "anthropic", call_anthropic))
    
    await asyncio.gather(*coros)
    
    # Retry failures with the other provider
    # (skipping for speed — can run again if needed)
    
    await client.aclose()
    
    logger.info("=== RESULTS ===")
    logger.info("Accepted: %d / %d (%.1f%%)", accepted, accepted + failed, 
                accepted / (accepted + failed) * 100 if accepted + failed else 0)
    for cat in sorted(results):
        logger.info("  %s: %d", cat, results[cat])

asyncio.run(main())
