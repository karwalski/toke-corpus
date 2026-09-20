#!/usr/bin/env python3
"""131.78 — bank the struct-layout (bare-property stdlib spelling) repairs.

Follows the 131 rewrite-wave conventions (bank_pattern.bank_one):
  archive original -> corpus/regen_v04/audit/replaced/131/<task_id>.tk (first touch only)
  record           -> tk_source replaced, regen.{source_sha256,min_bytes,proxy_tokens,
                      max_depth,lint_pattern_violations} recomputed, regen.rewrite131 stamped
  manifest         -> re-stamped (131.35: never replace without re-stamping)
  ledger           -> corpus/regen_v04/ledger/rewrite_131.jsonl, one line per record
"""
import json, os, sys, hashlib, time, tempfile, collections

sys.path.insert(0, '/Users/matthew.watt/tk/toke-corpus/regen')
import metrics, manifest_tool

CORP = '/Users/matthew.watt/tk/toke-corpus'
C = CORP + '/corpus/regen_v04'
REPLACED = C + '/audit/replaced/131'
LEDGER = C + '/ledger/rewrite_131.jsonl'
MANIFEST = C + '/MANIFEST.jsonl'
NEWBIN = '/Users/matthew.watt/tk/tmpwork/131_77/new/toke'
W = '/Users/matthew.watt/tk/tmpwork/131_78'

WAVE, STORY = 'struct-layout', '131.78'
OLD_SHA = '64a4336969dd14e73cd17b71ca04b11c1539f14a'
NEW_SHA = 'eb9c901762aca2157f1db6431a9af33c0f3f16fa'

sha_text = lambda s: hashlib.sha256(s.encode()).hexdigest()
sha_file = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()


def main(bank=False):
    rows = json.load(open(W + '/verdict78.json'))
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    binsha = sha_file(NEWBIN)
    os.makedirs(REPLACED, exist_ok=True)
    man = manifest_tool.Manifest(MANIFEST) if bank else None
    led = []
    for r in rows:
        tid, path = r['tid'], r['rec']
        rec = json.load(open(path))
        prev_rec_sha = sha_file(path)
        rg = rec.setdefault('regen', {})
        mb_before = rg.get('min_bytes')
        stamp = {
            'wave': WAVE, 'story': STORY, 'ts': now,
            'tkc_bin_sha': binsha, 'compiler_old': OLD_SHA, 'compiler_new': NEW_SHA,
            'defect': ('bare-property spelling of a stdlib call (E4034), or a legacy-case '
                       'struct field declaration (E4025) — resolved against a layout the '
                       'compiler had not established and read raw bytes before eb9c901'),
            'bad_spellings': r['bad'], 'repairs': r['notes'], 'repair_mode': r['mode'],
            'prev_sha256': r['prev_source_sha256'],
            'output_check': r['output_check'], 'behaviour': r['behaviour'],
            'diff_check': r['diff_check'],
            'output_before': r['so_old'], 'output_after': r['so_new'],
            'tests_after': r['tests_new'], 'status': r['status'], 'attempts': 1,
        }
        if r['status'] == 'rewritten':
            arch = os.path.join(REPLACED, tid + '.tk')
            if bank and not os.path.exists(arch):
                with open(arch, 'w') as f:
                    f.write(rec['tk_source'])
            rec['tk_source'] = r['fixed']
            rg['source_sha256'] = sha_text(r['fixed'])
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, tid + '.tk')
                open(p, 'w').write(r['fixed'])
                m = metrics.analyse(p, src=r['fixed'], tkc=NEWBIN) or {}
            for k in ('min_bytes', 'proxy_tokens', 'max_depth', 'lint_pattern_violations'):
                if m.get(k) is not None:
                    rg[k] = m[k]
            stamp['min_bytes_before'] = mb_before
            stamp['min_bytes_after'] = m.get('min_bytes')
            stamp['archived'] = 'audit/replaced/131/%s.tk' % tid
        else:
            stamp['needs_regen'] = True
            stamp['reason'] = r['reason']
        rg['rewrite131'] = stamp
        if bank:
            with open(path, 'w') as f:
                json.dump(rec, f)
            man.stamp(tid, path, save=False)
        led.append({'task_id': tid, 'wave': WAVE, 'story': STORY, 'status': r['status'],
                    'reason': r['reason'] or ('repaired:' + ','.join(
                        sorted({n[0] for n in r['notes']})) if r['notes'] else 'repaired'),
                    'attempts': 1, 'prev_sha256': prev_rec_sha,
                    'new_sha256': sha_file(path) if bank else None, 'ts': now,
                    'tkc_bin_sha': binsha, 'bad_spellings': r['bad'],
                    'repairs': r['notes'], 'output_check': r['output_check'],
                    'behaviour': r['behaviour'], 'needs_regen': r['status'] == 'flagged'})
    if bank:
        man.save()
        with open(LEDGER, 'a') as f:
            for e in led:
                f.write(json.dumps(e) + '\n')
    json.dump(led, open(W + '/ledger78.json', 'w'), indent=1)
    print(collections.Counter((e['status'], e['output_check']) for e in led))
    print('BANKED' if bank else 'DRY RUN', len(led))


if __name__ == '__main__':
    main(bank='--bank' in sys.argv)
