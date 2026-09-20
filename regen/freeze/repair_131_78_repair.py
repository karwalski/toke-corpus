import json, os, re, sys, glob, subprocess, tempfile
sys.path.insert(0, '/Users/matthew.watt/tk/tmpwork/131_78')
sys.path.insert(0, '/Users/matthew.watt/tk/toke-corpus/regen')
import repair as R
from repair import (repair, str_alias, add_str_import, METHOD, CHARCODE, MODCALL, field_of, NEW, OLD, CORP, lower_struct_fields, E4025_RE)
import driver as drv
from assemble import assemble
from validate import render_expected

W = '/Users/matthew.watt/tk/tmpwork/131_78'
rows = json.load(open(W + '/run78.json'))
SPEC = {}
for p in glob.glob(CORP + '/corpus/regen_v04/work/*/specs/*.json'):
    SPEC.setdefault(os.path.basename(p)[:-5], p)

def compile_build(binary, src, d, tag, build=True):
    p = os.path.join(d, tag + '.tk'); open(p, 'w').write(src)
    out = os.path.join(d, tag + '.bin')
    argv = [binary, '--diag-json', p] + (['-o', out] if build else ['--emit-llvm'])
    r = subprocess.run(argv, capture_output=True, text=True, timeout=180, cwd=d)
    ds = []
    for ln in (r.stdout + r.stderr).splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try: ds.append(json.loads(ln))
            except Exception: pass
    return r.returncode, [x for x in ds if x.get('severity') == 'error'], out

def want_lines(spec):
    tcs = spec.get('test_cases') or []
    if spec.get('task_type') == 'single_function':
        return drv.expected_lines(spec)
    w = []
    for tc in tcs: w.extend(render_expected(tc.get('expected')).split('\n'))
    return w

def evaluate(binary, src, spec, d, tag):
    """(status, stdout, tests) — tests is None when there is no oracle."""
    want = want_lines(spec) if spec else []
    if 'f=main(' in src:
        rc, errs, out = compile_build(binary, src, d, tag)
        if rc != 0 or not os.path.exists(out):
            return 'BUILD_ERR:' + ','.join(sorted({e.get('error_code','?') for e in errs}) or ['link']), '', (False if want else None)
        try:
            r = subprocess.run([out], capture_output=True, text=True, errors='replace', timeout=20, cwd=d)
        except subprocess.TimeoutExpired:
            return 'TIMEOUT', '', (False if want else None)
        got = r.stdout.splitlines()
        t = None
        if want:
            t = len(got) == len(want) and r.returncode == 0 and all(
                g.strip() == w.strip() for g, w in zip(got, want))
        return 'RAN', r.stdout, t
    if not spec or not spec.get('test_cases'):
        rc, errs, out = compile_build(binary, src, d, tag, build=False)
        return ('CHECK_OK' if rc == 0 else 'CHECK_ERR:' + ','.join(sorted({e.get('error_code','?') for e in errs}))), '', None
    asm = assemble(spec, src) if spec.get('task_type') == 'single_function' else src
    dsrc, err = drv.append_main(spec, asm)
    if err:
        rc, errs, out = compile_build(binary, src, d, tag, build=False)
        return ('DRV_NA/' + ('CHECK_OK' if rc == 0 else 'CHECK_ERR:' + ','.join(sorted({e.get('error_code','?') for e in errs})))), '', None
    rc, errs, out = compile_build(binary, dsrc, d, tag + '_drv')
    if rc != 0 or not os.path.exists(out):
        return 'DRV_BUILD_ERR:' + ','.join(sorted({e.get('error_code','?') for e in errs}) or ['link']), '', False
    try:
        r = subprocess.run([out], capture_output=True, text=True, errors='replace', timeout=20, cwd=d)
    except subprocess.TimeoutExpired:
        return 'DRV_TIMEOUT', '', False
    got = r.stdout.splitlines()
    t = len(got) == len(want) and r.returncode == 0 and all(g.strip() == w.strip() for g, w in zip(got, want))
    return 'DRV_RAN', r.stdout, t

def variant(src, mode):
    """mode 'call'  : upper/lower/trim -> (), bytes -> s.bytes, code family -> s.charcode
       mode 'charcode': every offending field -> s.charcode(recv;0)"""
    alias = [str_alias(src)]
    fixed, notes = src, []
    for _ in range(12):
        with tempfile.TemporaryDirectory() as d:
            rc, errs, _o = compile_build(NEW, fixed, d, 'chk', build=False)
        if rc == 0: break
        prog = False
        for e in errs:                                    # E4025 legacy-case decl
            mm = E4025_RE.search(e.get('message') or '')
            if mm:
                fixed, k = lower_struct_fields(fixed, mm.group(1), mm.group(2))
                if k: notes.append([mm.group(1) + '.' + mm.group(2), k, 'lowercase-struct-decl']); prog = True
        fields = [f for f in (field_of(e) for e in errs) if f]
        todo = [f for f in fields if f in METHOD | CHARCODE | MODCALL]
        if not todo and not prog: break
        for f in dict.fromkeys(todo):
            if mode == 'charcode' and f in METHOD:
                R.METHOD.discard(f); R.CHARCODE.add(f)
                fixed, n = repair(fixed, f, alias)
                R.CHARCODE.discard(f); R.METHOD.add(f)
            else:
                fixed, n = repair(fixed, f, alias)
            if n: notes.append([f, n, mode if f in METHOD and mode == 'charcode' else 'call']); prog = True
        if not prog: break
    if alias[0] and not str_alias(fixed) and re.search(r'(^|[^a-z0-9.])' + alias[0] + r'\.(charcode|bytes)\(', fixed):
        fixed = add_str_import(fixed, alias[0])
    return fixed, notes

out = []
for i, r in enumerate(rows):
    spec = json.load(open(SPEC[r['tid']])) if r['tid'] in SPEC else None
    src = r['orig']
    v1, n1 = variant(src, 'call')
    with tempfile.TemporaryDirectory() as d:
        s_old, o_old, t_old = evaluate(OLD, src, spec, d, 'orig')
        s1, o1, t1 = evaluate(NEW, v1, spec, d, 'v1')
    pick, notes, s_new, o_new, t_new, mode = v1, n1, s1, o1, t1, 'call'
    v2 = n2 = None
    if t1 is False or s1.startswith('BUILD_ERR') or s1.startswith('CHECK_ERR') or s1.startswith('DRV_BUILD_ERR'):
        v2, n2 = variant(src, 'charcode')
        if v2 != v1:
            with tempfile.TemporaryDirectory() as d:
                s2, o2, t2 = evaluate(NEW, v2, spec, d, 'v2')
            if (t1 is not True) and (t2 is True or (t1 is False and t2 is None and not s2.startswith(('BUILD','CHECK_ERR','DRV_BUILD')))):
                pick, notes, s_new, o_new, t_new, mode = v2, n2, s2, o2, t2, 'charcode'
    out.append(dict(r, spec_tt=(spec or {}).get('task_type'),
                    want=want_lines(spec) if spec else [],
                    desc=(spec or {}).get('description', '')[:300],
                    v1=v1, v2=v2, pick=pick, notes=notes, mode=mode,
                    st_old=s_old, so_old=o_old, tests_old=t_old,
                    st_new=s_new, so_new=o_new, tests_new=t_new,
                    st_v1=s1, so_v1=o1, tests_v1=t1))
    if i % 10 == 0: print(i, flush=True)
json.dump(out, open(W + '/run78c.json', 'w'), indent=1)
print('done', len(out))
