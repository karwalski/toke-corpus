import sys,json,os,subprocess,tempfile,re
sys.path.insert(0,'/Users/matthew.watt/tk/tmpwork/131_77')
from susp77 import classify
from concurrent.futures import ProcessPoolExecutor

OLD='/Users/matthew.watt/tk/tmpwork/131_77/old/toke'
NEW='/Users/matthew.watt/tk/tmpwork/131_77/new/toke'

recs=json.load(open('/tmp/corpus_uniq.json'))
susp=[r for r in recs if classify(r['src']) & {'field_read','struct_lit'}]
del recs

def emit(binary,src,d,tag):
    p=os.path.join(d,tag+'.tk')
    open(p,'w').write(src)
    try:
        r=subprocess.run([binary,'--emit-llvm','--diag-json',p],
                         capture_output=True,text=True,timeout=40,cwd=d)
    except Exception:
        return ('TIMEOUT','')
    ll=os.path.join(d,tag+'.ll')
    if r.returncode==0 and os.path.exists(ll):
        return ('OK',open(ll,errors='replace').read())
    codes=sorted(set(re.findall(r'"error_code"\s*:\s*"([A-Z0-9]+)"',r.stdout+r.stderr)))
    return ('ERR:'+','.join(codes),'')

def norm(ll):
    # drop file-path / module-id noise that differs only by temp dir name
    out=[]
    for l in ll.splitlines():
        if l.startswith('; ModuleID') or l.startswith('source_filename'): continue
        l=re.sub(r'old\.tk|new\.tk','X.tk',l)
        out.append(l)
    return '\n'.join(out)

def work(r):
    with tempfile.TemporaryDirectory() as d:
        os_,lo=emit(OLD,r['src'],d,'old')
        ns,ln=emit(NEW,r['src'],d,'new')
    if os_=='OK' and ns=='OK':
        return (r['id'],'IRDIFF' if norm(lo)!=norm(ln) else 'SAME',os_,ns)
    if os_=='OK' and ns!='OK':
        return (r['id'],'NOW_REJECTED',os_,ns)
    if os_!='OK' and ns=='OK':
        return (r['id'],'NOW_ACCEPTED',os_,ns)
    return (r['id'],'BOTH_ERR',os_,ns)

if __name__=='__main__':
    print('susceptible:',len(susp),flush=True)
    out=[]
    from collections import Counter
    c=Counter()
    with ProcessPoolExecutor(max_workers=12) as ex:
        for n,res in enumerate(ex.map(work,susp,chunksize=20)):
            c[res[1]]+=1
            if res[1] in ('IRDIFF','NOW_REJECTED','NOW_ACCEPTED'): out.append(res)
            if n%2000==0: print(n,dict(c),flush=True)
    print('FINAL',dict(c),flush=True)
    json.dump(out,open('/tmp/diff77_out.json','w'))
