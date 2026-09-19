#!/usr/bin/env python3
"""131.11 card-v2 gate: compile-check every code fragment in the syntax card.

The syntax card is the one document every generation wave reads, and until this
story it had no compile gate -- which is how it drifted (it kept workarounds for
Epic 127 bugs that had been fixed, and taught at least one construct that no
longer behaves as described).

Usage:
    check_card.py [--card PATH] [--verbose] [--only SECTION]

What it does
------------
Extracts two kinds of fragment from the card:

  * fenced blocks -- split into statement-level fragments on brace/paren depth,
    with the card's right-hand annotation column stripped (a fence line is
    `code   <2+ spaces>   prose`, and a line indented past column 20 with no
    code is a pure annotation continuation);
  * inline `backtick spans` in the prose that look like toke statements
    (contain `=`, end with `;`, or are a postfix/module call).

Each fragment is compiled with `tkc --check` inside a per-section harness that
binds the names that section's prose assumes (`text`, `arr`, `m2`, ...).

A fragment the card presents as ILLEGAL (the sentence around it says NEVER /
illegal / compile error / parse error / BROKEN / an E-code) is expected to FAIL
the check: for those, a clean compile is the bug. Fragments that are obvious
templates (`...`, `p:type`, `modulename`) are reported as TEMPLATE and are not
gated.

Exit 0 when every gated fragment matched its expectation, else 1.
"""
import argparse, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import pattern_common as pc
    TKC, CARD = pc.TKC, pc.CARD_PATH
except Exception:                                  # standalone use
    TKC = os.path.expanduser("~/tk/toke/tkc")
    CARD = os.path.join(HERE, "syntax_card.md")

ANNOT_COL = 20            # a fence line indented past this with no code is prose
TEMPLATE = re.compile(r"\.\.\.|modulename|typename|:type|\bbody\b|\bexpr\b|"
                      r"^\s*<expr\s*$|f:type|p:type")
# Polarity markers. An inline span is classified by the NEAREST marker on its
# own line (see polarity()), which is what separates the card's three kinds of
# claim -- and the distinction matters: "the card says this is a compile error"
# and "the card says this compiles and then misbehaves" are opposite
# expectations for `tkc --check`.
#
#   NEG_COMPILE  -- the card says it does not compile     -> expect check to FAIL
#   NEG_RUNTIME  -- the card says it compiles and is wrong -> expect check to PASS
#   STYLE        -- the card says do not write it, but it is legal -> PASS
#   POS          -- the card teaches it                    -> expect check to PASS
NEG_COMPILE = re.compile(
    r"is a compile error|is a parse error|does not compile|do not write it|"
    r"\bis illegal\b|\billegal\b|fails `--check`|fails --check|"
    r"is a compile-time error", re.I)
NEG_RUNTIME = re.compile(
    r"silently|SIGSEGV|segfault|crash|DANGLE|prints a (raw )?pointer|"
    r"does NOT mutate|mutates the RECEIVER|WRONG|"
    r"passes `--check`|type-checks|drops|appends NOTHING|explodes", re.I)
STYLE = re.compile(
    r"NEVER write|Never simulate|never idiomatic|not idiomatic|\bAvoid\b|"
    r"no dead\b|pure token cost|costs tokens|never rely", re.I)
POS = re.compile(r"always|\buse\b|prefer|instead|correct|→|ALWAYS|PREFERRED|"
                 r"\bfine\b|\bvalid\b|\bworks?\b|compiles|is how|canonical|"
                 r"takes the separator", re.I)
MARKER_WINDOW = 45        # chars: past this the marker no longer governs the span

# Per-section harness preludes: the bindings that section's prose assumes.
# key = the `##` heading text (lowercased, first three words).
PRELUDE = {
    "file structure": [],
    "character set": [],
    "types": [],
    "bindings and assignment": ["let arr=@(1;2;3);", "let c=true;", "let i=0;"],
    "operators": ["let a=mut.@(1;2);", "let x=2;"],
    "if is an": ["let n=95;", "let c=true;", "let a=1;", "let b=2;"],
    "loops": ["let n=3;"],
    "match expression": ["let x=\"12\";", "let v=12;"],
    "error handling": [],
    "strings no concatenation": ["let text=\"alpha beta gamma\";", "let n=7;",
                                 "let a=\"a\";", "let b=\"b\";", "let x=\"12\";", "let y=\"y\";",
                                 "let parts=@(\"p\";\"q\");", "let arr=mut.@(\"z\");"],
    "arrays and maps": ["let arr=mut.@(1;2;3);", "let a=mut.@(1;2;3);", "let i=0;",
                        "let j=1;", "let v=9;", "let k=\"k\";", "let n=3;",
                        "let m2=mut.@(\"a\":1);", "let m=@(\"a\":1);", "let c=true;"],
    "std modules": [],
    "program conventions and": ["let value=1;", "let line=\"a,b\";"],
    "patterns measured canonical": ["let n=7;", "let v=9;", "let k=\"k\";",
                                    "let xs=mut.@(3;1;2);", "let out=mut.@(1);",
                                    "let av=@(\"-v\";\"f\");", "let b=true;"],
    "task hygiene": [],
}
IMPORTS = ["i=io:std.io;", "i=s:std.str;", "i=json:std.json;", "i=file:std.file;",
           "i=fmt:std.fmt;"]
# Mirrors the helper names the card's own examples reference (&dbl, &p, &cmp,
# &notflag, &fadd) plus the `$p` struct its array examples use.
TOPTYPES = ["t=$rec{v:i64};"]
HELPERS = ["f=dbl(x:i64):i64{<x*2};", "f=cmp(a:i64;b:i64):i64{<a-b};",
           "f=notflag(x:str):bool{<s.len(x)>0};", "f=p(x:i64):bool{<x>0};",
           "f=fadd(acc:i64;x:i64):i64{<acc+x};"]


def sec_key(heading):
    h = re.sub(r"[^a-z0-9 ]", " ", heading.lower()).split()
    return " ".join(h[:3])


def strip_annot(line):
    """Drop the card's right-hand prose column. Returns '' for a prose-only line."""
    if not line.strip():
        return ""
    lead = len(line) - len(line.lstrip())
    if lead >= ANNOT_COL:                          # annotation continuation line
        return ""
    body = line.strip()
    parts = re.split(r"\s{2,}", body, maxsplit=1)
    return parts[0].strip()


def balanced(text):
    depth, instr, esc = 0, False, False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == "\\" and instr:
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
    return depth <= 0


def extract(card_text):
    """-> [ {kind, section, code, line, expect, pre} ]

    A fence reads as a sequence, so each fence fragment carries the fragments
    before it in the same fence as its preamble (`pre`) -- that is what makes
    the swap example, where `r` is bound three lines above its use, checkable."""
    frags, section, in_fence, buf, start = [], "(preamble)", False, [], 0
    fence_pre = []
    lines = card_text.splitlines()
    for ln, raw in enumerate(lines, 1):
        if raw.lstrip().startswith("```"):
            if in_fence and buf:                   # unterminated tail
                frags.append(mk("fence", section, "\n".join(buf), start, card_text,
                                pre=list(fence_pre)))
                buf = []
            in_fence = not in_fence
            if not in_fence:
                fence_pre = []
            continue
        if not in_fence:
            if raw.startswith("## "):
                section = raw[3:].strip()
            for m in re.finditer(r"`([^`]+)`", raw):
                if looks_like_stmt(m.group(1)):
                    frags.append(mk("inline", section, m.group(1), ln, raw,
                                    at=m.start(1)))
            continue
        code = strip_annot(raw)
        if not code:
            continue
        if not buf:
            start = ln
        buf.append(code)
        joined = "\n".join(buf)
        # The card's reference fences list one construct per line with no
        # terminator (`arr.len`, `arr=arr.append(v)`); close on any balanced
        # line that is not opening a block.
        if balanced(joined) and not joined.rstrip().endswith(("{", "el")):
            frags.append(mk("fence", section, joined, start, card_text,
                            pre=list(fence_pre)))
            fence_pre.append(joined)
            buf = []
    if buf:
        frags.append(mk("fence", section, "\n".join(buf), start, card_text,
                        pre=list(fence_pre)))
    return frags


def looks_like_stmt(span):
    s = span.strip()
    if len(s) < 4 or "\n" in s:
        return False
    if s.startswith(("@", "$", "#")) and "=" not in s:
        return False                               # a type, not a statement
    if re.fullmatch(r"[a-z0-9.!\[\]@$:|_ -]+", s) and "(" not in s and "=" not in s:
        return False                               # bare identifier / type / prose
    if re.fullmatch(r"[A-Z]\d{4}", s):
        return False
    if not re.match(r"^(let|if|lp|mt|rt|br|<|[a-z][a-z0-9]*[.(=+])", s):
        return False                               # not a statement head
    return ("=" in s or s.endswith(";") or
            re.match(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9]*\(", s) or
            re.match(r"^(let|if|lp|mt|<|rt|br)\b", s))


def polarity(line, at):
    """Classify an inline span by the nearest polarity marker on its line.
    Returns ('fail'|'ok', tally-bucket) or (None, None) when no marker is close
    enough -- unclassified spans are reported, never gated."""
    best, kind, bucket = None, None, None
    for rx, k, b in ((NEG_COMPILE, "fail", "negok"),
                     (NEG_RUNTIME, "ok", "runtime_bug"),
                     (STYLE, "ok", "style"),
                     (POS, "ok", "ok")):
        for m in rx.finditer(line):
            d = 0 if m.start() <= at <= m.end() else min(abs(m.start() - at),
                                                         abs(m.end() - at))
            if best is None or d < best:
                best, kind, bucket = d, k, b
    if best is None or best > MARKER_WINDOW:
        return "ok", "unmarked"       # prose code with no polarity marker: teaching
    return kind, bucket


def mk(kind, section, code, line, ctx, pre=None, at=0):
    # Fences are the card's teaching code and are always expected to compile.
    # An inline span is gated only when the prose around it says, close enough
    # to be unambiguous, whether it is the right form or a broken one.
    if kind == "fence":
        expect, bucket = "ok", "ok"
    else:
        expect, bucket = polarity(ctx, at)
    return {"kind": kind, "section": section, "code": code, "line": line,
            "expect": expect, "bucket": bucket, "pre": pre or []}


def harness(frag):
    code = frag["code"].strip()
    key = sec_key(frag["section"])
    pre = PRELUDE.get(key, [])
    if code.startswith("m="):
        return code if "f=main" in code else None
    if code.startswith("i="):
        return None
    if code.startswith(("t=", "f=")):
        top = [p for p in frag.get("pre", [])
               if p.startswith(("t=", "f=")) and not TEMPLATE.search(p)]
        types = [] if code.startswith("t=$rec{") else TOPTYPES
        body = ["m=cardchk;"] + IMPORTS + types + top + [code]
        if not any("f=main" in x for x in top + [code]):
            body.append("f=main():i64{<0};")
        return "\n".join(body)
    pre_lines = [p for p in frag.get("pre", []) if not TEMPLATE.search(p)]
    pre_lines = [p if p.rstrip().endswith((";", "}")) else p + ";" for p in pre_lines
                 if not p.lstrip().startswith("<")]
    bound = set(re.findall(r"let\s+([a-z][a-z0-9]*)=", code + "\n" + "\n".join(pre_lines)))
    pre = [p for p in pre if re.match(r"let\s+([a-z][a-z0-9]*)=", p)
           and re.match(r"let\s+([a-z][a-z0-9]*)=", p).group(1) not in bound]
    stmt = code if code.rstrip().endswith((";", "}")) else code + ";"
    # A fragment whose last DEPTH-0 statement is a return supplies main's own
    # return -- appending `<0` after it would be E5002 unreachable code.
    tail = "" if last_stmt(stmt).lstrip().startswith(("<", "rt ")) else "<0"
    return "\n".join(["m=cardchk;"] + IMPORTS + TOPTYPES + HELPERS +
                     ["f=main():i64{"] + pre + pre_lines + [stmt, tail, "};"])


def last_stmt(code):
    """The final depth-0 statement of a fragment (`;` inside braces or a string
    does not separate statements)."""
    depth, instr, esc, start, out = 0, False, False, 0, ""
    for i, ch in enumerate(code):
        if esc:
            esc = False
            continue
        if instr:
            if ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == ";" and depth == 0:
            piece = code[start:i].strip()
            if piece:
                out = piece
            start = i + 1
    tailpiece = code[start:].strip()
    return tailpiece or out


def check(src, tmp):
    p = os.path.join(tmp, "frag.tk")
    open(p, "w").write(src + "\n")
    r = subprocess.run([TKC, "--check", p], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", default=CARD)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    text = open(a.card, encoding="utf-8").read()
    frags = extract(text)
    if a.only:
        frags = [f for f in frags if a.only.lower() in f["section"].lower()]
    tally = {"ok": 0, "bad": 0, "template": 0, "skip": 0, "negok": 0,
             "runtime_bug": 0, "style": 0, "unmarked": 0}
    bad, unc = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for f in frags:
            if TEMPLATE.search(f["code"]):
                tally["template"] += 1
                if a.verbose:
                    print(f"TEMPLATE  L{f['line']:<4} [{f['section'][:22]}] {f['code'][:64]}")
                continue
            if f["bucket"] == "unmarked":
                unc.append(f)
            src = harness(f)
            if src is None:
                tally["skip"] += 1
                continue
            rc, out = check(src, tmp)
            got = "ok" if rc == 0 else "fail"
            if got == f["expect"]:
                tally[f["bucket"]] += 1
                if a.verbose:
                    print(f"{'PASS/' + f['bucket']:22} "
                          f"L{f['line']:<4} [{f['section'][:22]}] {f['code'][:64]}")
            else:
                tally["bad"] += 1
                bad.append((f, got, out))
    print(f"\ncard: {a.card}")
    print(f"fragments {len(frags)}:  compile-clean {tally['ok']}  |  "
          f"rejected-as-the-card-says {tally['negok']}  |  "
          f"documented-runtime-bug, compiles {tally['runtime_bug']}  |  "
          f"style-banned, compiles {tally['style']}  |  "
          f"MISMATCH {tally['bad']}  |  template {tally['template']}  |  "
          f"not-checkable {tally['skip']}  |  unmarked-span, compiles "
          f"{tally['unmarked']}")
    if a.verbose and unc:
        print("\nspans with no polarity marker within "
              f"{MARKER_WINDOW} chars (gated as must-compile):")
        for f in unc:
            print(f"   L{f['line']:<4} {f['code'][:70]}")
    for f, got, out in bad:
        why = ("compiles clean, but the card says it is rejected"
               if got == "ok" else "the card teaches it but it does NOT compile")
        print(f"\n-- L{f['line']} [{f['section']}] ({f['kind']}) {why}")
        print("   " + f["code"].replace("\n", "\n   "))
        if got == "fail":
            for l in out.splitlines()[:4]:
                print("     | " + l)
    return 1 if tally["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
