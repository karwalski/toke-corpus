#!/usr/bin/env python3
"""A-ERR test-lock audit (precondition for story 131.16).

Question this answers, per A-ERR base task: **does the banked a_tests test
case actually lock the error path, or would a program that merely RETURNS the
harness's err marker as a `str` satisfy it?**  131.42 found 429 records that
"pass" by printing `{'err': 'NotFound'}`; regenerating A-ERR against a test
that cannot tell that apart from a real `T!Err` would relearn the gaming.

Method — every verdict is an execution, never a reading:

  ORACLE   for each case k, a module declaring ONLY the spec's target
           signature whose body returns exactly that case's expected value
           (`<$lookuperr{$notfound:"x"}` for an err case, the rendered ok
           literal otherwise).  The case's test lock must ACCEPT it.
  MARKER-A the same signature, return type drifted to `str`, body returns the
           marker text (`<"{'err': 'NotFound'}"`).  The lock must REJECT it.
  MARKER-B the correct union target, plus a trailing same-arity wrapper that
           returns the marker `str` — the shape `driver.find_target` actually
           calls (131.42's `wrapper_returns_str`, 40 records).  Must REJECT.

Both marker shapes are run over the base's WHOLE case list; the lock passes
only if every oracle case passes AND both marker programs fail.

`python_ref` is re-executed over every case first (the 129.7 ground truth for
the expected VALUES); this harness adds the ground truth for the expected
FORM.

Banks nothing.  Rows -> corpus/regen_v04/audit/err_test_lock.jsonl.
Compiler is pinned per 131.39 (~/tk/toke/tkc is relinked by any `make`).
"""
from __future__ import annotations

import argparse, json, glob, os, re, sys, tempfile
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver as drv                                            # noqa: E402
import audit as aud                                             # noqa: E402
import validate                                                 # noqa: E402
import tkc_pin                                                  # noqa: E402
import verify_a_tests as vat                                    # noqa: E402

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
ADIR = os.path.join(CORPUS, "audit", "a_tests")
OUT = os.path.join(CORPUS, "audit", "err_test_lock.jsonl")
_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


# --------------------------------------------------------------------------
# module synthesis
# --------------------------------------------------------------------------
def signature(spec):
    """(name, [param types], ok type, err type name or None, [(tag, type)])."""
    desc = spec.get("description", "") or ""
    m = drv._FSTART.search(desc)
    name = m.group(1) if m else "target"
    types = [vat.tk_type(t) for t in vat.input_types(spec)]
    ret = drv._norm_type(spec.get("output_type_v03") or spec.get("output_type") or "i64")
    if "!" not in ret:
        return name, types, ret, None, []
    okt, errt = ret.split("!", 1)
    ename = re.sub(r"[^a-z0-9]", "", errt.lower())
    variants = []
    for dm in vat._TDECL.finditer(desc):
        if dm.group(1).lower() == ename:
            for part in dm.group(2).split(";"):
                if ":" in part:
                    tag, ft = part.split(":", 1)
                    variants.append((re.sub(r"[^a-z0-9]", "", tag.lower()), vat.tk_type(ft.strip())))
    # 131.16: some descriptions spell the declaration `T=MathErr{...}` (no `t=$`);
    # the variant set is then taken from the description's Uppercase form, and
    # failing that from the markers the cases themselves use.
    if not variants:
        for dm in re.finditer(r"\b[Tt]=\$?([A-Za-z0-9]+)\{([^}]*)\}", desc):
            if re.sub(r"[^a-z0-9]", "", dm.group(1).lower()) == ename:
                for part in dm.group(2).split(";"):
                    if ":" in part:
                        tag, ft = part.split(":", 1)
                        variants.append((re.sub(r"[^a-z0-9]", "", tag.lower()),
                                         vat.tk_type(ft.strip().lstrip("$") or "str")))
    return name, types, vat.tk_type(okt), ename, variants


def _payload(t):
    return vat._default_expr(t) if t != "str" else '"e"'


def _err_expr(ename, variants, tag):
    ft = dict(variants).get(tag, "str")
    return f"<${ename}{{${tag}:{_payload(ft)}}}"


def _ok_expr(v, okt):
    return "<" + drv.lit(v, okt)


def _header(ename, variants):
    h = "m=harness;\ni=io:std.io;\ni=s:std.str;\n"
    if ename:
        h += "t=$" + ename + "{" + ";".join(f"${t}:{ft}" for t, ft in variants) + "};\n"
    return h


def oracle_module(spec, tc):
    """Module whose target returns exactly `tc`'s expected value."""
    name, types, okt, ename, variants = signature(spec)
    params = ";".join(f"p{i}:{t}" for i, t in enumerate(types))
    ret = f"{okt}!${ename}" if ename else okt
    en = drv.err_name(tc.get("expected"))
    if en is not None:
        if not ename:
            return None, "err expectation but the spec's return type is not T!Err"
        tag = drv.render_err(en)[4:]
        if tag not in dict(variants):
            return None, f"err marker {en!r} is not a declared variant ({', '.join(t for t, _ in variants)})"
        body = _err_expr(ename, variants, tag)
    else:
        try:
            body = _ok_expr(tc.get("expected"), okt)
        except (ValueError, TypeError) as e:
            return None, f"cannot render expected {tc.get('expected')!r} as {okt}: {e}"
    return _header(ename, variants) + f"f={name}({params}):{ret}{{{body}}};\n", None


def marker_text(tcs):
    """The marker string a gamed record prints, as 131.42 found it."""
    for tc in tcs:
        en = drv.err_name(tc.get("expected"))
        if en:
            key = "err" if "err" in (tc["expected"] or {}) else "error"
            return "{'" + key + "': '" + en + "'}"
    return None


def marker_module(spec, tcs, shape):
    """shape 'A': the target itself drifts to `str` and returns the marker.
       shape 'B': a correct union target plus a trailing same-arity `str`
                  wrapper — what find_target actually calls (131.42)."""
    name, types, okt, ename, variants = signature(spec)
    if not ename or not variants:
        return None, "not a T!Err spec"
    txt = marker_text(tcs)
    if txt is None:
        return None, "no err case to fake"
    params = ";".join(f"p{i}:{t}" for i, t in enumerate(types))
    args = ";".join(f"p{i}" for i in range(len(types)))
    esc = txt.replace("\\", "\\\\").replace('"', '\\"')
    if shape == "A":
        body = f'f={name}({params}):str{{<"{esc}"}};\n'
    else:
        # the wrapper really calls the union target (and matches on it, so the
        # module compiles under Profile-1 — no underscore identifiers) and then
        # returns the marker anyway: 131.42's `wrapper_returns_str`
        first = variants[0][0]
        body = (f"f={name}real({params}):{okt}!${ename}{{{_err_expr(ename, variants, first)}}};\n"
                f"f={name}({params}):str{{let u=mt {name}real({args}){{$ok:v 1;$err:q 0}};"
                f'if(u>1){{<"marker"}}el{{<"{esc}"}}}};\n')
    return _header(ename, variants) + body, None


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def run_lock(spec, src, tcs, tmpdir, tag):
    """Render tcs through the driver against src, build, run, compare."""
    s2 = dict(spec)
    s2["test_cases"] = tcs
    dsrc, err = drv.append_main(s2, src)
    if err:
        return {"ok": False, "driver_fail": err}
    g = aud._exec_tests(dsrc, drv.expected_lines(s2), tmpdir, tag)
    return {"ok": bool(g.get("tests_new")), "driver_fail": None,
            "reason": g.get("reason"), "build": g.get("build")}


def ref_check(doc, spec):
    """129.7 ground truth for the expected VALUES: re-run python_ref."""
    return vat.verify(doc.get("base"), doc, spec, probe=False)


def audit_base(base, spec, doc, tmpdir):
    tcs = doc.get("test_cases") or []
    row = {"base": base, "n_cases": len(tcs), "provenance": doc.get("provenance"),
           "spec_task_id": spec["task_id"],
           "spec_return": drv._norm_type(spec.get("output_type_v03") or spec.get("output_type") or ""),
           "ref_errors": ref_check(doc, spec)}
    row["n_err_cases"] = sum(1 for tc in tcs if drv.err_name(tc.get("expected")) is not None)
    if "!" not in row["spec_return"]:
        row["verdict"] = "total_function" if row["n_err_cases"] == 0 else "err_case_on_total_spec"
        row["note"] = "spec declares no T!Err return; no error path to lock"
        return row
    # 1. oracle, per case
    oracle = []
    for k, tc in enumerate(tcs):
        src, err = oracle_module(spec, tc)
        if err:
            oracle.append({"case": k, "ok": False, "error": err})
            continue
        r = run_lock(spec, src, [tc], tmpdir, f"{base}.o{k}")
        oracle.append({"case": k, "ok": r["ok"],
                       "error": r.get("driver_fail") or r.get("reason")})
    row["oracle"] = oracle
    row["oracle_all_pass"] = all(o["ok"] for o in oracle) and bool(oracle)
    # 2. marker shapes
    markers = {}
    for shape in ("A", "B"):
        src, err = marker_module(spec, tcs, shape)
        if err:
            markers[shape] = {"applicable": False, "error": err}
            continue
        r = run_lock(spec, src, tcs, tmpdir, f"{base}.m{shape}")
        markers[shape] = {"applicable": True, "accepted": r["ok"],
                          "rejected": not r["ok"],
                          "how": r.get("driver_fail") or r.get("reason")}
    row["markers"] = markers
    applicable = [m for m in markers.values() if m.get("applicable")]
    row["marker_rejected"] = bool(applicable) and all(m["rejected"] for m in applicable)
    if row["n_err_cases"] == 0:
        row["verdict"] = "no_err_case"
    elif not row["oracle_all_pass"]:
        row["verdict"] = "oracle_fail"
    elif not row["marker_rejected"]:
        row["verdict"] = "marker_accepted"
    elif row["ref_errors"]:
        row["verdict"] = "ref_fail"
    else:
        row["verdict"] = "sound"
    return row


def load_specs(category):
    specs = {}
    for sh in sorted(glob.glob(os.path.join(CORPUS, "shards", "shard_*.jsonl"))):
        for line in open(sh):
            s = json.loads(line)
            if s.get("category") == category and s.get("task_type") == "single_function":
                m = _BASE.match(s["task_id"])
                if m and m.group(1) not in specs:
                    specs[m.group(1)] = s
    return specs


def _say(row):
    print(f"{row['base']:14s} {row['verdict']:22s} "
          f"err_cases={row['n_err_cases']} "
          f"oracle={row.get('oracle_all_pass')} "
          f"marker_rejected={row.get('marker_rejected')}", flush=True)


def _one(arg):
    """Pool worker: TOKE_TKC_PIN is inherited, so every worker execs the same
    pinned binary the parent copied (131.39)."""
    b, spec = arg
    doc = json.load(open(os.path.join(ADIR, b + ".json")))
    with tempfile.TemporaryDirectory(prefix="errlock_") as td:
        return audit_base(b, spec, doc, td)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--category", default="A-ERR")
    ap.add_argument("--bases", nargs="*", help="limit to these bases")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    specs = load_specs(a.category)
    bases = a.bases or sorted(b for b in specs
                              if os.path.exists(os.path.join(ADIR, b + ".json")))
    rows = []
    with tkc_pin.pin() as p:
        p.install(validate, aud, drv)
        aud.TKC = p.path
        stamp = p.stamp()
        if a.jobs > 1:
            with ProcessPoolExecutor(max_workers=a.jobs) as ex:
                for row in ex.map(_one, [(b, specs[b]) for b in bases]):
                    row["tkc_bin_sha"] = stamp["tkc_bin_sha"]
                    rows.append(row)
                    _say(row)
        else:
            with tempfile.TemporaryDirectory(prefix="errlock_") as td:
                for b in bases:
                    doc = json.load(open(os.path.join(ADIR, b + ".json")))
                    row = audit_base(b, specs[b], doc, td)
                    row["tkc_bin_sha"] = stamp["tkc_bin_sha"]
                    rows.append(row)
                    _say(row)
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    import collections
    print("\n" + json.dumps(collections.Counter(r["verdict"] for r in rows), indent=1))
    print("tkc:", stamp["tkc_bin_sha"], stamp["tkc_version"], "toke_head", stamp["toke_head"])


if __name__ == "__main__":
    main()
