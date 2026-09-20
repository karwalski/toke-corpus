"""131.78 — repair the bare-property stdlib spellings the 131.77 survey found."""
import json, os, re, subprocess, tempfile, sys

CORP = '/Users/matthew.watt/tk/toke-corpus'
NEW  = '/Users/matthew.watt/tk/tmpwork/131_77/new/toke'
OLD  = '/Users/matthew.watt/tk/tmpwork/131_77/old/toke'

METHOD = {'upper', 'lower', 'trim'}                    # builtin UFCS: just add ()
CHARCODE = {'ord', 'code', 'tocode', 'tocharcode', 'charcode', 'ascii'}
MODCALL = {'bytes'}                                    # s.bytes(x)
STRUCTURAL = {'keys'}                                  # not a call; needs regen

ID = 'abcdefghijklmnopqrstuvwxyz0123456789'

def mask(src):
    """same-length copy with string bodies and comments blanked — but the
    EXPRESSION inside an interpolation segment `\\(...)` stays visible, since
    that is real code (and is where several of the bare-property spellings
    live)."""
    out = list(src); i = 0; n = len(src)

    def blank_string(i):
        """i is at the opening quote; returns the index past the closing quote."""
        out[i] = ' '; i += 1
        while i < n and src[i] != '"':
            if src[i] == '\\' and i + 1 < n and src[i+1] == '(':
                out[i] = ' '; i += 1          # the backslash
                depth = 0
                while i < n:                   # leave the expression visible
                    if src[i] == '(': depth += 1
                    elif src[i] == ')':
                        depth -= 1
                        if depth == 0: i += 1; break
                    elif src[i] == '"': i = blank_string(i); continue
                    i += 1
                continue
            if src[i] == '\\':
                out[i] = ' '; i += 1
            if i < n:
                out[i] = ' '; i += 1
        if i < n: out[i] = ' '; i += 1
        return i

    while i < n:
        c = src[i]
        if c == '"':
            i = blank_string(i)
        elif c == '#':
            while i < n and src[i] != '\n':
                out[i] = ' '; i += 1
        else:
            i += 1
    return ''.join(out)


def recv_start(m, j):
    """start offset of the receiver expression ending at masked offset j (the '.')."""
    i = j
    while i > 0:
        c = m[i-1]
        if c == ')':
            depth = 1; i -= 1
            while i > 0 and depth:
                i -= 1
                if m[i] == ')': depth += 1
                elif m[i] == '(': depth -= 1
            continue
        if c in ID or c == '.':
            i -= 1
            continue
        break
    return i

def str_alias(src):
    m = re.search(r'^i=([a-z0-9]+):std\.str;\s*$', src, re.M)
    return m.group(1) if m else None

def add_str_import(src, alias='s'):
    """insert `i=<alias>:std.str;` after the last import (or the module line)."""
    lines = src.splitlines(keepends=True)
    k = 1
    while k < len(lines) and lines[k].startswith('i='):
        k += 1
    lines.insert(k, 'i=%s:std.str;\n' % alias)
    return ''.join(lines)

def occurrences(src, field):
    m = mask(src)
    pat = re.compile(r'\.\s*' + field + r'(?![a-z0-9])\s*(?!\()')
    return [mo.start() for mo in pat.finditer(m)]

def repair(src, field, alias_holder):
    """one pass: repair every bare `.field` occurrence. Returns (src, n)."""
    m = mask(src)
    hits = occurrences(src, field)
    if not hits:
        return src, 0
    if field in METHOD:
        out = src; off = 0
        for h in hits:
            end = h + len(re.match(r'\.\s*' + field, m[h:]).group(0))
            out = out[:end + off] + '()' + out[end + off:]
            off += 2
        return out, len(hits)
    # module-call forms need the std.str alias
    alias = alias_holder[0]
    if alias is None:
        alias = str_alias(src) or 's'
        alias_holder[0] = alias
    out = src; off = 0
    for h in hits:
        rs = recv_start(m, h)
        end = h + len(re.match(r'\.\s*' + field, m[h:]).group(0))
        recv = src[rs:h]
        if field in CHARCODE:
            new = '%s.charcode(%s;0)' % (alias, recv)
        else:
            new = '%s.%s(%s)' % (alias, field, recv)
        out = out[:rs + off] + new + out[end + off:]
        off += len(new) - (end - rs)
    return out, len(hits)

def diags(binary, src, build=False, outbin=None, cwd=None):
    d = cwd or tempfile.mkdtemp()
    p = os.path.join(d, 'x.tk')
    open(p, 'w').write(src)
    argv = [binary, '--diag-json', p] + (['-o', outbin or os.path.join(d, 'x')] if build else ['--emit-llvm'])
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=d)
    ds = []
    for ln in (r.stdout + r.stderr).splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try: ds.append(json.loads(ln))
            except Exception: pass
    return r.returncode, [x for x in ds if x.get('severity') == 'error'], (r.stdout + r.stderr)

def field_of(d):
    mm = re.search(r"field '([a-z0-9]+)'", d.get('message') or '')
    return mm.group(1) if mm else None


def lower_struct_fields(src, struct, field):
    """E4025 legacy-case repair: the `t=$s{NotFound:str;...}` declaration keeps
    the pre-131 uppercase field spelling while the constructors use the
    lowercase one. Lowercase the DECLARATION (Profile-1 has no uppercase)."""
    m = re.search(r'(t=\$' + struct + r'\{)([^}]*)(\})', src)
    if not m:
        return src, 0
    body = m.group(2)
    new = re.sub(r'(^|;)\s*([A-Za-z][A-Za-z0-9]*)\s*:',
                 lambda g: g.group(1) + g.group(2).lower() + ':', body)
    if new == body:
        return src, 0
    return src[:m.start(2)] + new + src[m.end(2):], 1


E4025_RE = re.compile(r"struct '([a-z0-9]+)' has no field '([a-z0-9]+)'")
