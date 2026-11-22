#!/usr/bin/env python3
"""Story 131.62 step (a): measure what a wrong spec prompt would have done.

`prompts/spec-reference.md` before 132.15 / 131.62 (git 11392c9..0691ea1)
taught a 56-character set with `^`/`~` reserved, 12 keywords, no `mt`/`sc`,
`=` as equality, no `!=`/`&&`/`%`, statement-only `if`, no `el if` and the
retired `expr|{...}` match. This tool measures, read-only, whether that
teaching reached the v0.4 corpus and whether its fingerprints are in it:

  provenance  which prompt material each record was generated under
              (record `regen.card_sha`, the worker prompts on disk, which
              harness files read spec-reference.md, the library generator's
              quick reference), and whether the generation card taught the
              four constructs.
  constructs  `mt`, `sc`, `^`, `~` in code (string literals stripped,
              interpolation code kept) in three views: GEN = the raw worker
              output for every banked regen id (work/shard_NN/gen/*.tk.done,
              the model's own text), LIVE = the banked record's tk_source
              today, LIB = the 1,583 library programs.
  demand      the reference expectation: what each task's description asks
              for (bitwise / complement, concurrency, error union, an
              explicit "use pattern matching" mandate), so that use is
              read as a rate over the tasks that call for the construct.
  workaround  compile-clean shapes the wrong teaching would produce --
              the pre-132.15 page itself prescribed "!(a=b) for not-equal,
              !(a>b) for less-or-equal, nested if for logical AND/OR, a-a/b*b
              for modulo" -- (xor / not / mod / != / <= emulation, nested-if
              AND, hand-rolled int parse instead
              of `mt s.toint`) plus the 131.13 / 131.64 sweep's counts of
              the shapes it teaches indirectly (mut-flag-if, flag-soup,
              nested-if-ladder).
  probes      the pinned tkc compiles each construct the prompt called
              reserved or absent.

Writes regen/freeze/measure_131.62.json (no record, ledger or MANIFEST is
touched). Usage:

  python3 regen/measure_131_62.py --tkc <tkc> [--corpus corpus/regen_v04]
      [--worker-prompt ~/tk/.claude/workflows/regen-shard-wave.js]
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "freeze", "measure_131.62.json")
SAMPLE_PER_CELL = 3

CONSTRUCTS = {
    "mt": re.compile(r"(?<![a-z0-9])mt(?![a-z0-9])"),
    "sc": re.compile(r"(?<![a-z0-9])sc\s*\{"),
    "caret": re.compile(r"\^"),
    "tilde": re.compile(r"~"),
}

# Compile-clean shapes a model taught "no ^ ~ % != && el-if, if is a
# statement, no mt" would write instead. Each is a deliberately narrow
# regex over code with strings removed; hits are listed so they can be read.
WORKAROUNDS = {
    # (a|b)-(a&b)  or  a+b-2*(a&b): xor without ^
    "xor_emulation": re.compile(
        r"\(\s*([a-z0-9.]+)\s*\|\s*([a-z0-9.]+)\s*\)\s*-\s*\(\s*\1\s*&\s*\2\s*\)"
        r"|([a-z0-9.]+)\s*\+\s*([a-z0-9.]+)\s*-\s*2\s*\*\s*\(\s*\3\s*&\s*\4\s*\)"),
    # per-bit xor in a loop: a%2!=b%2 (or ==), the bit-by-bit fallback
    "xor_bitloop": re.compile(
        r"[a-z0-9.)]+\s*%\s*2\s*!=\s*[a-z0-9.(]+\s*%\s*2"),
    # -x-1 / 0-x-1 / -1-x : bitwise not without ~
    "not_emulation": re.compile(
        r"(?:(?<=[=(<;,{])|\b0)\s*-\s*\(?\s*[a-z][a-z0-9.]*\s*\)?\s*-\s*1(?![0-9])"
        r"|(?<=[=(<;{])\s*-\s*1\s*-\s*[a-z][a-z0-9.]*"),
    # x-(x/y)*y, x-x/y*y, ((e)-((e)/26)*26): modulo without %
    "mod_emulation": re.compile(
        r"-[^;{}=<>]{0,40}?/\s*([a-z0-9.]+)\s*\)*\s*\*\s*\1(?![a-z0-9.])"),
    # !(a==b) : != without != (the legacy input spells it !(a=b))
    "neq_emulation": re.compile(r"!\(\s*[^()]*?(?:==|[^=!<>:]=(?!=))[^()]*\)"),
    # !(a>b) / !(a<b) : <= and >= without <= / >= (the page's own advice)
    "le_ge_emulation": re.compile(
        r"!\(\s*[a-z0-9.()]+\s*[<>](?!=)\s*[a-z0-9.()-]+\s*\)"),
    # if(a){if(b){...}} with nothing else in the outer body: && without &&
    # (whitespace is removed before this one is matched, see features())
    "nested_if_and": re.compile(r"if\([^{}]*\)\{if\([^{}]*\)\{[^{}]*\};?\}(?!el)"),
    # parse-int form b: charcode digit loop instead of mt s.toint
    # (a digit test alone, c>=48&&c<=57, is not counted: only the
    # accumulate-the-digit form v*10+c-48 that replaces s.toint)
    "handrolled_int_parse": re.compile(r"\*\s*10\s*\+\s*\(?\s*[a-z0-9.()]+\s*-\s*48\b"),
}

# the four substitutions the pre-132.15 page spelled out in so many words
PAGE_PRESCRIBED = ("mod_emulation", "neq_emulation", "le_ge_emulation", "nested_if_and")
WKEYS = list(WORKAROUNDS) + ["any_page_prescribed"]

DEMAND = {
    "xor_named": re.compile(r"\bxor\b|exclusive or", re.I),
    "complement_named": re.compile(r"\bcomplement\b|bitwise not", re.I),
    "bitwise_explicit": re.compile(r"\bxor\b|bitwise|\bcomplement\b|\bbit-?wise\b|exclusive or", re.I),
    "bitwise_general": re.compile(
        r"\bbits?\b|popcount|hamming weight|bitmask|\bmask\b|\bshift|parity|power of (?:two|2)", re.I),
    "arith_prescribed": re.compile(r"divid\w* by 2|modulo 2|mod 2|%\s*2|n % 2|divide by 2", re.I),
    # "parallel arrays" and conversation "threads" are not concurrency; the
    # broad form (concurren|parallel|spawn|thread|async) matched 112 regen and
    # 11 library descriptions, none of which asks for concurrent execution
    "concurrency": re.compile(r"concurren|run in parallel|\bspawn|multi-?thread|worker pool|\basync\b", re.I),
    "match_mandate": re.compile(r"pattern matching|match expression", re.I),
}

# A value an `mt` can consume: an error union or a user sum type in the
# source, or a call the card says returns an error union.
MATCHABLE = re.compile(
    r"![$][a-z]|\bt=\$[a-z0-9]+\{\$|\.toint\(|\.tofloat\(|\bjson\.dec\(|\bfile\.read\("
    r"|\.frombytes\(|\bargs\.get\(")

SWEEP_SHAPES = {
    ("mut-flag-if", "cond-bind-if"): "mut_flag_if (statement-only if)",
    ("flag-soup", "cond-bool-combine"): "flag_soup (no &&/||)",
    ("hand-rolled-parser", "parse-json"): "hand_rolled_parser (no mt json.dec)",
}

PROBES = {
    "caret": "m=p;\ni=io:std.io;\nf=main():i64{\n  io.println(\"\\(6^3)\");\n  <0\n};\n",
    "tilde": "m=p;\ni=io:std.io;\nf=main():i64{\n  io.println(\"\\(~5)\");\n  <0\n};\n",
    "mt": ("m=p;\ni=io:std.io;\ni=s:std.str;\nf=main():i64{\n"
           "  let v=mt s.toint(\"12\") {$ok:n n;$err:e 0};\n  io.println(\"\\(v)\");\n  <0\n};\n"),
    "sc": "m=p;\nf=main():i64{\n  sc{let x=1};\n  <0\n};\n",
    "neq_and_and_mod": ("m=p;\ni=io:std.io;\nf=main():i64{\n"
                        "  let a=7;\n  let b=if(a!=3&&a%2==1){1}el if(a>9){2}el{3};\n"
                        "  io.println(\"\\(b)\");\n  <0\n};\n"),
}


def code_only(src):
    """Source with string-literal text blanked; interpolation code is kept."""
    out = []
    i, n = 0, len(src)
    instr = False
    depth = []          # paren depth of each open \( ... ) interpolation
    while i < n:
        ch = src[i]
        if instr:
            if ch == "\\" and i + 1 < n:
                if src[i + 1] == "(":
                    instr = False
                    depth.append(0)
                    out.append(" ")
                    i += 2
                    continue
                i += 2
                continue
            if ch == '"':
                instr = False
                out.append(" ")
            i += 1
            continue
        if ch == '"':
            instr = True
            i += 1
            continue
        if depth:
            if ch == "(":
                depth[-1] += 1
            elif ch == ")":
                if depth[-1] == 0:
                    depth.pop()
                    instr = True
                    out.append(" ")
                    i += 1
                    continue
                depth[-1] -= 1
        out.append(ch)
        i += 1
    return "".join(out)


def tilde(path):
    """Home-relative spelling for paths written into the report."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home) else path


def sha_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def first_line(code, rx):
    """The first hit with up to 50 characters of context on its own line."""
    m = rx.search(code)
    if not m:
        return None
    s = max(code.rfind("\n", 0, m.start()) + 1, m.start() - 50)
    e = code.find("\n", m.end())
    e = min(e if e >= 0 else len(code), m.end() + 50)
    return code[s:e].strip()


def features(src):
    code = code_only(src)
    f = {k: len(rx.findall(code)) for k, rx in CONSTRUCTS.items()}
    flat = re.sub(r"\s+", "", code)
    w = {k: bool(rx.search(flat if k == "nested_if_and" else code)) for k, rx in WORKAROUNDS.items()}
    w["any_page_prescribed"] = any(w[k] for k in PAGE_PRESCRIBED)
    return code, f, w


LEGACY = re.compile(r"Legacy source[^\n]*\n(.*?)\n\s*\nCurrent compiler diagnostics", re.S)


def legacy_input(prompt_path):
    """The legacy source a migrate_fix prompt hands the worker ('' if none)."""
    if not prompt_path or not os.path.exists(prompt_path):
        return ""
    m = LEGACY.search(open(prompt_path).read())
    return m.group(1) if m else ""


def demand(desc):
    return {k: bool(rx.search(desc or "")) for k, rx in DEMAND.items()}


def load_specs(corpus):
    specs = {}
    for f in sorted(glob.glob(os.path.join(corpus, "shards", "shard_*.jsonl"))):
        for line in open(f):
            t = json.loads(line)
            specs[t["task_id"]] = t
    return specs


def gen_outputs(corpus):
    out = {}
    for p in glob.glob(os.path.join(corpus, "work", "shard_*", "gen", "out_*.tk.done")):
        out[os.path.basename(p)[4:-8]] = p
    return out


def task_prompts(corpus):
    out = {}
    for p in glob.glob(os.path.join(corpus, "work", "shard_*", "prompts", "*.txt")):
        out[os.path.basename(p)[:-4]] = p
    return out


def provenance(corpus, records, worker_prompt):
    cards = collections.Counter(r["regen"].get("card_sha") for r in records.values())
    models = collections.Counter(r.get("model") for r in records.values())
    # every card the repo ever had, by the sha the harness stamps
    hist = []
    log = subprocess.run(["git", "-C", REPO, "log", "--format=%h %ad", "--date=short",
                          "--", "regen/syntax_card.md"], capture_output=True, text=True).stdout
    for line in log.split("\n"):
        if not line.strip():
            continue
        c, d = line.split()
        text = subprocess.run(["git", "-C", REPO, "show", f"{c}:regen/syntax_card.md"],
                              capture_output=True, text=True).stdout
        hist.append({"commit": c, "date": d,
                     "card_sha": hashlib.sha256(text.encode()).hexdigest()[:12],
                     "teaches": {
                         "caret_tilde_live": bool(re.search(r"Bitwise: `& \| \^ ~", text))
                         or "`^` and `~` are live" in text,
                         "mt_match": "mt " in text,
                         "sc_named": bool(re.search(r"\bsc\b", text)),
                         "keyword_list": "keywords are" in text.lower() or "14 keywords" in text,
                         "equality_eqeq": "Equality is `==`" in text or "`==`" in text}})
    # the per-task prompts the workers read
    prompt_hits = collections.Counter()
    n_prompts = 0
    # phrases only the pre-132.15 spec-reference.md used (task text such as
    # D-TST diagnostic fixtures mentions "keyword" legitimately, so match the
    # page's own wording, not the word)
    bad = re.compile(r"reserved `?\^|56 char|Keywords \(12\)|12 keywords|spec-reference"  # facts-exempt: 2026-11-23 retired wording matched, not asserted
                     r"|Character Set \(56|`expr\\?\|\{", re.I)
    for p in glob.glob(os.path.join(corpus, "work", "shard_*", "prompts", "*.txt")):
        n_prompts += 1
        t = open(p).read()
        for m in bad.finditer(t):
            prompt_hits[m.group(0).lower()] += 1
    readers = subprocess.run(["git", "-C", REPO, "grep", "-l", "spec-reference", "--", "*.py", "*.js", "*.sh"],
                             capture_output=True, text=True).stdout.split()
    wp = {"path": tilde(worker_prompt), "present": os.path.exists(worker_prompt)}
    if wp["present"]:
        t = open(worker_prompt).read()
        wp["sha256"] = hashlib.sha256(t.encode()).hexdigest()
        wp["reads_card"] = "syntax_card.md" in t
        wp["reads_spec_reference"] = "spec-reference" in t
        m = re.search(r"[^\n]*toke uses \\`=\\` for equality[^\n]*", t)
        wp["wrong_teaching"] = m.group(0).strip() if m else None
    return {"record_card_sha": dict(cards), "record_model": dict(models),
            "card_history": hist, "worker_prompt": wp,
            "task_prompts": {"files": n_prompts, "spec_terms": dict(prompt_hits)},
            "repo_files_reading_spec_reference": readers}


def library_provenance():
    ref = os.path.expanduser("~/tk/toke-test-programs/infra/toke_docs_lookup.py")
    if not os.path.exists(ref):
        return {"present": False}
    t = open(ref).read()
    kw = re.search(r'"keywords":\s*"([^"]*)"', t)
    op = re.search(r'"operators":\s*"([^"]*)"', t)
    return {"present": True, "path": "toke-test-programs/infra/toke_docs_lookup.py",
            "sha256": hashlib.sha256(t.encode()).hexdigest(),
            "keywords_line": kw.group(1) if kw else None,
            "operators_line": op.group(1) if op else None,
            "mentions_spec_reference": "spec-reference" in t}


def sweep_counts(corpus):
    out = {}
    for name in ("pattern_sweep.jsonl", "pattern_sweep.131.64.jsonl"):
        p = os.path.join(corpus, "audit", name)
        c = collections.defaultdict(set)
        for line in open(p):
            r = json.loads(line)
            for v in r["violations"]:
                k = SWEEP_SHAPES.get((v.get("rule"), v.get("pattern_id")))
                if k:
                    c[(r["source"], k)].add(r["task_id"])
            for o in r.get("other_lint", []):
                if (o if isinstance(o, str) else o.get("rule")) == "nested-if-ladder":
                    c[(r["source"], "nested_if_ladder (no el if)")].add(r["task_id"])
        out[name] = {f"{s}:{k}": len(v) for (s, k), v in sorted(c.items())}
    return out


def bucket_table(corpus, regen):
    """Merged-routing bucket (audit/pattern_sweep.merged.jsonl) of every banked
    record that still carries a page-prescribed shape in its LIVE source: does
    an existing wave already reach it?"""
    p = os.path.join(corpus, "audit", "pattern_sweep.merged.jsonl")
    if not os.path.exists(p):
        return None
    bucket = {}
    for line in open(p):
        r = json.loads(line)
        bucket[r["task_id"]] = r.get("bucket")
    out = collections.defaultdict(collections.Counter)
    for x in regen:
        if x["live_w"]["any_page_prescribed"]:
            out[x["tt"]][bucket.get(x["id"], "none")] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def probe(tkc):
    res = {}
    with tempfile.TemporaryDirectory() as d:
        for k, src in PROBES.items():
            p = os.path.join(d, k + ".tk")
            open(p, "w").write(src)
            r = subprocess.run([tkc, "--check", p], capture_output=True, text=True)
            res[k] = {"check_exit": r.returncode}
    return res


def rate(n, d):
    return round(n / d, 4) if d else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(REPO, "corpus", "regen_v04"))
    ap.add_argument("--tkc", required=True)
    ap.add_argument("--worker-prompt",
                    default=os.path.expanduser("~/tk/.claude/workflows/regen-shard-wave.js"))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    corpus = os.path.abspath(a.corpus)

    specs = load_specs(corpus)
    records = {}
    for p in glob.glob(os.path.join(corpus, "[AD]-*", "*.json")):
        r = json.load(open(p))
        records[r["task_id"]] = r
    gens = gen_outputs(corpus)
    prompts = task_prompts(corpus)
    lib = [json.loads(line) for line in open(os.path.join(corpus, "shards", "library_131.jsonl"))]

    rows = []
    for tid in sorted(records):
        r = records[tid]
        sp = specs.get(tid, {})
        cat = r["regen"]["category"]
        tt = r["regen"]["task_type"]
        live_code, lf, lw = features(r["tk_source"])
        g = gens.get(tid)
        gsrc = open(g).read() if g else ""
        gen_code, gf, gw = features(gsrc)
        legacy = legacy_input(prompts.get(tid))
        _, inf, inw = features(legacy)
        rows.append({"id": tid, "src": "regen", "cat": cat, "tt": tt,
                     "mig_src": re.sub(r"-[A-Z]-[A-Z]{3}-.*", "", tid) if tid.startswith("MIG") else None,
                     "inp": inf, "inp_w": inw, "inp_present": bool(legacy),
                     "demand": demand(sp.get("description", "")),
                     "err_out": "!" in (sp.get("output_type") or ""),
                     "gen_present": bool(g), "gen": gf, "gen_w": gw, "live": lf, "live_w": lw,
                     "gen_matchable": bool(MATCHABLE.search(gen_code)),
                     "live_matchable": bool(MATCHABLE.search(live_code)),
                     "_gen_code": gen_code, "_live_code": live_code})
    lib_drift = 0
    for t in lib:
        p = t["source_program"]
        src = open(p).read() if os.path.exists(p) else ""
        if src and sha_file(p) != t["source_sha256"]:
            lib_drift += 1
        code, f, w = features(src)
        rows.append({"id": t["task_id"], "src": "library", "cat": t["category"], "tt": t["task_type"],
                     "demand": demand(t.get("description", "")), "err_out": False,
                     "gen_present": False, "live": f, "live_w": w,
                     "live_matchable": bool(MATCHABLE.search(code)), "_live_code": code})

    regen = [x for x in rows if x["src"] == "regen"]
    library = [x for x in rows if x["src"] == "library"]

    def view_counts(sel, view):
        n = len(sel)
        c = {}
        for k in CONSTRUCTS:
            hit = [x for x in sel if x[view][k]]
            c[k] = {"records": len(hit), "rate": rate(len(hit), n),
                    "occurrences": sum(x[view][k] for x in sel)}
        for k in WKEYS:
            hit = [x for x in sel if x[view + "_w"][k]]
            c["w:" + k] = {"records": len(hit), "rate": rate(len(hit), n)}
        return {"n": n, "counts": c}

    def strata(sel, view, key):
        groups = collections.defaultdict(list)
        for x in sel:
            groups[x[key]].append(x)
        return {g: {"n": len(v), **{k: sum(1 for x in v if x[view][k]) for k in CONSTRUCTS},
                    **{"w:" + k: sum(1 for x in v if x[view + "_w"][k]) for k in WKEYS}}
                for g, v in sorted(groups.items())}

    def demand_table(sel, view):
        out = {}
        for d in DEMAND:
            sub = [x for x in sel if x["demand"][d]]
            if not sub:
                continue
            row = {"tasks": len(sub)}
            for k in CONSTRUCTS:
                row[k] = sum(1 for x in sub if x[view][k])
            for k in WKEYS:
                row["w:" + k] = sum(1 for x in sub if x[view + "_w"][k])
            if d.startswith("bitwise"):
                ops = re.compile(r"\^|~|<<|>>|(?<!&)&(?!&)|(?<!\|)\|(?!\|)")
                row["any_bit_operator"] = sum(1 for x in sub if ops.search(x["_" + view + "_code"]))
                pres = [x for x in sub if x["demand"]["arith_prescribed"]]
                row["arith_prescribed_by_task"] = len(pres)
            if d in ("xor_named", "complement_named") or d.startswith("bitwise"):
                ops = re.compile(r"\^|~|<<|>>|(?<!&)&(?!&)|(?<!\|)\|(?!\|)")
                row["any_bit_operator"] = sum(1 for x in sub if ops.search(x["_" + view + "_code"]))
                row["xor_helper_call"] = sum(1 for x in sub
                                             if re.search(r"(?<![a-z0-9.])xor\(", x["_" + view + "_code"]))
                row["neither"] = sum(1 for x in sub if not ops.search(x["_" + view + "_code"])
                                     and not re.search(r"(?<![a-z0-9.])xor\(", x["_" + view + "_code"]))
            out[d] = row
        # mt where something is there to match, and against an explicit mandate
        m = [x for x in sel if x[view + "_matchable"]]
        out["matchable_value_present"] = {"tasks": len(m), "mt": sum(1 for x in m if x[view]["mt"]),
                                          "mt_rate": rate(sum(1 for x in m if x[view]["mt"]), len(m))}
        mm = [x for x in m if x["demand"]["match_mandate"]]
        out["match_mandate_and_matchable"] = {"tasks": len(mm), "mt": sum(1 for x in mm if x[view]["mt"]),
                                              "mt_rate": rate(sum(1 for x in mm if x[view]["mt"]), len(mm))}
        e = [x for x in sel if x.get("err_out")]
        if e:
            out["error_union_return"] = {"tasks": len(e), "mt": sum(1 for x in e if x[view]["mt"]),
                                         "mt_rate": rate(sum(1 for x in e if x[view]["mt"]), len(e))}
        return out

    # stratified sample: per signal, up to SAMPLE_PER_CELL ids per category,
    # chosen by sha256(id) order so the pick is stable and not alphabetical.
    def sample(sel, view, sig, is_w, by="cat", per=SAMPLE_PER_CELL):
        cells = collections.defaultdict(list)
        for x in sel:
            hit = x[view + "_w"][sig] if is_w else x[view][sig]
            if hit:
                cells[x[by]].append(x)
        rx = WORKAROUNDS[sig] if is_w else CONSTRUCTS[sig]
        out = []
        for cell in sorted(cells):
            pick = sorted(cells[cell], key=lambda x: hashlib.sha256(x["id"].encode()).hexdigest())
            for x in pick[:per]:
                code = x["_" + view + "_code"]
                if sig == "nested_if_and":
                    code = re.sub(r"\s+", "", code)
                out.append({"id": x["id"], "category": x["cat"], "task_type": x["tt"],
                            "line": first_line(code, rx)})
        return out

    # migrate_fix: did the shape come in with the legacy source (inherited),
    # appear only in the output (introduced), or get removed?
    def inheritance(sel):
        out = {}
        mig = [x for x in sel if x["tt"] == "migrate_fix"]
        for k in list(CONSTRUCTS) + ["w:" + w for w in WKEYS]:
            isw = k.startswith("w:")
            kk = k[2:] if isw else k
            def has(x, v):
                return bool(x[v + "_w"][kk]) if isw else bool(x[v][kk])
            out[k] = {"input": sum(1 for x in mig if has(x, "inp")),
                      "output": sum(1 for x in mig if has(x, "gen")),
                      "inherited": sum(1 for x in mig if has(x, "inp") and has(x, "gen")),
                      "introduced": sum(1 for x in mig if not has(x, "inp") and has(x, "gen")),
                      "removed": sum(1 for x in mig if has(x, "inp") and not has(x, "gen"))}
        by_src = collections.Counter(x["mig_src"] for x in mig)
        # same task families: native (generated from the description) records
        # whose base id (e.g. A-ARR-0045) is also the base of a migrate_fix
        base_rx = re.compile(r"([AD]-[A-Z]{3}-\d{4})")
        def base(tid):
            m = base_rx.search(tid)
            return m.group(1) if m else None
        mig_bases = {base(x["id"]) for x in mig} - {None}
        native = [x for x in sel if x["tt"] != "migrate_fix" and base(x["id"]) in mig_bases]
        matched = {"bases": len(mig_bases), "native_records": len(native), "migrate_records": len(mig)}
        for k in ["mt"] + ["w:" + w for w in PAGE_PRESCRIBED] + ["w:any_page_prescribed"]:
            isw = k.startswith("w:")
            kk = k[2:] if isw else k
            def has(x):
                return bool(x["gen_w"][kk]) if isw else bool(x["gen"][kk])
            n_nat = sum(1 for x in native if has(x))
            n_mig = sum(1 for x in mig if has(x))
            matched[k] = {"native": n_nat, "native_rate": rate(n_nat, len(native)),
                          "migrate": n_mig, "migrate_rate": rate(n_mig, len(mig))}
        per_cat = collections.defaultdict(lambda: {"migrate_fix": 0, "any_page_prescribed": 0,
                                                   "native": 0, "native_any_page_prescribed": 0})
        for x in sel:
            c = per_cat[x["cat"]]
            if x["tt"] == "migrate_fix":
                c["migrate_fix"] += 1
                c["any_page_prescribed"] += x["gen_w"]["any_page_prescribed"]
            else:
                c["native"] += 1
                c["native_any_page_prescribed"] += x["gen_w"]["any_page_prescribed"]
        return {"migrate_fix_records": len(mig), "by_source_prefix": dict(by_src),
                "by_category": {k: per_cat[k] for k in sorted(per_cat)},
                "legacy_input_found": sum(1 for x in mig if x["inp_present"]), "shapes": out,
                "same_family_reference": matched}

    samples = {}
    for k in CONSTRUCTS:
        samples["gen:" + k] = sample(regen, "gen", k, False)
        samples["library:" + k] = sample(library, "live", k, False)
    for k in WORKAROUNDS:
        samples["gen:w:" + k] = sample(regen, "gen", k, True)
        samples["gen:w:" + k + ":by_task_type"] = sample(regen, "gen", k, True, by="tt", per=5)
        samples["library:w:" + k] = sample(library, "live", k, True)
    # demand-unmet: bitwise_explicit tasks whose generated code uses no bit operator
    ops = re.compile(r"\^|~|<<|>>|(?<!&)&(?!&)|(?<!\|)\|(?!\|)")
    unmet = [x for x in regen if x["demand"]["bitwise_explicit"] and not ops.search(x["_gen_code"])]
    samples["gen:bitwise_explicit_without_operator"] = [
        {"id": x["id"], "category": x["cat"], "task_type": x["tt"],
         "description": specs[x["id"]]["description"][:200]}
        for x in sorted(unmet, key=lambda x: hashlib.sha256(x["id"].encode()).hexdigest())[:12]]

    report = {
        "story": "131.62", "step": "a", "batch": "Z234", "date": "2026-11-23",
        "inputs": {
            "regen_records": len(regen), "gen_outputs_found": sum(1 for x in regen if x["gen_present"]),
            "library_programs": len(library), "library_sha_drift_since_131.13": lib_drift,
            "tkc": tilde(a.tkc), "tkc_sha256": sha_file(a.tkc),
            "tkc_version": subprocess.run([a.tkc, "--version"], capture_output=True, text=True).stdout.strip()},
        "provenance": provenance(corpus, records, a.worker_prompt),
        "library_provenance": library_provenance(),
        "probes": probe(a.tkc),
        "views": {"regen_gen": view_counts(regen, "gen"), "regen_live": view_counts(regen, "live"),
                  "library": view_counts(library, "live")},
        "by_category_gen": strata(regen, "gen", "cat"),
        "by_task_type_gen": strata(regen, "gen", "tt"),
        "by_task_type_live": strata(regen, "live", "tt"),
        "live_page_shapes_by_bucket": bucket_table(corpus, regen),
        "by_category_library": strata(library, "live", "cat"),
        "demand_gen": demand_table(regen, "gen"),
        "demand_live": demand_table(regen, "live"),
        "demand_library": demand_table(library, "live"),
        "migrate_fix_inheritance": inheritance(regen),
        "sweep_shapes": sweep_counts(corpus),
        "samples": samples,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print(json.dumps({"views": report["views"], "demand_gen": report["demand_gen"],
                      "demand_library": report["demand_library"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
