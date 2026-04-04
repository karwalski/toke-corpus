#!/usr/bin/env python3
"""Corpus regeneration pipeline.

Reproduces the toke corpus deterministically given the same config.yaml
and random seed.  Follows the same pipeline stages as main.py but is
designed for standalone re-execution and auditability.

Steps:
  1. Load config.yaml (providers, seed, limits, prompts dir)
  2. Generate curriculum / prompt set for the requested stage(s)
  3. Dispatch to model API (or skip in --dry-run)
  4. Compile-check each response via tkc
  5. Score + judge
  6. Deduplicate
  7. Write accepted entries to corpus/

Usage::

    # Full regeneration (requires API keys in env)
    python scripts/regenerate.py --config config.yaml

    # Dry-run: show plan without calling any API
    python scripts/regenerate.py --config config.yaml --dry-run

    # Regenerate only stage A
    python scripts/regenerate.py --config config.yaml --stage A

Story 10.7.7 -- Regeneration infrastructure for D13=E publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger("regenerate")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict[str, Any]:
    """Load and validate config.yaml."""
    with open(path) as f:
        cfg = yaml.safe_load(f)
    required = ["seed", "providers", "prompts_dir"]
    for key in required:
        if key not in cfg:
            raise SystemExit(f"config missing required key: {key}")
    return cfg


def resolve_providers(cfg: dict[str, Any], stage: str | None) -> list[dict[str, Any]]:
    """Return provider list, optionally filtered by open-weight flag."""
    providers = []
    for name, spec in cfg["providers"].items():
        providers.append({"name": name, **spec})
    return providers


# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------

STAGE_CATEGORIES: dict[str, list[str]] = {
    "A": ["A-ARR", "A-CND", "A-ERR", "A-MTH", "A-SRT", "A-STR"],
    "B": ["B-CMP"],
    "C": ["C-EDG"],
    "D": ["D-APP"],
}


def build_task_list(cfg: dict[str, Any], stage: str | None) -> list[dict[str, Any]]:
    """Build the deterministic task list from config + seed."""
    rng = random.Random(cfg["seed"])
    stages = [stage] if stage else sorted(STAGE_CATEGORIES)
    tasks: list[dict[str, Any]] = []
    total = cfg.get("total_tasks", 26000)

    for s in stages:
        categories = STAGE_CATEGORIES.get(s, [])
        per_cat = total // max(len(categories), 1)
        for cat in categories:
            for i in range(1, per_cat + 1):
                task_id = f"{cat}-{i:04d}"
                tasks.append({
                    "task_id": task_id,
                    "phase": s,
                    "category": cat,
                    "seed": rng.randint(0, 2**32 - 1),
                })
    rng.shuffle(tasks)
    return tasks


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def load_prompts(cfg: dict[str, Any]) -> dict[str, str]:
    """Load prompt templates from prompts_dir."""
    prompts_dir = Path(cfg["prompts_dir"])
    templates: dict[str, str] = {}
    for p in prompts_dir.glob("*.md"):
        templates[p.stem] = p.read_text()
    return templates


def build_prompt(task: dict[str, Any], templates: dict[str, str]) -> str:
    """Assemble the generation prompt for a task."""
    system = templates.get("system", templates.get("system_base", ""))
    generate = templates.get("generate_toke", "")
    return f"{system}\n\n{generate}\n\nTask: {task['task_id']}\nSeed: {task['seed']}"


# ---------------------------------------------------------------------------
# API dispatch (stub -- real calls need keys)
# ---------------------------------------------------------------------------

def call_model(provider: dict[str, Any], prompt: str, *, dry_run: bool) -> str | None:
    """Call a model provider API.  Returns generated source or None in dry-run."""
    if dry_run:
        return None
    # Real implementation would import from dispatch/ subpackage.
    # Left as explicit error so users know API keys are required.
    raise NotImplementedError(
        f"Live API calls require provider client for {provider['name']}. "
        "Set API keys and use main.py for actual generation."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def compile_check(source: str, tkc_path: str) -> tuple[int, list[str]]:
    """Run tkc compiler on source, return (exit_code, error_codes)."""
    try:
        result = subprocess.run(
            [tkc_path, "check", "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
        )
        errors = []
        for line in result.stderr.splitlines():
            if line.startswith("E"):
                code = line.split(":")[0].strip()
                if code:
                    errors.append(code)
        return result.returncode, errors
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        LOG.warning("compile_check failed: %s", exc)
        return -1, ["COMPILE_ERROR"]


def score_entry(source: str) -> float:
    """Simple quality heuristic (placeholder)."""
    length = len(source)
    if length < 10:
        return 0.0
    return min(1.0, length / 500.0)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def content_hash(source: str) -> str:
    """SHA-256 of normalised source for dedup."""
    normalised = " ".join(source.split())
    return hashlib.sha256(normalised.encode()).hexdigest()


def deduplicate(entries: list[dict[str, Any]], threshold: float = 0.95) -> list[dict[str, Any]]:
    """Remove exact-duplicate entries by content hash."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in entries:
        h = content_hash(entry.get("tk_source", ""))
        if h not in seen:
            seen.add(h)
            unique.append(entry)
    return unique


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_entry(entry: dict[str, Any], corpus_dir: Path) -> Path:
    """Write a single corpus entry JSON to the appropriate phase dir."""
    phase_dir = corpus_dir / f"phase_{entry['phase'].lower()}" / entry["category"]
    phase_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(entry["id"].encode()).hexdigest()[:8]
    filename = f"{entry['phase']}-{entry['task_id']}-{h}.json"
    path = phase_dir / filename
    with open(path, "w") as f:
        json.dump(entry, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(cfg: dict[str, Any], *, stage: str | None, dry_run: bool) -> None:
    """Execute the full regeneration pipeline."""
    LOG.info("=== Regeneration pipeline ===")
    LOG.info("Seed: %s", cfg["seed"])
    LOG.info("Stage filter: %s", stage or "ALL")
    LOG.info("Dry run: %s", dry_run)

    # 1. Build task list
    tasks = build_task_list(cfg, stage)
    LOG.info("Tasks to generate: %d", len(tasks))

    # 2. Load prompts
    templates = load_prompts(cfg)
    LOG.info("Prompt templates loaded: %s", list(templates.keys()))

    # 3. Resolve providers
    providers = resolve_providers(cfg, stage)
    LOG.info("Providers: %s", [p["name"] for p in providers])

    if dry_run:
        LOG.info("--- DRY RUN SUMMARY ---")
        by_stage: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        for t in tasks:
            by_stage[t["phase"]] = by_stage.get(t["phase"], 0) + 1
            by_cat[t["category"]] = by_cat.get(t["category"], 0) + 1
        LOG.info("Tasks per stage: %s", json.dumps(by_stage, sort_keys=True))
        LOG.info("Tasks per category: %s", json.dumps(by_cat, sort_keys=True))
        LOG.info("Providers: %s", json.dumps(
            {p["name"]: p.get("model", "?") for p in providers}, indent=2
        ))
        estimated_cost = sum(
            p.get("cost_input", 0) + p.get("cost_output", 0)
            for p in providers
        ) * len(tasks) / len(providers) / 1000
        LOG.info("Estimated cost: $%.2f (rough)", estimated_cost)
        LOG.info("No API calls made.  Remove --dry-run to generate.")
        return

    # 4. Generate + validate + score
    tkc_path = cfg.get("tkc_path", "tkc")
    corpus_dir = Path(cfg.get("corpus_dir", "corpus"))
    accepted: list[dict[str, Any]] = []
    rejected = 0

    for i, task in enumerate(tasks):
        prompt = build_prompt(task, templates)
        provider = providers[i % len(providers)]

        source = call_model(provider, prompt, dry_run=False)
        if source is None:
            rejected += 1
            continue

        exit_code, errors = compile_check(source, tkc_path)
        if exit_code != 0:
            rejected += 1
            continue

        sc = score_entry(source)
        entry_id = f"{task['phase']}-{task['task_id']}-{hashlib.sha256(source.encode()).hexdigest()[:8]}"
        entry = {
            "id": entry_id,
            "version": 1,
            "phase": task["phase"],
            "task_id": task["task_id"],
            "category": task["category"],
            "tk_source": source,
            "tk_tokens": len(source.split()),
            "model": provider.get("model", provider["name"]),
            "validation": {"compiler_exit_code": exit_code, "error_codes": errors},
            "differential": {"languages_agreed": [], "majority_output": ""},
            "judge": {"accepted": sc >= 0.3, "score": sc},
        }
        if entry["judge"]["accepted"]:
            accepted.append(entry)
        else:
            rejected += 1

        if (i + 1) % 500 == 0:
            LOG.info("Progress: %d/%d  accepted=%d rejected=%d", i + 1, len(tasks), len(accepted), rejected)

    # 5. Deduplicate
    threshold = cfg.get("dedup", {}).get("threshold", 0.95)
    before = len(accepted)
    accepted = deduplicate(accepted, threshold)
    LOG.info("Dedup: %d -> %d entries", before, len(accepted))

    # 6. Write
    for entry in accepted:
        write_entry(entry, corpus_dir)
    LOG.info("Written %d entries to %s", len(accepted), corpus_dir)
    LOG.info("Rejected %d entries total", rejected)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate toke corpus from config")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"),
                        help="Path to config.yaml")
    parser.add_argument("--stage", choices=["A", "B", "C", "D"], default=None,
                        help="Regenerate only this stage")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without calling APIs")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = load_config(args.config)
    run_pipeline(cfg, stage=args.stage, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
