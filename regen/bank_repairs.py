#!/usr/bin/env python3
"""Main-thread acceptance for the 129.4/129.5 repair wave (workers are never
trusted). For each gen/fix_<task_id>.tk: re-run the full audit gates; on pass,
replace the corpus record's tk_source (original archived to audit/replaced/,
provenance stamped regen.repaired), refresh the audit ledger row, mark the
bank ledger; on fail, mark rejected (worker gets one retry via re-prep).
Processed candidates are archived .done (idempotent, resumable).
"""
import hashlib, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import audit_one, load_specs           # noqa: E402

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "repair_129")


def main():
    specs = load_specs(CORPUS)
    gen = os.path.join(WD, "gen")
    replaced_dir = os.path.join(CORPUS, "audit", "replaced")
    os.makedirs(replaced_dir, exist_ok=True)
    bank_ledger = os.path.join(WD, "bank_ledger.jsonl")
    audit_ledger = os.path.join(CORPUS, "audit", "audit_corpus.jsonl")
    banked = rejected = 0
    for fn in sorted(os.listdir(gen)):
        if not fn.startswith("fix_") or not fn.endswith(".tk"):
            continue
        tid = fn[4:-3]
        spec = specs.get(tid)
        path = os.path.join(gen, fn)
        if spec is None:
            os.rename(path, path + ".done")
            continue
        src = open(path, errors="replace").read()
        tmp = path + ".rec.json"
        with open(tmp, "w") as f:
            json.dump({"tk_source": src, "regen": {}}, f)
        try:
            row = audit_one((tid, tmp, spec, WD))
        finally:
            os.unlink(tmp)
        gates_ok = (row.get("compile")
                    and not row.get("driver_fail")
                    and (not row.get("executed") or row.get("tests_new"))
                    and (row.get("idiom") or 1.0) >= 0.6
                    and (row.get("metrics") or {}).get("max_depth", 0) <= 4
                    and (row.get("metrics") or {}).get("max_func_bytes", 0) <= 600)
        rec_path = os.path.join(CORPUS, spec["category"], tid + ".json")
        if gates_ok:
            rec = json.load(open(rec_path))
            with open(os.path.join(replaced_dir, tid + ".tk"), "w") as f:
                f.write(rec["tk_source"])
            rec["tk_source"] = src
            rec["regen"]["source_sha256"] = hashlib.sha256(src.encode()).hexdigest()
            rec["regen"]["repaired"] = "129.4-5"
            rec["regen"]["repaired_ts"] = int(time.time())
            with open(rec_path, "w") as f:
                json.dump(rec, f)
            row["sha256"] = rec["regen"]["source_sha256"]
            row["repaired"] = True
            with open(audit_ledger, "a") as f:      # last-write-wins in report
                f.write(json.dumps(row) + "\n")
            status = "banked"
            banked += 1
        else:
            status = "rejected"
            rejected += 1
        with open(bank_ledger, "a") as f:
            f.write(json.dumps({"task_id": tid, "status": status,
                                "reason": row.get("reason") or row.get("driver_fail"),
                                "ts": int(time.time())}) + "\n")
        os.rename(path, path + ".done")
    print(json.dumps({"banked": banked, "rejected": rejected}))


if __name__ == "__main__":
    main()
