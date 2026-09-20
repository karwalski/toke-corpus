import re
def strip(src):
    out=[];i=0;n=len(src)
    while i<n:
        c=src[i]
        if c=='"':
            i+=1
            while i<n and src[i]!='"':
                if src[i]=='\\': i+=1
                i+=1
            i+=1; out.append('""')
        elif c=='#':
            while i<n and src[i]!='\n': i+=1
        else:
            out.append(c); i+=1
    return ''.join(out)

ID=r'[A-Za-z_][A-Za-z0-9_]*'
NB=r'(?![A-Za-z0-9_])'
IMPORT=re.compile(r'^\s*[im]\s*=.*$',re.M)   # module decl + import lines carry std.x paths
FIELD_READ=re.compile(r'(?:'+ID+r'|\)|\})\s*\.\s*('+ID+r')'+NB+r'\s*(?!\()')
STRUCT_LIT=re.compile(r'\b('+ID+r')\s*\{\s*'+ID+r'\s*:')
REC_DECL=re.compile(r'^\s*r\s*=\s*'+ID+r'\s*\{',re.M)
MT=re.compile(r'\bmt'+NB)

def prep(src):
    return IMPORT.sub('',strip(src))

def classify(src):
    s=prep(src)
    tags=set()
    frs=[m.group(1) for m in FIELD_READ.finditer(s)]
    if frs: tags.add('field_read')
    if STRUCT_LIT.search(s): tags.add('struct_lit')
    if REC_DECL.search(s): tags.add('rec_decl')
    if MT.search(s) and frs: tags.add('mt_field')
    return tags

def susceptible(src):
    return bool(classify(src) & {'field_read','struct_lit'})
