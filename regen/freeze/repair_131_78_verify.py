import json, os, re, subprocess, tempfile, collections
CORP='/Users/matthew.watt/tk/toke-corpus'
NEW='/Users/matthew.watt/tk/tmpwork/131_77/new/toke'
bl=json.load(open(CORP+'/regen/freeze/blast_radius_131.77.json'))['repair_needed']
def recpath(x):
    ps=[f for f in x['files'] if re.match(r'corpus/regen_v04/[A-Z]+-[A-Z]+/',f)]
    return CORP+'/'+ps[0] if ps else None
c=collections.Counter(); bad=[]; mb=[]
for x in bl:
    p=recpath(x)
    if not p: c['not_in_regen_v04']+=1; continue
    rec=json.load(open(p))
    src=rec['tk_source']
    with tempfile.TemporaryDirectory() as d:
        f=os.path.join(d,'x.tk'); open(f,'w').write(src)
        r=subprocess.run([NEW,'--emit-llvm','--diag-json',f],capture_output=True,text=True,cwd=d,timeout=120)
    ok = r.returncode==0
    c['emit_ok' if ok else 'emit_err']+=1
    st=(rec.get('regen') or {}).get('rewrite131') or {}
    c['stamped' if st.get('story')=='131.78' else 'UNSTAMPED']+=1
    if not ok: bad.append((x['id'], sorted(set(re.findall(r'"error_code"\s*:\s*"([A-Z0-9]+)"',r.stdout+r.stderr))), st.get('status')))
    if st.get('min_bytes_before') and st.get('min_bytes_after'): mb.append((st['min_bytes_before'],st['min_bytes_after']))
print(c)
print('still rejected:',bad)
import statistics
if mb:
    b=[x[0] for x in mb]; a=[x[1] for x in mb]
    print('min_bytes p50 %d -> %d ; mean %.1f -> %.1f ; total %d -> %d (+%d)'%(
        statistics.median(b),statistics.median(a),statistics.mean(b),statistics.mean(a),sum(b),sum(a),sum(a)-sum(b)))
