import json, os, hashlib, time, sys
CORP = '/Users/matthew.watt/tk/toke-corpus'
REPLACED = CORP + '/corpus/regen_v04/audit/replaced/131'
LEDGER = CORP + '/corpus/regen_v04/ledger/rewrite_131.jsonl'
NEWBIN = '/Users/matthew.watt/tk/tmpwork/131_77/new/toke'
TID = 'MUT-vari-019701-8770d29d'
PATHS = ['corpus/archive_phase1/phase2_combined/MUT-variable_rename/%s.json' % TID,
         'corpus/phase2_deduplicated/MUT-variable_rename/%s.json' % TID]
sha_text = lambda s: hashlib.sha256(s.encode()).hexdigest()
sha_file = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()
now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
binsha = sha_file(NEWBIN)
bank = '--bank' in sys.argv
led = []
for rel in PATHS:
    p = os.path.join(CORP, rel)
    rec = json.load(open(p))
    key = 'tk_source' if 'tk_source' in rec else 'toke_source'
    src = rec[key]
    assert 'arr.lenr' in src, rel
    new = src.replace('arr.lenr', 'arr.len')
    prev = sha_file(p)
    arch = os.path.join(REPLACED, TID + '.tk')
    if bank:
        os.makedirs(REPLACED, exist_ok=True)
        if not os.path.exists(arch):
            open(arch, 'w').write(src)
        rec[key] = new
        rec.setdefault('regen', {})['rewrite131'] = {
            'wave': 'struct-layout', 'story': '131.78', 'ts': now, 'tkc_bin_sha': binsha,
            'compiler_old': '64a4336969dd14e73cd17b71ca04b11c1539f14a',
            'compiler_new': 'eb9c901762aca2157f1db6431a9af33c0f3f16fa',
            'defect': ("the variable_rename mutation renamed the PROPERTY access as well as "
                       "the binding (`arr.len` -> `arr.lenr`); the old compiler resolved the "
                       "bogus field against an unestablished layout, the fixed one rejects it (E4034)"),
            'bad_spellings': ['lenr'], 'repairs': [['lenr', 1, 'restore-property']],
            'repair_mode': 'restore-property', 'prev_sha256': sha_text(src),
            'output_check': 'not_executable_under_either', 'behaviour': 'preserved',
            'diff_check': 'not_executable_under_either', 'status': 'rewritten',
            'archived': 'corpus/regen_v04/audit/replaced/131/%s.tk' % TID, 'attempts': 1,
        }
        with open(p, 'w') as f:
            json.dump(rec, f)
    led.append({'task_id': TID, 'wave': 'struct-layout', 'story': '131.78',
                'status': 'rewritten', 'reason': 'repaired:lenr (mutation renamed a property access)',
                'attempts': 1, 'prev_sha256': prev, 'new_sha256': sha_file(p) if bank else None,
                'ts': now, 'tkc_bin_sha': binsha, 'bad_spellings': ['lenr'],
                'repairs': [['lenr', 1, 'restore-property']], 'record_path': rel,
                'output_check': 'not_executable_under_either', 'behaviour': 'preserved',
                'needs_regen': False})
if bank:
    with open(LEDGER, 'a') as f:
        for e in led: f.write(json.dumps(e) + '\n')
print(json.dumps(led, indent=1)[:400]); print('BANKED' if bank else 'DRY', len(led))
