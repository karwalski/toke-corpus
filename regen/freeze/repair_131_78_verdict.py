import json, os, re, subprocess, tempfile, hashlib, collections, sys
sys.path.insert(0, '/Users/matthew.watt/tk/toke-corpus/regen')
NEW = '/Users/matthew.watt/tk/tmpwork/131_77/new/toke'
rows = json.load(open('/Users/matthew.watt/tk/tmpwork/131_78/run78c.json'))

def check(src):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'x.tk'); open(p, 'w').write(src)
        r = subprocess.run([NEW, '--emit-llvm', '--diag-json', p], capture_output=True, text=True, cwd=d)
        codes = sorted(set(re.findall(r'"error_code"\s*:\s*"([A-Z0-9]+)"', r.stdout + r.stderr)))
        return r.returncode, codes

ran = lambda s: s in ('RAN', 'DRV_RAN')
out = []
for r in rows:
    rc, codes = check(r['pick'])
    clean = rc == 0
    if ran(r['st_old']) and ran(r['st_new']):
        oc = 'unchanged' if r['so_old'] == r['so_new'] else 'changed'
    elif ran(r['st_new']):
        oc = 'now_runs'
    elif clean:
        oc = 'not_executable_under_either'
    else:
        oc = 'still_rejected'
    if clean and oc in ('unchanged', 'changed', 'not_executable_under_either', 'now_runs'):
        status, reason = 'rewritten', None
    else:
        status = 'flagged'
        reason = 'no valid repair: ' + ','.join(codes or ['?']) + ' — ' + \
                 '; '.join(sorted({(s[1] or '')[:80] for s in r['structural']}))
    out.append(dict(r, check_rc=rc, check_codes=codes, clean=clean,
                    output_check=oc, status=status, reason=reason,
                    behaviour='preserved' if oc in ('unchanged', 'not_executable_under_either') else 'repaired',
                    diff_check='identical' if oc == 'unchanged' else oc,
                    prev_source_sha256=hashlib.sha256(r['orig'].encode()).hexdigest(),
                    fixed=r['pick']))
json.dump(out, open('/Users/matthew.watt/tk/tmpwork/131_78/verdict78.json', 'w'), indent=1)
c = collections.Counter((r['status'], r['output_check']) for r in out)
for k, v in c.most_common(): print(v, k)
print('\nvalidated 10:')
for r in out:
    if r['validated']:
        print(' ', r['id'], r['status'], r['output_check'], 'tests', r['tests_new'], r['mode'])
print('\nflagged:')
for r in out:
    if r['status'] == 'flagged': print(' ', r['id'], r['reason'][:110])
