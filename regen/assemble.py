"""Sanitize worker output and assemble a compilable module for validation."""
import json, re, sys

DEFAULTS = {"i64":"0","i32":"0","u64":"0","u32":"0","f64":"0.0","f32":"0.0","bool":"false","str":'""',"$str":'""',"void":"0"}

def sanitize(text):
    # strip markdown fences and any prose before the first toke declaration
    text = re.sub(r"```[a-z]*", "", text)
    lines = text.splitlines()
    start = 0
    for i, l in enumerate(lines):
        if re.match(r"\s*(m=|f=|t=|i=)", l):
            start = i
            break
    return "\n".join(lines[start:]).strip() + "\n"

def stub_from_sig(sig):
    m = re.match(r"f=([a-z0-9]+)\(([^)]*)\):(\S+)", sig.strip())
    if not m: return None
    name, params, ret = m.groups()
    if ret.startswith("@"): body = "<@()"
    else: body = "<" + DEFAULTS.get(ret, "0")
    return f"f={name}({params}):{ret}{{{body}}};"

def assemble(spec, raw):
    src = sanitize(raw)
    if spec.get("task_type") != "single_function":
        return src
    parts = ["m=harness;", "i=io:std.io;", "i=s:std.str;"]
    ctx = spec.get("domain_context_v03","")
    for sig in re.findall(r"f=[a-z0-9]+\([^)]*\):\S+", ctx):
        st = stub_from_sig(sig)
        if st: parts.append(st)
    parts.append(src)
    return "\n".join(parts) + "\n"

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    raw = open(sys.argv[2]).read()
    sys.stdout.write(assemble(spec, raw))
