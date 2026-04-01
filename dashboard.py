"""Corpus pipeline monitoring dashboard.

Lightweight HTTP dashboard served on port 8080.
Reads progress.json, logs, and system stats to render real-time metrics.
No external dependencies — uses stdlib only + Chart.js via CDN.
"""

import http.server
import json
import os
import re
import ssl
import subprocess
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import math


def _sanitize_for_json(obj):
    """Replace Infinity/NaN with None for JSON serialization."""
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


PORT = 443
CERT_FILE = Path("/opt/toke-corpus/dashboard.crt")
KEY_FILE = Path("/opt/toke-corpus/dashboard.key")
CORPUS_DIR = Path("/opt/toke-corpus/corpus")
METRICS_DIR = Path("/opt/toke-corpus/metrics")
LOGS_DIR = Path("/opt/toke-corpus/logs")
PROGRESS_FILE = METRICS_DIR / "progress.json"
DEFERRED_FILE = LOGS_DIR / "deferred_failures.jsonl"
TOTAL_TARGET = 25000
PHASE_B_DIR = Path("/opt/toke-corpus/corpus/phase_b")
PHASE_B_TARGET = 10000
PHASE_C_DIR = Path("/opt/toke-corpus/corpus/phase_c")
PHASE_C_TARGET = 5000
PHASE_D_DIR = Path("/opt/toke-corpus/corpus/phase_d")
PHASE_D_TARGET = 5000
SCORECARD_FILE = METRICS_DIR / "scorecard.json"
TRIAL_RESULTS_FILE = METRICS_DIR / "trial_results.json"

# Cache for expensive computations
_cache = {"ts": 0, "data": None}
CACHE_TTL = 10  # seconds


def get_system_stats():
    """Get CPU, RAM, disk stats."""
    stats = {}
    try:
        load = os.getloadavg()
        stats["cpu_load_1m"] = round(load[0], 2)
        stats["cpu_load_5m"] = round(load[1], 2)
        stats["cpu_load_15m"] = round(load[2], 2)
        cpu_count = os.cpu_count() or 1
        stats["cpu_count"] = cpu_count
        stats["cpu_pct"] = round(load[0] / cpu_count * 100, 1)
    except Exception:
        stats["cpu_load_1m"] = 0
        stats["cpu_pct"] = 0

    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            stats["ram_total_gb"] = round(total / 1048576, 1)
            stats["ram_used_gb"] = round((total - avail) / 1048576, 1)
            stats["ram_pct"] = round((total - avail) / total * 100, 1) if total else 0
    except Exception:
        stats["ram_total_gb"] = 0
        stats["ram_used_gb"] = 0
        stats["ram_pct"] = 0

    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        stats["disk_total_gb"] = round(total / (1024**3), 1)
        stats["disk_used_gb"] = round(used / (1024**3), 1)
        stats["disk_pct"] = round(used / total * 100, 1) if total else 0
    except Exception:
        stats["disk_total_gb"] = 0
        stats["disk_used_gb"] = 0
        stats["disk_pct"] = 0

    return stats


def get_corpus_count():
    """Count corpus entries by category."""
    counts = Counter()
    total = 0
    if CORPUS_DIR.exists():
        for f in CORPUS_DIR.rglob("*.json"):
            cat = f.parent.name
            if cat in ("phase_b", "phase_c", "phase_d", "B-CMP", "C-EDG", "D-APP", "corpus"):
                continue  # counted separately
            counts[cat] += 1
            total += 1
    return total, dict(sorted(counts.items()))


def get_phase_b_count():
    """Count Phase B corpus entries."""
    if not PHASE_B_DIR.exists():
        return 0
    return sum(1 for _ in PHASE_B_DIR.rglob("*.json"))


def get_phase_c_count():
    """Count Phase C corpus entries."""
    if not PHASE_C_DIR.exists():
        return 0
    return sum(1 for _ in PHASE_C_DIR.rglob("*.json"))


def get_phase_d_count():
    """Count Phase D corpus entries."""
    if not PHASE_D_DIR.exists():
        return 0
    return sum(1 for _ in PHASE_D_DIR.rglob("*.json"))


def get_gate1_metrics():
    """Read Gate 1 scorecard and trial results."""
    gate1 = {"scorecard": None, "trial_summary": None}
    try:
        with open(SCORECARD_FILE) as f:
            gate1["scorecard"] = json.load(f)
    except Exception:
        pass
    try:
        with open(TRIAL_RESULTS_FILE) as f:
            data = json.load(f)
            # Summarize trial results (file can be large)
            if isinstance(data, list):
                total = len(data)
                passed = sum(1 for t in data if t.get("accepted", False))
                gate1["trial_summary"] = {
                    "total_trials": total,
                    "passed": passed,
                    "pass_rate": round(passed / total * 100, 1) if total else 0,
                }
            elif isinstance(data, dict):
                gate1["trial_summary"] = data
    except Exception:
        pass
    return gate1


def get_progress():
    """Read progress.json metrics."""
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_deferred_stats():
    """Analyze deferred failures."""
    by_cat = Counter()
    by_model = Counter()
    total = 0
    try:
        with open(DEFERRED_FILE) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    by_cat[d.get("category", "?")] += 1
                    by_model[d.get("model", "?")] += 1
                    total += 1
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return {"total": total, "by_category": dict(by_cat.most_common()), "by_model": dict(by_model.most_common())}


def get_pipeline_status():
    """Check if pipeline tmux session is running."""
    try:
        # Check both root and ubuntu user tmux sessions
        for cmd in [["tmux", "list-sessions"], ["su", "-c", "tmux list-sessions", "ubuntu"]]:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if "corpus" in result.stdout:
                return True
        # Fallback: check if main.py process is running
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"], capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def parse_log_timeseries():
    """Parse the current run log for time series data."""
    log_file = _find_current_log()
    if not log_file:
        return {"minutes": [], "accepted": [], "failed": [], "autofixed": [],
                "transpiled": [], "corrections": [], "errors": []}

    ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
    accepted_per_min = Counter()
    failed_per_min = Counter()
    rescued_per_min = Counter()
    autofixed_per_min = Counter()
    transpiled_per_min = Counter()
    corrections_per_min = Counter()
    errors_per_min = Counter()
    first_ts = None
    last_ts = None
    batch_times = []  # Track time between batches for avg calculation
    last_batch_ts = None

    try:
        with open(log_file) as f:
            for line in f:
                m = ts_pattern.match(line)
                if not m:
                    continue
                minute = m.group(1)
                if first_ts is None:
                    first_ts = minute
                last_ts = minute

                if "rescued by" in line:
                    rescued_per_min[minute] += 1
                    if "autofixed" in line:
                        autofixed_per_min[minute] += 1
                    elif "transpiled" in line:
                        transpiled_per_min[minute] += 1

                if "Task " in line and "rejected:" in line:
                    failed_per_min[minute] += 1
                elif "Task " in line and "accepted" in line:
                    accepted_per_min[minute] += 1

                if "Correction attempt" in line:
                    corrections_per_min[minute] += 1
                if " ERROR " in line:
                    errors_per_min[minute] += 1
                if "correction attempts failed" in line:
                    failed_per_min[minute] += 1

                if "Processing batch" in line:
                    # Extract full timestamp for batch timing
                    full_ts = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if full_ts and last_batch_ts:
                        try:
                            curr = datetime.strptime(full_ts.group(1), "%Y-%m-%d %H:%M:%S")
                            prev = datetime.strptime(last_batch_ts, "%Y-%m-%d %H:%M:%S")
                            batch_times.append((curr - prev).total_seconds())
                        except ValueError:
                            pass
                    if full_ts:
                        last_batch_ts = full_ts.group(1)

    except Exception:
        pass

    all_minutes = sorted(
        set(accepted_per_min) | set(failed_per_min) | set(rescued_per_min)
        | set(autofixed_per_min) | set(transpiled_per_min)
        | set(corrections_per_min) | set(errors_per_min)
    )

    avg_batch_time = round(sum(batch_times) / len(batch_times), 1) if batch_times else 0
    last_batch_secs = round(batch_times[-1], 1) if batch_times else 0

    return {
        "minutes": all_minutes,
        "accepted": [accepted_per_min.get(m, 0) for m in all_minutes],
        "failed": [failed_per_min.get(m, 0) for m in all_minutes],
        "rescued": [rescued_per_min.get(m, 0) for m in all_minutes],
        "autofixed": [autofixed_per_min.get(m, 0) for m in all_minutes],
        "transpiled": [transpiled_per_min.get(m, 0) for m in all_minutes],
        "corrections": [corrections_per_min.get(m, 0) for m in all_minutes],
        "errors": [errors_per_min.get(m, 0) for m in all_minutes],
        "first_ts": first_ts,
        "last_ts": last_ts,
        "avg_batch_secs": avg_batch_time,
        "last_batch_secs": last_batch_secs,
    }


def parse_log_recovery_stats():
    """Count rescue method usage from logs."""
    log_file = _find_current_log()
    stats = {"direct": 0, "autofixed": 0, "transpiled": 0, "corrected": 0,
             "autofix_fixes": Counter()}
    if not log_file:
        return stats
    try:
        with open(log_file) as f:
            for line in f:
                if "rescued by autofixed" in line:
                    stats["autofixed"] += 1
                elif "rescued by transpiled" in line:
                    stats["transpiled"] += 1
                elif "rescued by corrected" in line or ("Correction" in line and "succeeded" in line):
                    stats["corrected"] += 1

                m = re.search(r"Auto-fixer applied \d+ fixes for \S+: (.+)", line)
                if m:
                    for fix in m.group(1).split(", "):
                        stats["autofix_fixes"][fix.strip()] += 1
    except Exception:
        pass
    stats["autofix_fixes"] = dict(stats["autofix_fixes"].most_common(20))
    return stats


def parse_api_errors():
    """Detect API rate limits and balance issues from logs."""
    log_file = _find_current_log()
    issues = []
    rate_limits = Counter()
    balance_errors = Counter()
    if not log_file:
        return {"issues": issues, "rate_limits": dict(rate_limits),
                "balance_errors": dict(balance_errors)}
    try:
        with open(log_file) as f:
            for line in f:
                if "429" in line or "rate" in line.lower() and "limit" in line.lower():
                    for provider in ["anthropic", "openai", "x.ai"]:
                        if provider in line:
                            rate_limits[provider] += 1
                if "402" in line or "insufficient" in line.lower() or "balance" in line.lower() or "quota" in line.lower():
                    for provider in ["anthropic", "openai", "x.ai"]:
                        if provider in line:
                            balance_errors[provider] += 1
    except Exception:
        pass

    for provider, count in rate_limits.items():
        if count > 5:
            issues.append(f"{provider}: {count} rate limit hits")
    for provider, count in balance_errors.items():
        if count > 0:
            issues.append(f"{provider}: {count} balance/quota errors")

    return {"issues": issues, "rate_limits": dict(rate_limits),
            "balance_errors": dict(balance_errors)}


def _find_current_log():
    """Find the most recent full-run log."""
    logs = sorted(LOGS_DIR.glob("full-run-*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def get_all_metrics():
    """Aggregate all metrics."""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]

    corpus_total, corpus_by_cat = get_corpus_count()
    phase_b_count = get_phase_b_count()
    phase_c_count = get_phase_c_count()
    phase_d_count = get_phase_d_count()
    gate1 = get_gate1_metrics()
    progress = get_progress()
    deferred = get_deferred_stats()
    system = get_system_stats()
    timeseries = parse_log_timeseries()
    recovery = parse_log_recovery_stats()
    api_issues = parse_api_errors()
    running = get_pipeline_status()

    per_cat = progress.get("per_category", {})
    held_categories = []
    for cat, stats in per_cat.items():
        dispatched = stats.get("dispatched", 0)
        accepted = stats.get("accepted", 0)
        if dispatched >= 50:
            rejection_rate = 1 - (accepted / dispatched) if dispatched else 0
            if rejection_rate > 0.80:
                held_categories.append({
                    "category": cat,
                    "rejection_rate": round(rejection_rate * 100, 1),
                    "dispatched": dispatched,
                    "accepted": accepted,
                })

    # Pass@1 rate
    total_dispatched = progress.get("dispatched", 0)
    total_accepted = progress.get("accepted", 0)
    pass_at_1 = round(total_accepted / total_dispatched * 100, 1) if total_dispatched else 0

    # ETA
    started_at = progress.get("started_at", "")
    remaining = TOTAL_TARGET - corpus_total
    eta_hours = progress.get("estimated_remaining_hours", 0)

    # Rate calculation from progress timestamps
    rate_per_hour = 0
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            elapsed_hrs = (datetime.now(timezone.utc) - start).total_seconds() / 3600
            if elapsed_hrs > 0.01:
                run_accepted = progress.get("accepted", 0)
                rate_per_hour = round(run_accepted / elapsed_hrs, 1)
        except Exception:
            pass

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_running": running,
        "system": system,
        "corpus": {
            "total": corpus_total,
            "target": TOTAL_TARGET,
            "pct_complete": round(corpus_total / TOTAL_TARGET * 100, 2),
            "remaining": remaining,
            "by_category": corpus_by_cat,
        },
        "phase_b": {
            "total": phase_b_count,
            "target": PHASE_B_TARGET,
            "pct_complete": round(phase_b_count / PHASE_B_TARGET * 100, 2) if PHASE_B_TARGET else 0,
            "remaining": PHASE_B_TARGET - phase_b_count,
        },
        "phase_c": {
            "total": phase_c_count,
            "target": PHASE_C_TARGET,
            "pct_complete": round(phase_c_count / PHASE_C_TARGET * 100, 2) if PHASE_C_TARGET else 0,
            "remaining": max(0, PHASE_C_TARGET - phase_c_count),
        },
        "phase_d": {
            "total": phase_d_count,
            "target": PHASE_D_TARGET,
            "pct_complete": round(phase_d_count / PHASE_D_TARGET * 100, 2) if PHASE_D_TARGET else 0,
            "remaining": max(0, PHASE_D_TARGET - phase_d_count),
        },
        "combined_total": corpus_total + phase_b_count + phase_c_count + phase_d_count,
        "combined_target": TOTAL_TARGET + PHASE_B_TARGET + PHASE_C_TARGET + PHASE_D_TARGET,
        "gate1": gate1,
        "progress": {
            "dispatched": total_dispatched,
            "accepted": total_accepted,
            "failed": progress.get("failed", 0),
            "pass_at_1_pct": pass_at_1,
            "rate_per_hour": rate_per_hour,
            "eta_hours": round(eta_hours, 1),
            "per_category": per_cat,
            "per_model": progress.get("per_model", {}),
            "cost": progress.get("cost", {}),
        },
        "held_categories": held_categories,
        "deferred": deferred,
        "recovery": recovery,
        "api_issues": api_issues,
        "timeseries": timeseries,
    }

    _cache["data"] = data
    _cache["ts"] = now
    return data


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Toke Corpus Pipeline</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, 'Segoe UI', Roboto, monospace; background: #0d1117; color: #c9d1d9; }
  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 18px; font-weight: 600; color: #f0f6fc; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .status-dot.running { background: #3fb950; animation: pulse 2s infinite; }
  .status-dot.stopped { background: #f85149; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; padding: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 13px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .metric { font-size: 32px; font-weight: 700; color: #f0f6fc; }
  .metric-sm { font-size: 20px; font-weight: 600; color: #f0f6fc; }
  .sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
  .bar { height: 6px; background: #21262d; border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
  .bar-fill.green { background: #3fb950; }
  .bar-fill.blue { background: #58a6ff; }
  .bar-fill.yellow { background: #d29922; }
  .bar-fill.red { background: #f85149; }
  .wide { grid-column: 1 / -1; }
  .chart-container { position: relative; height: 250px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 500; }
  td { color: #c9d1d9; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .tag.held { background: #f8514922; color: #f85149; }
  .tag.ok { background: #3fb95022; color: #3fb950; }
  .tag.warn { background: #d2992222; color: #d29922; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .cols3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  .mini-stat { text-align: center; }
  .mini-stat .val { font-size: 24px; font-weight: 700; color: #f0f6fc; }
  .mini-stat .label { font-size: 11px; color: #8b949e; }
  .alert { background: #f8514915; border: 1px solid #f8514940; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; color: #f85149; font-size: 13px; }
  #last-update { font-size: 11px; color: #484f58; }
</style>
</head>
<body>
<div class="header">
  <span class="status-dot" id="pipeline-dot"></span>
  <h1>Toke Corpus Pipeline Dashboard</h1>
  <span id="last-update"></span>
</div>

<!-- Overall Corpus Timeline -->
<div class="grid" style="padding-bottom:0">
  <div class="card wide" style="padding:14px 20px">
    <h2 style="margin-bottom:10px">Overall Corpus Timeline</h2>
    <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
      <div>
        <span style="color:#8b949e;font-size:12px">Core (A):</span>
        <strong id="tl-phase-a" style="color:#3fb950">--</strong>
        <span style="color:#8b949e;font-size:12px">/ 25,000</span>
        <span style="color:#484f58;font-size:12px">(<span id="tl-phase-a-pct">--</span>%)</span>
      </div>
      <div style="color:#30363d;font-size:16px">&#x2192;</div>
      <div>
        <span style="color:#8b949e;font-size:12px">Phase B:</span>
        <strong id="tl-phase-b" style="color:#a371f7">--</strong>
        <span style="color:#8b949e;font-size:12px">/ 10,000</span>
        <span style="color:#484f58;font-size:12px">(<span id="tl-phase-b-pct">--</span>%)</span>
      </div>
      <div style="color:#30363d;font-size:16px">&#x2192;</div>
      <div>
        <span style="color:#8b949e;font-size:12px">Phase C:</span>
        <strong id="tl-phase-c" style="color:#58a6ff">--</strong>
        <span style="color:#8b949e;font-size:12px">/ 5,000</span>
        <span style="color:#484f58;font-size:12px">(<span id="tl-phase-c-pct">--</span>%)</span>
      </div>
      <div style="color:#30363d;font-size:16px">&#x2192;</div>
      <div>
        <span style="color:#8b949e;font-size:12px">Phase D:</span>
        <strong id="tl-phase-d" style="color:#d29922">--</strong>
        <span style="color:#8b949e;font-size:12px">/ 5,000</span>
        <span style="color:#484f58;font-size:12px">(<span id="tl-phase-d-pct">--</span>%)</span>
      </div>
      <div style="color:#30363d;font-size:16px">&#x2192;</div>
      <div>
        <span style="color:#8b949e;font-size:12px">Combined:</span>
        <strong id="tl-combined" style="color:#f0f6fc">--</strong>
        <span style="color:#8b949e;font-size:12px">/ 45,000</span>
      </div>
    </div>
    <div class="bar" style="margin-top:10px;height:8px;display:flex;gap:2px;background:transparent">
      <div style="flex:25000;background:#21262d;border-radius:3px;overflow:hidden"><div class="bar-fill green" id="tl-bar-a" style="height:100%"></div></div>
      <div style="flex:5000;background:#21262d;border-radius:3px;overflow:hidden"><div class="bar-fill" id="tl-bar-b" style="height:100%;background:#a371f7"></div></div>
      <div style="flex:5000;background:#21262d;border-radius:3px;overflow:hidden"><div class="bar-fill" id="tl-bar-c" style="height:100%;background:#58a6ff"></div></div>
      <div style="flex:5000;background:#21262d;border-radius:3px;overflow:hidden"><div class="bar-fill" id="tl-bar-d" style="height:100%;background:#d29922"></div></div>
    </div>
  </div>
</div>

<div class="grid">
  <!-- Progress -->
  <div class="card">
    <h2>Corpus Progress</h2>
    <div class="metric" id="corpus-total">--</div>
    <div class="sub" id="corpus-sub">of 25,000 target</div>
    <div class="bar"><div class="bar-fill green" id="corpus-bar"></div></div>
    <div class="sub" style="margin-top:8px"><span id="corpus-pct">--</span>% complete &bull; <span id="corpus-remaining">--</span> remaining</div>
  </div>

  <!-- Rate -->
  <div class="card">
    <h2>Processing Rate</h2>
    <div class="metric" id="rate-val">--</div>
    <div class="sub">entries/hour (this run)</div>
    <div style="margin-top:12px">
      <div class="sub">Pass@1: <strong id="pass1-val" style="color:#f0f6fc">--</strong>%</div>
      <div class="sub">Dispatched: <strong id="dispatched-val" style="color:#f0f6fc">--</strong> | Accepted: <strong id="accepted-val" style="color:#3fb950">--</strong> | Failed: <strong id="failed-val" style="color:#f85149">--</strong></div>
    </div>
  </div>

  <!-- ETA -->
  <div class="card">
    <h2>Estimated Completion</h2>
    <div class="metric-sm" id="eta-val">--</div>
    <div class="sub" id="eta-sub">at current rate</div>
    <div style="margin-top:12px">
      <div class="sub">Last batch: <strong id="last-batch-time" style="color:#f0f6fc">--</strong>s</div>
      <div class="sub">Avg batch: <strong id="avg-batch-time" style="color:#f0f6fc">--</strong>s</div>
      <div class="sub">Run started: <span id="run-started">--</span></div>
    </div>
  </div>

  <!-- System -->
  <div class="card">
    <h2>System Resources</h2>
    <div class="cols3">
      <div class="mini-stat">
        <div class="val" id="cpu-val">--</div>
        <div class="label">CPU %</div>
        <div class="bar"><div class="bar-fill blue" id="cpu-bar"></div></div>
      </div>
      <div class="mini-stat">
        <div class="val" id="ram-val">--</div>
        <div class="label">RAM %</div>
        <div class="bar"><div class="bar-fill yellow" id="ram-bar"></div></div>
      </div>
      <div class="mini-stat">
        <div class="val" id="disk-val">--</div>
        <div class="label">Disk %</div>
        <div class="bar"><div class="bar-fill green" id="disk-bar"></div></div>
      </div>
    </div>
  </div>

  <!-- API Cost -->
  <div class="card">
    <h2>API Cost (This Run)</h2>
    <div class="metric-sm" id="cost-total">$--</div>
    <div class="sub" style="margin-top:8px" id="cost-breakdown"></div>
  </div>

  <!-- API Issues -->
  <div class="card" id="api-issues-card">
    <h2>API Health</h2>
    <div id="api-issues-content"><span class="tag ok">All providers healthy</span></div>
  </div>

  <!-- Recovery Methods -->
  <div class="card">
    <h2>Recovery Methods</h2>
    <div id="recovery-content">
      <div class="cols">
        <div class="mini-stat"><div class="val" id="r-autofix">0</div><div class="label">Auto-fixed</div></div>
        <div class="mini-stat"><div class="val" id="r-transpile">0</div><div class="label">Transpiled</div></div>
      </div>
      <div style="margin-top:8px">
        <div class="sub">Top auto-fix patterns:</div>
        <div id="autofix-patterns" class="sub"></div>
      </div>
    </div>
  </div>

  <!-- Deferred -->
  <div class="card">
    <h2>Deferred Failures</h2>
    <div class="metric-sm" id="deferred-total">--</div>
    <div class="sub" style="margin-top:8px" id="deferred-breakdown"></div>
  </div>

  <!-- B-COMPOSE -->
  <div class="card">
    <h2>B-COMPOSE (Phase B)</h2>
    <div class="metric" id="phase-b-total">--</div>
    <div class="sub" id="phase-b-sub">of 10,000 target</div>
    <div class="bar"><div class="bar-fill" id="phase-b-bar" style="background:#a371f7"></div></div>
    <div class="sub" style="margin-top:8px">
      <span id="phase-b-pct">--</span>% complete &bull; <span id="phase-b-remaining">--</span> remaining
    </div>
    <div class="sub" style="margin-top:4px">Status: <span id="phase-b-status" class="tag">--</span></div>
  </div>

  <!-- C-EDG (Phase C) -->
  <div class="card">
    <h2>C-EDG (Phase C)</h2>
    <div class="metric" id="phase-c-total">--</div>
    <div class="sub" id="phase-c-sub">of 5,000 target</div>
    <div class="bar"><div class="bar-fill" id="phase-c-bar" style="background:#58a6ff"></div></div>
    <div class="sub" style="margin-top:8px">
      <span id="phase-c-pct">--</span>% complete &bull; <span id="phase-c-remaining">--</span> remaining
    </div>
    <div class="sub" style="margin-top:4px">Status: <span id="phase-c-status" class="tag">--</span></div>
  </div>

  <!-- D-APP (Phase D) -->
  <div class="card">
    <h2>D-APP (Phase D)</h2>
    <div class="metric" id="phase-d-total">--</div>
    <div class="sub" id="phase-d-sub">of 5,000 target</div>
    <div class="bar"><div class="bar-fill" id="phase-d-bar" style="background:#d29922"></div></div>
    <div class="sub" style="margin-top:8px">
      <span id="phase-d-pct">--</span>% complete &bull; <span id="phase-d-remaining">--</span> remaining
    </div>
    <div class="sub" style="margin-top:4px">Status: <span id="phase-d-status" class="tag">--</span></div>
  </div>

  <!-- Gate 1 Readiness -->
  <div class="card">
    <h2>Gate 1 Readiness</h2>
    <div id="gate1-content">
      <div class="cols">
        <div class="mini-stat">
          <div class="val" id="g1-pass1">--</div>
          <div class="label">Best Pass@1</div>
          <div class="sub">target: >= 60%</div>
        </div>
        <div class="mini-stat">
          <div class="val" id="g1-composite">--</div>
          <div class="label">Best Composite</div>
          <div class="sub">higher is better</div>
        </div>
      </div>
      <div style="margin-top:12px">
        <div class="sub">Providers passed: <strong id="g1-providers-passed" style="color:#f0f6fc">--</strong></div>
        <div class="sub">Trial tasks: <strong id="g1-trial-count" style="color:#f0f6fc">--</strong></div>
      </div>
      <div style="margin-top:8px">
        <span>Gate status: </span><span id="g1-status" class="tag">--</span>
      </div>
      <div style="margin-top:8px">
        <table id="g1-provider-table" style="font-size:12px">
          <thead><tr><th>Provider</th><th>Pass@1</th><th>Correction</th><th>Composite</th><th>Status</th></tr></thead>
          <tbody id="g1-provider-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Held Categories -->
  <div class="card">
    <h2>Category Status</h2>
    <div id="held-content"></div>
  </div>

  <!-- Per-Model -->
  <div class="card">
    <h2>Model Performance</h2>
    <table id="model-table">
      <thead><tr><th>Model</th><th>Accepted</th><th>Failed</th><th>Rate</th><th>Cost</th></tr></thead>
      <tbody id="model-tbody"></tbody>
    </table>
  </div>

  <!-- Time Series Chart -->
  <div class="card wide">
    <h2>Processing Timeline</h2>
    <div class="chart-container"><canvas id="timeline-chart"></canvas></div>
  </div>

  <!-- Category Breakdown -->
  <div class="card wide">
    <h2>Category Breakdown</h2>
    <table id="cat-table">
      <thead><tr><th>Category</th><th>Corpus</th><th>Dispatched</th><th>Accepted</th><th>Failed</th><th>Rate</th><th>Status</th></tr></thead>
      <tbody id="cat-tbody"></tbody>
    </table>
  </div>
</div>

<script>
let chart = null;

function fmt(n) { return n.toLocaleString(); }

function updateDashboard(d) {
  // Pipeline status
  const dot = document.getElementById('pipeline-dot');
  dot.className = 'status-dot ' + (d.pipeline_running ? 'running' : 'stopped');

  document.getElementById('last-update').textContent =
    'Updated: ' + new Date(d.timestamp).toLocaleTimeString();

  // Corpus
  document.getElementById('corpus-total').textContent = fmt(d.corpus.total);
  document.getElementById('corpus-pct').textContent = d.corpus.pct_complete;
  document.getElementById('corpus-remaining').textContent = fmt(d.corpus.remaining);
  document.getElementById('corpus-bar').style.width = d.corpus.pct_complete + '%';

  // Rate
  document.getElementById('rate-val').textContent = d.progress.rate_per_hour;
  document.getElementById('pass1-val').textContent = d.progress.pass_at_1_pct;
  document.getElementById('dispatched-val').textContent = fmt(d.progress.dispatched);
  document.getElementById('accepted-val').textContent = fmt(d.progress.accepted);
  document.getElementById('failed-val').textContent = fmt(d.progress.failed);

  // ETA
  const hrs = d.progress.eta_hours;
  if (hrs > 24) {
    document.getElementById('eta-val').textContent = (hrs/24).toFixed(1) + ' days';
  } else {
    document.getElementById('eta-val').textContent = hrs.toFixed(1) + ' hours';
  }

  // System
  document.getElementById('cpu-val').textContent = d.system.cpu_pct + '%';
  document.getElementById('cpu-bar').style.width = Math.min(d.system.cpu_pct, 100) + '%';
  document.getElementById('ram-val').textContent = d.system.ram_pct + '%';
  document.getElementById('ram-bar').style.width = d.system.ram_pct + '%';
  document.getElementById('disk-val').textContent = d.system.disk_pct + '%';
  document.getElementById('disk-bar').style.width = d.system.disk_pct + '%';

  // Cost
  const cost = d.progress.cost || {};
  document.getElementById('cost-total').textContent = '$' + (cost.api_total || 0).toFixed(2);
  const bp = cost.by_provider || {};
  document.getElementById('cost-breakdown').innerHTML = Object.entries(bp)
    .map(([k,v]) => k.split('-')[0] + ': $' + v.toFixed(3)).join(' &bull; ');

  // API Issues
  const issues = d.api_issues.issues || [];
  const ic = document.getElementById('api-issues-content');
  if (issues.length > 0) {
    ic.innerHTML = issues.map(i => '<div class="alert">' + i + '</div>').join('');
  } else {
    ic.innerHTML = '<span class="tag ok">All providers healthy</span>';
  }

  // Recovery
  document.getElementById('r-autofix').textContent = d.recovery.autofixed;
  document.getElementById('r-transpile').textContent = d.recovery.transpiled;
  const patterns = Object.entries(d.recovery.autofix_fixes || {}).slice(0, 8);
  document.getElementById('autofix-patterns').innerHTML = patterns
    .map(([k,v]) => '<span class="tag warn">' + k + ' (' + v + ')</span> ').join('');

  // Deferred
  document.getElementById('deferred-total').textContent = fmt(d.deferred.total);
  const dbc = d.deferred.by_category || {};
  document.getElementById('deferred-breakdown').innerHTML =
    Object.entries(dbc).map(([k,v]) => k + ': ' + v).join(' &bull; ');

  // Phase B / B-COMPOSE
  const pb = d.phase_b || {};
  document.getElementById('phase-b-total').textContent = fmt(pb.total || 0);
  document.getElementById('phase-b-pct').textContent = pb.pct_complete || 0;
  document.getElementById('phase-b-remaining').textContent = fmt(pb.remaining || 0);
  document.getElementById('phase-b-bar').style.width = (pb.pct_complete || 0) + '%';
  const pbStatus = document.getElementById('phase-b-status');
  if (pb.total >= 5000) {
    pbStatus.textContent = 'Complete'; pbStatus.className = 'tag ok';
  } else if (pb.total > 0) {
    pbStatus.textContent = 'Running'; pbStatus.className = 'tag warn';
  } else {
    pbStatus.textContent = 'Not started'; pbStatus.className = 'tag';
  }

  // Phase C / C-EDG
  const pc = d.phase_c || {};
  document.getElementById('phase-c-total').textContent = fmt(pc.total || 0);
  document.getElementById('phase-c-pct').textContent = pc.pct_complete || 0;
  document.getElementById('phase-c-remaining').textContent = fmt(pc.remaining || 0);
  document.getElementById('phase-c-bar').style.width = (pc.pct_complete || 0) + '%';
  const pcStatus = document.getElementById('phase-c-status');
  if (pc.total >= 5000) {
    pcStatus.textContent = 'Complete'; pcStatus.className = 'tag ok';
  } else if (pc.total > 0) {
    pcStatus.textContent = 'Running'; pcStatus.className = 'tag warn';
  } else {
    pcStatus.textContent = 'Not started'; pcStatus.className = 'tag';
  }

  // Phase D / D-APP
  const pd = d.phase_d || {};
  document.getElementById('phase-d-total').textContent = fmt(pd.total || 0);
  document.getElementById('phase-d-pct').textContent = pd.pct_complete || 0;
  document.getElementById('phase-d-remaining').textContent = fmt(pd.remaining || 0);
  document.getElementById('phase-d-bar').style.width = (pd.pct_complete || 0) + '%';
  const pdStatus = document.getElementById('phase-d-status');
  if (pd.total >= 5000) {
    pdStatus.textContent = 'Complete'; pdStatus.className = 'tag ok';
  } else if (pd.total > 0) {
    pdStatus.textContent = 'Running'; pdStatus.className = 'tag warn';
  } else {
    pdStatus.textContent = 'Not started'; pdStatus.className = 'tag';
  }

  // Gate 1
  const g1 = d.gate1 || {};
  const sc = g1.scorecard || {};
  const scores = sc.scores || [];
  if (scores.length > 0) {
    const bestPass1 = Math.max(...scores.map(s => s.first_pass_compile_rate || 0));
    const bestComposite = Math.max(...scores.map(s => s.composite_score || 0));
    const providersPassed = scores.filter(s => s.passed).length;
    document.getElementById('g1-pass1').textContent = (bestPass1 * 100).toFixed(1) + '%';
    document.getElementById('g1-composite').textContent = bestComposite.toFixed(4);
    document.getElementById('g1-providers-passed').textContent = providersPassed + '/' + scores.length;
    document.getElementById('g1-trial-count').textContent = sc.trial_task_count || '--';
    const g1Status = document.getElementById('g1-status');
    if (bestPass1 >= 0.60 && providersPassed >= 1) {
      g1Status.textContent = 'PASS'; g1Status.className = 'tag ok';
    } else {
      g1Status.textContent = 'NOT YET'; g1Status.className = 'tag held';
    }
    // Provider table
    const g1Tbody = document.getElementById('g1-provider-tbody');
    g1Tbody.innerHTML = '';
    for (const s of scores) {
      const pTag = s.passed ? '<span class="tag ok">PASS</span>' : '<span class="tag held">FAIL</span>';
      g1Tbody.innerHTML += '<tr><td>' + s.provider_name + '</td><td>' +
        (s.first_pass_compile_rate * 100).toFixed(1) + '%</td><td>' +
        (s.correction_success_rate * 100).toFixed(1) + '%</td><td>' +
        (s.composite_score || 0).toFixed(4) + '</td><td>' + pTag + '</td></tr>';
    }
  } else {
    document.getElementById('g1-pass1').textContent = 'N/A';
    document.getElementById('g1-composite').textContent = 'N/A';
    document.getElementById('g1-providers-passed').textContent = 'N/A';
    document.getElementById('g1-trial-count').textContent = 'N/A';
    const g1Status = document.getElementById('g1-status');
    g1Status.textContent = 'NO DATA'; g1Status.className = 'tag';
  }

  // Overall timeline
  document.getElementById('tl-phase-a').textContent = fmt(d.corpus.total);
  document.getElementById('tl-phase-a-pct').textContent = d.corpus.pct_complete;
  document.getElementById('tl-phase-b').textContent = fmt(pb.total || 0);
  document.getElementById('tl-phase-b-pct').textContent = pb.pct_complete || 0;
  document.getElementById('tl-phase-c').textContent = fmt(pc.total || 0);
  document.getElementById('tl-phase-c-pct').textContent = pc.pct_complete || 0;
  document.getElementById('tl-phase-d').textContent = fmt(pd.total || 0);
  document.getElementById('tl-phase-d-pct').textContent = pd.pct_complete || 0;
  document.getElementById('tl-combined').textContent = fmt(d.combined_total || 0);
  document.getElementById('tl-bar-a').style.width = d.corpus.pct_complete + '%';
  document.getElementById('tl-bar-b').style.width = (pb.pct_complete || 0) + '%';
  document.getElementById('tl-bar-c').style.width = (pc.pct_complete || 0) + '%';
  document.getElementById('tl-bar-d').style.width = (pd.pct_complete || 0) + '%';

  // Held categories
  const hc = document.getElementById('held-content');
  const allCats = d.progress.per_category || {};
  const heldSet = new Set((d.held_categories || []).map(h => h.category));
  let catHtml = '';
  for (const [cat, s] of Object.entries(allCats).sort()) {
    const rate = s.dispatched > 0 ? (s.accepted / s.dispatched * 100).toFixed(0) : 0;
    const isHeld = heldSet.has(cat);
    catHtml += '<div style="margin-bottom:4px">' +
      '<span class="tag ' + (isHeld ? 'held' : 'ok') + '">' + cat +
      (isHeld ? ' HELD' : '') + '</span> ' +
      '<span class="sub">' + rate + '% (' + s.accepted + '/' + s.dispatched + ')</span></div>';
  }
  hc.innerHTML = catHtml || '<span class="sub">No categories tracked yet</span>';

  // Model table
  const mt = document.getElementById('model-tbody');
  const models = d.progress.per_model || {};
  mt.innerHTML = '';
  for (const [name, s] of Object.entries(models)) {
    if (name === 'pool') continue;
    const short = name.replace(/-20\d{2}.*$/, '');
    const rate = (s.accepted + s.failed) > 0
      ? (s.accepted / (s.accepted + s.failed) * 100).toFixed(1) : '0.0';
    const row = '<tr><td>' + short + '</td><td style="color:#3fb950">' + s.accepted +
      '</td><td style="color:#f85149">' + s.failed + '</td><td>' + rate +
      '%</td><td>$' + (s.cost || 0).toFixed(3) + '</td></tr>';
    mt.innerHTML += row;
  }

  // Category table
  const ct = document.getElementById('cat-tbody');
  ct.innerHTML = '';
  for (const [cat, s] of Object.entries(allCats).sort()) {
    const corpus = (d.corpus.by_category || {})[cat] || 0;
    const rate = s.dispatched > 0 ? (s.accepted / s.dispatched * 100).toFixed(1) : '0.0';
    const isHeld = heldSet.has(cat);
    const statusTag = isHeld ? '<span class="tag held">HELD</span>' : '<span class="tag ok">Active</span>';
    ct.innerHTML += '<tr><td>' + cat + '</td><td>' + corpus + '</td><td>' + s.dispatched +
      '</td><td style="color:#3fb950">' + s.accepted + '</td><td style="color:#f85149">' +
      s.failed + '</td><td>' + rate + '%</td><td>' + statusTag + '</td></tr>';
  }

  // Batch timing
  const ts = d.timeseries || {};
  document.getElementById('last-batch-time').textContent = ts.last_batch_secs || '--';
  document.getElementById('avg-batch-time').textContent = ts.avg_batch_secs || '--';
  document.getElementById('run-started').textContent = ts.first_ts || '--';

  // Timeline chart
  updateChart(ts);
}

function updateChart(ts) {
  const ctx = document.getElementById('timeline-chart');
  const labels = (ts.minutes || []).map(m => m.split(' ')[1] || m);

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = ts.accepted || [];
    chart.data.datasets[1].data = ts.failed || [];
    chart.data.datasets[2].data = ts.rescued || [];
    chart.data.datasets[3].data = ts.autofixed || [];
    chart.data.datasets[4].data = ts.corrections || [];
    chart.update('none');
    return;
  }

  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        { label: 'Accepted', data: ts.accepted || [], borderColor: '#3fb950', backgroundColor: '#3fb95030', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Failed', data: ts.failed || [], borderColor: '#f85149', backgroundColor: '#f8514930', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Rescued', data: ts.rescued || [], borderColor: '#a371f7', backgroundColor: '#a371f730', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Auto-fixed', data: ts.autofixed || [], borderColor: '#d29922', backgroundColor: '#d2992230', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1, borderDash: [2,2] },
        { label: 'Corrections', data: ts.corrections || [], borderColor: '#58a6ff', backgroundColor: '#58a6ff30', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1, borderDash: [4,4] },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { ticks: { color: '#484f58', maxTicksLimit: 20 }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#484f58' }, grid: { color: '#21262d' }, beginAtZero: true }
      },
      plugins: {
        legend: { labels: { color: '#8b949e', usePointStyle: true, pointStyle: 'line' } }
      }
    }
  });
}

async function refresh() {
  try {
    const r = await fetch('/api/metrics');
    const d = await r.json();
    updateDashboard(d);
  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/metrics":
            data = get_all_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_sanitize_for_json(data)).encode())
        elif self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logging


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"Dashboard running at https://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
