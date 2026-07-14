"""Toke Phase 2 Corpus Dashboard.

Single-file Python HTTPS server with embedded HTML/JS/CSS.
Monitors Phase 2 corpus generation: JSONL datasets, prompt inventory,
pipeline status, source registry, quality metrics, and sprint progress.
No external dependencies — uses stdlib only + Chart.js via CDN.
"""

import http.server
import json
import math
import os
import re
import ssl
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PORT = 8443
CERT_FILE = Path("/opt/toke-corpus/dashboard.crt")
KEY_FILE = Path("/opt/toke-corpus/dashboard.key")
BASE_DIR = Path("/opt/toke-corpus")
DATA_DIR = BASE_DIR / "data"
CORPUS_DIR = BASE_DIR / "corpus"
METRICS_DIR = BASE_DIR / "metrics"
LOGS_DIR = BASE_DIR / "logs"
PROGRESS_FILE = METRICS_DIR / "progress.json"
REGISTRY_DIR = BASE_DIR / "registry"
TKC_BIN = BASE_DIR / "bin" / "tkc"

# JSONL corpus files in data/
JSONL_SOURCES = {
    "Phase A (original)": DATA_DIR / "corpus_default.jsonl",
    "Mutations": DATA_DIR / "corpus_mutations.jsonl",
    "Error triples": DATA_DIR / "corpus_error_triples.jsonl",
    "Grammar fuzz": DATA_DIR / "corpus_fuzzed.jsonl",
    "Parallel corpus": DATA_DIR / "parallel_corpus_expanded.jsonl",
    "API-generated": DATA_DIR / "corpus_api.jsonl",
    "OSS ingest": DATA_DIR / "corpus_oss.jsonl",
}

# Prompt directories
PROMPT_DIRS = {
    "domain_prompts": DATA_DIR / "domain_prompts",
    "prompts": DATA_DIR / "prompts",
    "interfaces": DATA_DIR / "interfaces",
    "body_prompts": DATA_DIR / "body_prompts",
    "companions": DATA_DIR / "companions",
    "companion_prompts": DATA_DIR / "companion_prompts",
}

# ── Caches ──────────────────────────────────────────────────────────────────

_jsonl_cache = {"ts": 0, "data": {}}
JSONL_CACHE_TTL = 300  # 5 minutes — JSONL files are multi-GB

_metrics_cache = {"ts": 0, "data": None}
METRICS_CACHE_TTL = 10  # seconds


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


# ── JSONL line counting (cached, background refresh) ───────────────────────

def _count_jsonl_lines(filepath):
    """Count lines in a JSONL file efficiently using wc -l."""
    if not filepath.exists():
        return 0
    try:
        result = subprocess.run(
            ["wc", "-l", str(filepath)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    # Fallback: count in Python (slower but reliable)
    try:
        count = 0
        with open(filepath, "rb") as f:
            for _ in f:
                count += 1
        return count
    except Exception:
        return 0


def _file_size_mb(filepath):
    """Get file size in MB."""
    try:
        return round(filepath.stat().st_size / (1024 * 1024), 1)
    except Exception:
        return 0


def get_phase2_corpus_stats():
    """Count JSONL lines in data/ files. Cached for 5 minutes."""
    now = time.time()
    if _jsonl_cache["data"] and now - _jsonl_cache["ts"] < JSONL_CACHE_TTL:
        return _jsonl_cache["data"]

    sources = {}
    total = 0
    for name, path in JSONL_SOURCES.items():
        count = _count_jsonl_lines(path)
        size = _file_size_mb(path)
        sources[name] = {"count": count, "size_mb": size, "path": str(path)}
        total += count

    data = {"total": total, "sources": sources}
    _jsonl_cache["data"] = data
    _jsonl_cache["ts"] = now
    return data


def _refresh_jsonl_cache():
    """Background thread to refresh JSONL line counts."""
    while True:
        time.sleep(JSONL_CACHE_TTL)
        try:
            get_phase2_corpus_stats()
        except Exception:
            pass


# ── Prompt inventory ────────────────────────────────────────────────────────

def get_prompt_inventory():
    """Count prompt files in each prompt directory."""
    inventory = {}
    total = 0
    for name, path in PROMPT_DIRS.items():
        if path.exists():
            count = sum(1 for f in path.rglob("*") if f.is_file())
        else:
            count = 0
        inventory[name] = count
        total += count
    return {"total": total, "by_directory": inventory}


# ── Pipeline inventory ──────────────────────────────────────────────────────

def get_pipeline_inventory():
    """List available generation pipelines and their status."""
    pipelines = {
        "transpiler": {
            "story": "9.2.2",
            "description": "Transpile from other languages to toke",
            "prompt_dir": "prompts",
            "status": "available",
        },
        "interface_first": {
            "story": "9.2.3",
            "description": "Interface-first generation with body fill",
            "prompt_dir": "interfaces",
            "status": "available",
        },
        "companion": {
            "story": "9.2.4",
            "description": "Companion-driven generation",
            "prompt_dir": "companion_prompts",
            "status": "available",
        },
        "domain_stratified": {
            "story": "9.3.2",
            "description": "Domain-stratified prompt generation",
            "prompt_dir": "domain_prompts",
            "status": "available",
        },
    }
    # Check which pipelines have prompts ready
    for name, info in pipelines.items():
        pdir = PROMPT_DIRS.get(info["prompt_dir"])
        if pdir and pdir.exists():
            count = sum(1 for f in pdir.iterdir() if f.is_file())
            info["prompt_count"] = count
        else:
            info["prompt_count"] = 0
            info["status"] = "no prompts"
    return pipelines


# ── API generation status ───────────────────────────────────────────────────

def get_api_generation_status():
    """Check API generation pipeline status from progress.json and logs."""
    status = {
        "running": False,
        "entries_generated": 0,
        "success_rate": 0,
        "cost": {},
    }
    try:
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
        status["entries_generated"] = progress.get("accepted", 0)
        dispatched = progress.get("dispatched", 0)
        if dispatched > 0:
            status["success_rate"] = round(
                progress.get("accepted", 0) / dispatched * 100, 1
            )
        status["cost"] = progress.get("cost", {})
    except Exception:
        pass

    # Check if generation process is running
    status["running"] = get_pipeline_status()
    return status


# ── Source registry ─────────────────────────────────────────────────────────

def get_registry_stats():
    """Read source registry stats."""
    stats = {
        "training_sources": 0,
        "eval_sources": 0,
        "split_training": 0,
        "split_eval": 0,
        "contamination_firewall": "unknown",
    }
    if not REGISTRY_DIR.exists():
        return stats

    # Count .json or .jsonl files in training/ and eval/
    train_dir = REGISTRY_DIR / "training"
    eval_dir = REGISTRY_DIR / "eval"

    if train_dir.exists():
        stats["training_sources"] = sum(
            1 for f in train_dir.iterdir() if f.is_file()
        )
    if eval_dir.exists():
        stats["eval_sources"] = sum(
            1 for f in eval_dir.iterdir() if f.is_file()
        )

    # Check for split manifest
    manifest = REGISTRY_DIR / "manifest.json"
    if manifest.exists():
        try:
            with open(manifest) as f:
                m = json.load(f)
            stats["split_training"] = m.get("training_count", 0)
            stats["split_eval"] = m.get("eval_count", 0)
            stats["contamination_firewall"] = m.get("firewall_status", "unknown")
        except Exception:
            pass

    # Check for contamination firewall config
    firewall = REGISTRY_DIR / "firewall.json"
    if firewall.exists():
        stats["contamination_firewall"] = "active"
    elif stats["contamination_firewall"] == "unknown":
        stats["contamination_firewall"] = "not configured"

    return stats


# ── Quality metrics ─────────────────────────────────────────────────────────

def get_quality_metrics():
    """Get tkc validation pass rates and token stats."""
    quality = {
        "validation": {},
        "token_distribution": {},
        "phase_distribution": {},
    }

    # Check for quality report files
    quality_dir = METRICS_DIR / "quality"
    if quality_dir.exists():
        for report_file in quality_dir.glob("*.json"):
            try:
                with open(report_file) as f:
                    data = json.load(f)
                name = report_file.stem
                quality["validation"][name] = {
                    "total": data.get("total", 0),
                    "passed": data.get("passed", 0),
                    "pass_rate": data.get("pass_rate", 0),
                }
            except Exception:
                pass

    # Try to read aggregate quality stats
    quality_file = METRICS_DIR / "quality.json"
    if quality_file.exists():
        try:
            with open(quality_file) as f:
                data = json.load(f)
            quality["validation"] = data.get("validation", quality["validation"])
            quality["token_distribution"] = data.get("token_distribution", {})
            quality["phase_distribution"] = data.get("phase_distribution", {})
        except Exception:
            pass

    return quality


# ── System stats ────────────────────────────────────────────────────────────

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
            stats["ram_pct"] = (
                round((total - avail) / total * 100, 1) if total else 0
            )
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


# ── Pipeline status ─────────────────────────────────────────────────────────

def get_pipeline_status():
    """Check if pipeline tmux session is running."""
    try:
        for cmd in [
            ["tmux", "list-sessions"],
            ["su", "-c", "tmux list-sessions", "ubuntu"],
        ]:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if "corpus" in result.stdout or "generate" in result.stdout:
                return True
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# ── Sprint progress ─────────────────────────────────────────────────────────

def get_sprint_progress():
    """Sprint status for Phase 2 epics."""
    return {
        "9.1": {
            "name": "Sprint 1: Phase 2 Bootstrap",
            "status": "done",
            "stories": {
                "9.1.1": {"name": "JSONL consolidation", "status": "done"},
                "9.1.2": {"name": "Source registry", "status": "done"},
                "9.1.3": {"name": "Dashboard rewrite", "status": "done"},
            },
        },
        "9.2": {
            "name": "Sprint 2: Generation Pipelines",
            "status": "in_progress",
            "stories": {
                "9.2.1": {"name": "Mutation engine", "status": "done"},
                "9.2.2": {"name": "Transpiler pipeline", "status": "done"},
                "9.2.3": {"name": "Interface-first gen", "status": "done"},
                "9.2.4": {"name": "Companion pipeline", "status": "done"},
            },
        },
        "9.3": {
            "name": "Sprint 3: Domain & Quality",
            "status": "in_progress",
            "stories": {
                "9.3.1": {"name": "Quality validation", "status": "in_progress"},
                "9.3.2": {"name": "Domain-stratified prompts", "status": "done"},
                "9.3.3": {"name": "Token distribution", "status": "not_started"},
            },
        },
        "9.4": {
            "name": "Sprint 4: OSS Ingest",
            "status": "not_started",
            "stories": {
                "9.4.1": {"name": "Exercism ingest", "status": "not_started"},
                "9.4.2": {"name": "Rosetta Code ingest", "status": "not_started"},
                "9.4.3": {"name": "Snippet library", "status": "not_started"},
            },
        },
    }


# ── Phase B (new corpus) counts ────────────────────────────────────────────

_phase_b_cache = {"ts": 0, "data": {}}
PHASE_B_CACHE_TTL = 15


def get_phase_b_stats():
    """Count Phase B corpus entries by category."""
    now = time.time()
    if _phase_b_cache["data"] and now - _phase_b_cache["ts"] < PHASE_B_CACHE_TTL:
        return _phase_b_cache["data"]

    phase_b_dir = CORPUS_DIR / "phase_b"
    by_category: dict[str, int] = {}
    total = 0

    if phase_b_dir.exists():
        for cat_dir in sorted(phase_b_dir.rglob("*")):
            if cat_dir.is_dir():
                count = sum(1 for f in cat_dir.iterdir() if f.is_file() and f.suffix == ".json")
                if count > 0:
                    by_category[cat_dir.name] = count
                    total += count

    data = {"total": total, "by_category": by_category}
    _phase_b_cache["data"] = data
    _phase_b_cache["ts"] = now
    return data


# ── Provider stats (from progress.json) ───────────────────────────────────

def get_provider_stats():
    """Extract per-provider API call counts, tokens, and costs."""
    providers: dict[str, dict] = {}
    try:
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)

        # Token data from cost.provider_tokens (keyed by provider name)
        cost_data = progress.get("cost", {})
        provider_tokens = cost_data.get("provider_tokens", {})

        # Model data from per_model (keyed by model name)
        for model_name, mm in progress.get("per_model", {}).items():
            if model_name == "pool":
                continue
            # Derive short provider name from model
            if "claude" in model_name or "haiku" in model_name:
                provider = "anthropic"
            elif "gpt" in model_name:
                provider = "openai"
            elif "deepseek" in model_name:
                provider = "deepseek"
            else:
                provider = model_name.split("-")[0]

            # Get token data from provider_tokens if available
            pt = provider_tokens.get(provider, {})

            providers[provider] = {
                "model": model_name,
                "api_calls": mm.get("accepted", 0) + mm.get("failed", 0),
                "accepted": mm.get("accepted", 0),
                "failed": mm.get("failed", 0),
                "input_tokens": pt.get("input_tokens", 0),
                "output_tokens": pt.get("output_tokens", 0),
                "cost": mm.get("cost", 0),
            }
    except Exception:
        pass
    return providers


# ── Legacy Phase 1 counts ──────────────────────────────────────────────────

def get_legacy_phase1_count():
    """Count legacy Phase 1 individual JSON files."""
    total = 0
    by_phase = {}
    for phase in ["phase_a", "phase_b", "phase_c", "phase_d"]:
        d = CORPUS_DIR / phase
        if d.exists():
            count = sum(1 for _ in d.rglob("*.json"))
        else:
            count = 0
        by_phase[phase] = count
        total += count
    return {"total": total, "by_phase": by_phase}


# ── Aggregate all metrics ──────────────────────────────────────────────────

def get_all_metrics():
    """Aggregate all metrics."""
    now = time.time()
    if _metrics_cache["data"] and now - _metrics_cache["ts"] < METRICS_CACHE_TTL:
        return _metrics_cache["data"]

    corpus = get_phase2_corpus_stats()
    prompts = get_prompt_inventory()
    pipelines = get_pipeline_inventory()
    api_status = get_api_generation_status()
    registry = get_registry_stats()
    quality = get_quality_metrics()
    system = get_system_stats()
    running = get_pipeline_status()
    sprints = get_sprint_progress()
    legacy = get_legacy_phase1_count()
    phase_b = get_phase_b_stats()
    provider_stats = get_provider_stats()

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_running": running,
        "corpus": corpus,
        "phase_b": phase_b,
        "provider_stats": provider_stats,
        "prompts": prompts,
        "pipelines": pipelines,
        "api_generation": api_status,
        "registry": registry,
        "quality": quality,
        "system": system,
        "sprints": sprints,
        "legacy_phase1": legacy,
    }

    _metrics_cache["data"] = data
    _metrics_cache["ts"] = now
    return data


# ── HTML Dashboard ──────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Toke Phase 2 Corpus Dashboard</title>
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
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; padding: 16px; }
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
  .bar-fill.purple { background: #a371f7; }
  .wide { grid-column: 1 / -1; }
  .chart-container { position: relative; height: 300px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 500; }
  td { color: #c9d1d9; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .tag.done { background: #3fb95022; color: #3fb950; }
  .tag.in_progress { background: #d2992222; color: #d29922; }
  .tag.not_started { background: #484f5822; color: #484f58; }
  .tag.active { background: #3fb95022; color: #3fb950; }
  .tag.ok { background: #3fb95022; color: #3fb950; }
  .tag.warn { background: #d2992222; color: #d29922; }
  .tag.error { background: #f8514922; color: #f85149; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .cols3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  .mini-stat { text-align: center; }
  .mini-stat .val { font-size: 24px; font-weight: 700; color: #f0f6fc; }
  .mini-stat .label { font-size: 11px; color: #8b949e; }
  .section-title { font-size: 14px; font-weight: 600; color: #58a6ff; text-transform: uppercase; letter-spacing: 1px; padding: 16px 16px 0 16px; margin-top: 8px; }
  .banner { background: linear-gradient(135deg, #161b22 0%, #1c2333 100%); border: 1px solid #30363d; border-radius: 8px; padding: 20px 24px; margin: 16px; }
  .banner h2 { font-size: 13px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 14px; }
  .banner .metric { font-size: 42px; }
  .source-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #21262d; }
  .source-row:last-child { border-bottom: none; }
  .source-name { font-size: 13px; color: #c9d1d9; }
  .source-count { font-size: 14px; font-weight: 600; color: #f0f6fc; }
  .source-size { font-size: 11px; color: #484f58; margin-left: 8px; }
  .sprint-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #21262d; }
  .sprint-row:last-child { border-bottom: none; }
  .story-list { padding-left: 16px; margin-top: 4px; }
  .story-item { display: flex; justify-content: space-between; align-items: center; padding: 3px 0; font-size: 12px; }
  #last-update { font-size: 11px; color: #484f58; }
</style>
</head>
<body>
<div class="header">
  <span class="status-dot" id="pipeline-dot"></span>
  <h1>Toke Phase 2 Corpus Dashboard</h1>
  <span id="last-update"></span>
</div>

<!-- Section 0: New Corpus Accepted -->
<div class="banner" style="border-left:4px solid #3fb950">
  <h2>New Corpus (Phase B) — Live Generation</h2>
  <div style="display:flex;gap:32px;align-items:baseline;flex-wrap:wrap">
    <div>
      <div class="metric" id="phase-b-total" style="color:#3fb950">--</div>
      <div class="sub">accepted programs</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap" id="phase-b-cats"></div>
  </div>
</div>

<!-- Provider Stats -->
<div class="section-title">API Provider Stats</div>
<div class="grid">
  <div class="card wide">
    <h2>Per-Provider Usage</h2>
    <table>
      <thead><tr><th>Provider</th><th>Model</th><th>API Calls</th><th>Accepted</th><th>Failed</th><th>Input Tokens</th><th>Output Tokens</th><th>Cost</th></tr></thead>
      <tbody id="provider-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Section 1: Phase 2 Corpus Overview -->
<div class="banner">
  <h2>Phase 2 Corpus Overview (All Sources)</h2>
  <div style="display:flex;gap:32px;align-items:baseline;flex-wrap:wrap">
    <div>
      <div class="metric" id="total-entries">--</div>
      <div class="sub">total entries across all sources</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div class="mini-stat"><div class="val" id="src-count">--</div><div class="label">Sources</div></div>
      <div class="mini-stat"><div class="val" id="total-size">--</div><div class="label">Total Size</div></div>
    </div>
  </div>
</div>

<div class="grid" style="padding-top:0">
  <!-- Source Breakdown -->
  <div class="card">
    <h2>Corpus Sources</h2>
    <div id="source-list"></div>
  </div>

  <!-- Corpus Chart -->
  <div class="card">
    <h2>Corpus Composition</h2>
    <div class="chart-container"><canvas id="corpus-chart"></canvas></div>
  </div>
</div>

<!-- Section 2: Generation Pipeline Status -->
<div class="section-title">Generation Pipeline Status</div>
<div class="grid">
  <!-- Pipeline Inventory -->
  <div class="card">
    <h2>Available Pipelines</h2>
    <table id="pipeline-table">
      <thead><tr><th>Pipeline</th><th>Story</th><th>Prompts</th><th>Status</th></tr></thead>
      <tbody id="pipeline-tbody"></tbody>
    </table>
  </div>

  <!-- Prompt Inventory -->
  <div class="card">
    <h2>Prompt Inventory</h2>
    <div class="metric-sm" id="prompt-total">--</div>
    <div class="sub">total prompt files ready</div>
    <div style="margin-top:12px" id="prompt-breakdown"></div>
  </div>

  <!-- API Generation Status -->
  <div class="card">
    <h2>API Generation</h2>
    <div style="margin-bottom:8px">
      Status: <span id="api-status" class="tag">--</span>
    </div>
    <div class="cols">
      <div class="mini-stat"><div class="val" id="api-generated">--</div><div class="label">Generated</div></div>
      <div class="mini-stat"><div class="val" id="api-success">--</div><div class="label">Success %</div></div>
    </div>
    <div style="margin-top:12px">
      <div class="sub">Cost: <strong id="api-cost" style="color:#f0f6fc">$--</strong></div>
      <div class="sub" id="api-cost-breakdown"></div>
    </div>
  </div>
</div>

<!-- Section 3: Source Registry -->
<div class="section-title">Source Registry</div>
<div class="grid">
  <div class="card">
    <h2>Registry Overview</h2>
    <div class="cols" style="margin-bottom:12px">
      <div class="mini-stat"><div class="val" id="reg-train">--</div><div class="label">Training Sources</div></div>
      <div class="mini-stat"><div class="val" id="reg-eval">--</div><div class="label">Eval Sources (Holdout)</div></div>
    </div>
    <div class="cols" style="margin-bottom:12px">
      <div class="mini-stat"><div class="val" id="reg-split-train">--</div><div class="label">Split: Training</div></div>
      <div class="mini-stat"><div class="val" id="reg-split-eval">--</div><div class="label">Split: Eval</div></div>
    </div>
    <div style="margin-top:8px">
      Contamination Firewall: <span id="reg-firewall" class="tag">--</span>
    </div>
  </div>

  <!-- Section 4: Quality Metrics -->
  <div class="card">
    <h2>Quality Metrics</h2>
    <div id="quality-validation"></div>
    <div style="margin-top:12px">
      <div class="sub"><strong style="color:#8b949e">Phase Distribution</strong></div>
      <div id="quality-phases" style="margin-top:4px"></div>
    </div>
    <div style="margin-top:12px">
      <div class="sub"><strong style="color:#8b949e">Token Distribution</strong></div>
      <div id="quality-tokens" style="margin-top:4px"></div>
    </div>
  </div>
</div>

<!-- Section 5: System Resources -->
<div class="section-title">System Resources</div>
<div class="grid">
  <div class="card">
    <h2>System</h2>
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
    <div class="sub" style="margin-top:12px">
      <span id="sys-detail"></span>
    </div>
  </div>

  <!-- Legacy Phase 1 -->
  <div class="card">
    <h2>Legacy Phase 1 (Individual JSON)</h2>
    <div class="metric-sm" id="legacy-total">--</div>
    <div class="sub">individual .json files in corpus/</div>
    <div style="margin-top:8px" id="legacy-breakdown"></div>
  </div>
</div>

<!-- Section 6: Sprint Progress -->
<div class="section-title">Sprint Progress</div>
<div class="grid">
  <div class="card wide">
    <h2>Phase 2 Epics</h2>
    <div id="sprint-content"></div>
  </div>
</div>

<script>
let corpusChart = null;

function fmt(n) { return (n || 0).toLocaleString(); }

function statusTag(status) {
  const labels = { done: 'Done', in_progress: 'In Progress', not_started: 'Not Started', available: 'Available', 'no prompts': 'No Prompts', active: 'Active', 'not configured': 'Not Configured', unknown: 'Unknown' };
  const cls = status === 'done' || status === 'available' || status === 'active' ? 'done' : status === 'in_progress' ? 'in_progress' : 'not_started';
  return '<span class="tag ' + cls + '">' + (labels[status] || status) + '</span>';
}

function updateDashboard(d) {
  // Pipeline status dot
  const dot = document.getElementById('pipeline-dot');
  dot.className = 'status-dot ' + (d.pipeline_running ? 'running' : 'stopped');
  document.getElementById('last-update').textContent = 'Updated: ' + new Date(d.timestamp).toLocaleTimeString();

  // ── Section 0: Phase B Live Corpus ──
  const pb = d.phase_b || {};
  document.getElementById('phase-b-total').textContent = fmt(pb.total);
  let pbCatHtml = '';
  for (const [cat, count] of Object.entries(pb.by_category || {})) {
    pbCatHtml += '<div class="mini-stat"><div class="val">' + fmt(count) + '</div><div class="label">' + cat + '</div></div>';
  }
  document.getElementById('phase-b-cats').innerHTML = pbCatHtml;

  // ── Provider Stats ──
  const provStats = d.provider_stats || {};
  const provTbody = document.getElementById('provider-tbody');
  provTbody.innerHTML = '';
  for (const [prov, info] of Object.entries(provStats)) {
    const inTok = info.input_tokens || 0;
    const outTok = info.output_tokens || 0;
    const inStr = inTok > 1000000 ? (inTok / 1000000).toFixed(1) + 'M' : inTok > 1000 ? (inTok / 1000).toFixed(0) + 'K' : fmt(inTok);
    const outStr = outTok > 1000000 ? (outTok / 1000000).toFixed(1) + 'M' : outTok > 1000 ? (outTok / 1000).toFixed(0) + 'K' : fmt(outTok);
    provTbody.innerHTML += '<tr><td style="color:#58a6ff;font-weight:600">' + prov + '</td><td class="sub">' + (info.model || '') + '</td><td>' + fmt(info.api_calls) + '</td><td style="color:#3fb950">' + fmt(info.accepted) + '</td><td style="color:#f85149">' + fmt(info.failed) + '</td><td>' + inStr + '</td><td>' + outStr + '</td><td style="color:#d29922">$' + (info.cost || 0).toFixed(4) + '</td></tr>';
  }

  // ── Section 1: Corpus Overview ──
  const corpus = d.corpus || {};
  const sources = corpus.sources || {};
  document.getElementById('total-entries').textContent = fmt(corpus.total);

  let activeSources = 0;
  let totalSizeMB = 0;
  let sourceHtml = '';
  const chartLabels = [];
  const chartData = [];
  const chartColors = ['#3fb950', '#a371f7', '#f85149', '#d29922', '#58a6ff', '#79c0ff', '#7ee787'];
  let colorIdx = 0;

  for (const [name, info] of Object.entries(sources)) {
    if (info.count > 0) activeSources++;
    totalSizeMB += info.size_mb || 0;
    sourceHtml += '<div class="source-row"><span class="source-name">' + name + '</span><span><span class="source-count">' + fmt(info.count) + '</span><span class="source-size">' + (info.size_mb || 0) + ' MB</span></span></div>';
    if (info.count > 0) {
      chartLabels.push(name);
      chartData.push(info.count);
    }
    colorIdx++;
  }

  document.getElementById('source-list').innerHTML = sourceHtml || '<span class="sub">No sources found</span>';
  document.getElementById('src-count').textContent = activeSources;
  let sizeStr = totalSizeMB > 1024 ? (totalSizeMB / 1024).toFixed(1) + ' GB' : totalSizeMB.toFixed(0) + ' MB';
  document.getElementById('total-size').textContent = sizeStr;

  // Corpus chart (doughnut)
  updateCorpusChart(chartLabels, chartData, chartColors);

  // ── Section 2: Pipeline Status ──
  const pipelines = d.pipelines || {};
  const ptbody = document.getElementById('pipeline-tbody');
  ptbody.innerHTML = '';
  for (const [name, info] of Object.entries(pipelines)) {
    ptbody.innerHTML += '<tr><td>' + name.replace(/_/g, ' ') + '</td><td>' + (info.story || '') + '</td><td>' + fmt(info.prompt_count) + '</td><td>' + statusTag(info.status) + '</td></tr>';
  }

  // Prompt inventory
  const prompts = d.prompts || {};
  document.getElementById('prompt-total').textContent = fmt(prompts.total);
  let promptHtml = '';
  for (const [dir, count] of Object.entries(prompts.by_directory || {})) {
    promptHtml += '<div class="source-row"><span class="source-name">' + dir + '</span><span class="source-count">' + fmt(count) + '</span></div>';
  }
  document.getElementById('prompt-breakdown').innerHTML = promptHtml;

  // API generation
  const api = d.api_generation || {};
  const apiStatus = document.getElementById('api-status');
  if (api.running) {
    apiStatus.textContent = 'Running'; apiStatus.className = 'tag done';
  } else {
    apiStatus.textContent = 'Stopped'; apiStatus.className = 'tag not_started';
  }
  document.getElementById('api-generated').textContent = fmt(api.entries_generated);
  document.getElementById('api-success').textContent = (api.success_rate || 0) + '%';
  const cost = api.cost || {};
  document.getElementById('api-cost').textContent = '$' + (cost.api_total || 0).toFixed(2);
  const bp = cost.by_provider || {};
  document.getElementById('api-cost-breakdown').innerHTML = Object.entries(bp)
    .map(function(e) { return e[0].split('-')[0] + ': $' + e[1].toFixed(3); }).join(' &bull; ');

  // ── Section 3: Source Registry ──
  const reg = d.registry || {};
  document.getElementById('reg-train').textContent = fmt(reg.training_sources);
  document.getElementById('reg-eval').textContent = fmt(reg.eval_sources);
  document.getElementById('reg-split-train').textContent = fmt(reg.split_training);
  document.getElementById('reg-split-eval').textContent = fmt(reg.split_eval);
  const fw = document.getElementById('reg-firewall');
  fw.textContent = reg.contamination_firewall || 'unknown';
  fw.className = 'tag ' + (reg.contamination_firewall === 'active' ? 'done' : 'warn');

  // ── Section 4: Quality Metrics ──
  const quality = d.quality || {};
  const validation = quality.validation || {};
  let valHtml = '';
  if (Object.keys(validation).length > 0) {
    for (const [name, stats] of Object.entries(validation)) {
      const rate = stats.pass_rate || (stats.total > 0 ? (stats.passed / stats.total * 100).toFixed(1) : 0);
      const rateClass = rate >= 90 ? 'green' : rate >= 70 ? 'yellow' : 'red';
      valHtml += '<div style="margin-bottom:6px"><span class="sub">' + name + ':</span> <strong style="color:#f0f6fc">' + rate + '%</strong> <span class="sub">(' + fmt(stats.passed) + ' / ' + fmt(stats.total) + ')</span><div class="bar"><div class="bar-fill ' + rateClass + '" style="width:' + Math.min(rate, 100) + '%"></div></div></div>';
    }
  } else {
    valHtml = '<span class="sub">No validation data yet. Run tkc validate to generate.</span>';
  }
  document.getElementById('quality-validation').innerHTML = valHtml;

  const phases = quality.phase_distribution || {};
  let phaseHtml = '';
  for (const [p, count] of Object.entries(phases)) {
    phaseHtml += '<span class="tag ok" style="margin-right:4px">' + p + ': ' + fmt(count) + '</span>';
  }
  document.getElementById('quality-phases').innerHTML = phaseHtml || '<span class="sub">--</span>';

  const tokens = quality.token_distribution || {};
  let tokenHtml = '';
  for (const [bucket, count] of Object.entries(tokens)) {
    tokenHtml += '<span class="tag warn" style="margin-right:4px">' + bucket + ': ' + fmt(count) + '</span>';
  }
  document.getElementById('quality-tokens').innerHTML = tokenHtml || '<span class="sub">--</span>';

  // ── Section 5: System Resources ──
  const sys = d.system || {};
  document.getElementById('cpu-val').textContent = sys.cpu_pct + '%';
  document.getElementById('cpu-bar').style.width = Math.min(sys.cpu_pct || 0, 100) + '%';
  document.getElementById('ram-val').textContent = sys.ram_pct + '%';
  document.getElementById('ram-bar').style.width = (sys.ram_pct || 0) + '%';
  document.getElementById('disk-val').textContent = sys.disk_pct + '%';
  document.getElementById('disk-bar').style.width = (sys.disk_pct || 0) + '%';
  document.getElementById('sys-detail').textContent =
    'CPU: ' + (sys.cpu_count || '?') + ' cores, load ' + (sys.cpu_load_1m || 0) +
    ' | RAM: ' + (sys.ram_used_gb || 0) + '/' + (sys.ram_total_gb || 0) + ' GB' +
    ' | Disk: ' + (sys.disk_used_gb || 0) + '/' + (sys.disk_total_gb || 0) + ' GB';

  // Legacy Phase 1
  const legacy = d.legacy_phase1 || {};
  document.getElementById('legacy-total').textContent = fmt(legacy.total);
  let legacyHtml = '';
  for (const [phase, count] of Object.entries(legacy.by_phase || {})) {
    legacyHtml += '<div class="source-row"><span class="source-name">' + phase + '</span><span class="source-count">' + fmt(count) + '</span></div>';
  }
  document.getElementById('legacy-breakdown').innerHTML = legacyHtml;

  // ── Section 6: Sprint Progress ──
  const sprints = d.sprints || {};
  let sprintHtml = '';
  for (const [epic, info] of Object.entries(sprints)) {
    sprintHtml += '<div class="sprint-row"><div><strong style="color:#f0f6fc">Epic ' + epic + '</strong> <span class="sub">' + info.name + '</span></div>' + statusTag(info.status) + '</div>';
    sprintHtml += '<div class="story-list">';
    for (const [sid, story] of Object.entries(info.stories || {})) {
      sprintHtml += '<div class="story-item"><span>' + sid + ': ' + story.name + '</span>' + statusTag(story.status) + '</div>';
    }
    sprintHtml += '</div>';
  }
  document.getElementById('sprint-content').innerHTML = sprintHtml;
}

function updateCorpusChart(labels, data, colors) {
  const ctx = document.getElementById('corpus-chart');
  if (corpusChart) {
    corpusChart.data.labels = labels;
    corpusChart.data.datasets[0].data = data;
    corpusChart.data.datasets[0].backgroundColor = colors.slice(0, labels.length);
    corpusChart.update('none');
    return;
  }
  corpusChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data,
        backgroundColor: colors.slice(0, labels.length),
        borderColor: '#161b22',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: { color: '#8b949e', font: { size: 11 }, padding: 8, usePointStyle: true, pointStyle: 'circle' }
        },
        tooltip: {
          callbacks: {
            label: function(ctx) {
              const total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
              const pct = (ctx.parsed / total * 100).toFixed(1);
              return ctx.label + ': ' + ctx.parsed.toLocaleString() + ' (' + pct + '%)';
            }
          }
        }
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


# ── HTTP Server ─────────────────────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/metrics":
            data = get_all_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_sanitize_for_json(data)).encode())
        elif self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
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
    # Pre-warm the JSONL cache on startup
    print("Pre-warming JSONL line count cache (this may take a moment)...")
    get_phase2_corpus_stats()
    print("JSONL cache warm.")

    # Start background cache refresh thread
    t = threading.Thread(target=_refresh_jsonl_cache, daemon=True)
    t.start()

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
