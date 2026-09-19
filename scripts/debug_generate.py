#!/usr/bin/env python3
"""Debug: generate a few programs and show tkc errors."""
import anthropic
import subprocess
import tempfile
import os
import re

client = anthropic.Anthropic()

prompts = [
    """Write a toke program that reads a file and splits lines. Use i=file:std.file; and i=str:std.str; imports.

Rules:
- Module: m=name;
- Import: i=alias:std.module;
- Function: f=name(params):rettype{body};
- Types: i64, u64, f64, $str, bool, $void
- Arrays: @$type, .get(i), .len
- Let: let x=val; / let x=mut.val;
- Return: <expr
- Loop: lp(init;cond;step){body}
- If: if(cond){...}el{...}
- No underscores (use camelCase), 59-char lowercase set, no uppercase letters
Output ONLY a ```toke code block.""",

    """Write a toke program with a single function that finds the second largest element in an array.

Rules:
- Module: m=name;
- Function: f=name(params):rettype{body};
- Types: i64, u64, f64, $str, bool, $void
- Arrays: @i64, accessed via .get(i) not arr[i], .len returns u64
- Let: let x=val; / let x=mut.val;
- Return: <expr (bare < is return)
- Loop: lp(let i=0;i<n;i=i+1){body}
- If: if(cond){...}el{...}
- Equality: single = not ==
- No underscores, 59-char lowercase set, no uppercase
Output ONLY a ```toke code block.""",
]

for i, prompt in enumerate(prompts):
    print(f"\n{'='*60}")
    print(f"Prompt {i+1}")
    print(f"{'='*60}")
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text
    print(f"Raw response (first 500 chars):\n{raw[:500]}\n")

    m = re.search(r"```(?:toke|tk)?\s*\n(.*?)```", raw, re.DOTALL)
    code = m.group(1).strip() if m else raw.strip()
    print(f"Extracted code:\n{code}\n")

    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(code)
        fname = f.name
    r = subprocess.run(["tkc", "--check", fname], capture_output=True, text=True, timeout=15)
    os.unlink(fname)
    print(f"tkc exit code: {r.returncode}")
    if r.stderr:
        print(f"tkc stderr:\n{r.stderr[:1000]}")
    if r.returncode == 0:
        print("PASS")
    else:
        print("FAIL")
