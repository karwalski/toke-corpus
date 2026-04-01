# Proposal: Cloud-Based Corpus Phase A Generation

**Date:** 2026-03-29
**Author:** Matt Watt
**Status:** Draft
**Target:** 50,000 Phase A corpus programs in ≤5 days, cloud-only

---

## 1. Problem

The original plan assumes a Mac Studio M4 Max running Qwen 2.5 Coder 32B locally for 75% of generation, with Haiku API escalation for the remaining 25%. The Mac Studio is not yet available (blocking story 1.4+). Corpus Phase A is the critical path to Gate 1.

This proposal replaces the local Qwen + API hybrid with a **100% cloud API** approach running from a provisioned cloud compute instance, within budget and a five-day window.

---

## 2. Constraints

| Constraint | Value |
|------------|-------|
| Budget ceiling | ≤$500 API spend |
| Compute ceiling | ≤$100 instance cost (5–7 days runtime) |
| Wall-clock time | ≤5 days generation, 2 days QA buffer |
| Programs required | 50,000 toke + 50,000 each Python/C/Java (differential) |
| Infrastructure | Fresh AWS EC2 or Lightsail instance (provisioned for this job) |
| Compiler validation | `tkc` cross-compiled for Linux amd64; `gcc`, `python3`, `javac` installed |
| No GPU hardware | All LLM inference via API — no local model hosting |

---

## 3. Compute Instance Specification

The bottleneck is not API calls (those are async/batched) — it's the **validation pipeline**: compiling 200,000 programs (4 languages × 50K tasks) and running differential tests.

### Recommended Instance

| Spec | Value | Rationale |
|------|-------|-----------|
| **Type** | EC2 `c6a.2xlarge` or Lightsail 8GB | CPU-bound validation workload |
| **vCPUs** | 8 | Run 8 validation workers in parallel |
| **RAM** | 16 GB | Headroom for JVM (javac), parallel processes, corpus in memory |
| **Storage** | 80 GB SSD | Corpus output (~5 GB), compiler toolchains, logs |
| **OS** | Ubuntu 24.04 LTS | Standard, `apt` packages for gcc, python3, openjdk |
| **Estimated cost** | ~$0.15/hr × 168 hrs = **~$25/week** (on-demand) | Well under $100 ceiling |

### Software Stack

```
tkc                     (cross-compiled from tkc repo, Linux amd64)
gcc 13+                 (apt install build-essential)
python 3.11+            (apt install python3)
openjdk 21+             (apt install openjdk-21-jdk)
python3-pip + venv      (pipeline dependencies)
tmux                    (long-running orchestrator)
```

### Parallelism Model

```
Orchestrator (1 process)
├── API Dispatcher      (async — submits batch jobs, polls results)
├── Validator Pool      (8 workers — compile toke/C/Python/Java in parallel)
├── Diff Test Runner    (consumes validated quad-sets, runs majority vote)
├── Correction Loop     (resubmits failures to Tier 2 API)
└── Metrics Collector   (real-time cost, progress, quality tracking)
```

With 8 parallel workers and `tkc` compiling in <50ms, the validator can process **~160 tasks/minute** (each task = 4 compilations + test runs). 50,000 tasks = ~5.2 hours of pure validation time. The API dispatch and validation run concurrently, so the pipeline is effectively API-latency-bound on the first pass.

---

## 4. Multi-Model Pool Strategy

### 4.1 Philosophy

Phase A tasks are single-function primitives (5–50 lines). Many low-cost code models can handle these. Rather than picking one model upfront, **run a capability trial across a pool of candidates**, drop poor performers, and allocate the workload based on measured accuracy.

### 4.2 Model Pool (Candidates)

**Tier 1 — Low cost (candidates for 60% of generation)**

| Provider | Model | Est. Input/MTok | Est. Output/MTok | Notes |
|----------|-------|----------------|-----------------|-------|
| Google | Gemini 2.5 Flash | ~$0.15 | ~$0.60 | Context caching, high throughput |
| OpenAI | GPT-4.1 mini (batch) | ~$0.20 | ~$0.80 | 50% batch discount, 24hr turnaround |
| DeepSeek | DeepSeek V3 | ~$0.27 | ~$1.10 | Cache hits ~$0.07 input |
| Mistral | Codestral | ~$0.30 | ~$0.90 | Code-specialized |
| Fireworks | Qwen 2.5 Coder 32B | ~$0.80 | ~$0.80 | Open-weight via provider |

**Tier 2 — Higher capability (40% of generation + all escalations)**

| Provider | Model | Est. Input/MTok | Est. Output/MTok | Notes |
|----------|-------|----------------|-----------------|-------|
| Anthropic | Claude Haiku 4.5 (batch) | ~$0.40 | ~$2.00 | 50% batch discount, prompt caching |
| OpenAI | GPT-4.1 (batch) | ~$1.00 | ~$4.00 | 50% batch discount |
| Anthropic | Claude Sonnet 4.6 | ~$3.00 | ~$15.00 | Reserve for difficult escalations only |

### 4.3 Capability Trial (Day 1)

Before committing budget, run a **500-task trial** across all candidate models:

1. Generate 500 task specs from the curriculum (covering all 6 categories)
2. Send each task to **every** Tier 1 candidate + 2 Tier 2 candidates
3. Validate all responses: `tkc --check`, reference language compilation, differential test
4. Score each model on:

| Metric | Weight | Description |
|--------|--------|-------------|
| First-pass compile rate | 40% | % of toke programs that compile without error |
| Differential agreement | 30% | % where toke output matches majority vote |
| Correction success rate | 20% | % fixed within 3 retries (with error feedback) |
| Cost per accepted program | 10% | Total spend / accepted programs |

5. **Drop** any Tier 1 model scoring below 50% first-pass compile rate
6. **Rank** surviving models by composite score
7. **Allocate** workload proportionally: best Tier 1 models get more tasks

**Expected outcome:** 2–3 Tier 1 models survive the trial. The top performer gets 40–50% of Tier 1 allocation, the second gets 30–40%, the third gets the remainder.

### 4.4 Workload Split

| Pool | Share | Role | Correction |
|------|-------|------|------------|
| Tier 1 survivors | 60% (30,000 tasks) | Bulk generation of simpler categories | Failed tasks escalated to Tier 2 |
| Tier 2 primary | 30% (15,000 tasks) | Harder categories + guaranteed quality baseline | Retry with structured error feedback ×3 |
| Tier 2 escalation | 10% (5,000 tasks) | Tier 1 failures + difficult corrections | Final attempt with Sonnet if Haiku/GPT-4.1 fail |
| **Total** | **100%** | **50,000 accepted programs** | |

The 40% Tier 2 requirement (30% primary + 10% escalation) ensures the corpus has a quality backbone — not everything generated by the cheapest available model.

### 4.5 Category-Aware Routing

Not all categories are equal. Route based on expected difficulty:

| Category | Tasks | Tier 1 share | Tier 2 share | Rationale |
|----------|-------|-------------|-------------|-----------|
| A-MTH (math) | ~10,000 | 80% | 20% | Arithmetic is well-understood by all models |
| A-CND (conditionals) | ~8,000 | 70% | 30% | Boolean logic, straightforward |
| A-STR (strings) | ~8,000 | 60% | 40% | String ops vary by model capability |
| A-ARR (arrays) | ~8,000 | 60% | 40% | Array operations, moderate difficulty |
| A-SRT (sorting) | ~8,000 | 50% | 50% | Algorithms need stronger models |
| A-ERR (errors) | ~8,000 | 40% | 60% | Error propagation is toke-specific syntax |

These splits are initial estimates — adjusted after the Day 1 trial based on actual per-category scores.

---

## 5. Token Budget and Cost Estimate

### Per-task token profile

| Component | Input tokens | Output tokens |
|-----------|-------------|---------------|
| System prompt (toke spec + grammar + examples) | ~4,000 | — |
| Task description + category template | ~300 | — |
| toke generation | — | ~200 |
| Python generation | — | ~250 |
| C generation | — | ~300 |
| Java generation | — | ~350 |
| Test input generation | — | ~150 |
| **Total per task (5 calls)** | **~21,500** | **~1,250** |

With prompt caching (90% hit rate on the 4,000-token spec), effective input drops to ~6,300 tokens per task.

### Cost estimate (Scenario: Tiered with trial survivors)

| Component | Tasks | Provider | Input | Output | Subtotal |
|-----------|-------|----------|-------|--------|----------|
| Trial (Day 1) | 500 × 7 models | Mixed | $3 | $5 | **$8** |
| Tier 1 bulk | 30,000 | Gemini Flash + survivors | $12 | $24 | **$36** |
| Tier 2 primary | 15,000 | Haiku batch + GPT-4.1 batch | $10 | $35 | **$45** |
| Tier 2 escalation | 5,000 retries | Haiku batch | $4 | $15 | **$19** |
| Correction loops | ~3,000 retries | Haiku/Sonnet | $5 | $20 | **$25** |
| Embeddings (dedup) | 50,000 | text-embedding-3-small | — | — | **$1** |
| **API total** | | | | | **~$134** |
| Compute instance | 7 days | EC2 c6a.2xlarge | | | **~$25** |
| **Grand total** | | | | | **~$159** |

**Headroom:** Budget ceiling is $500 API + $100 compute. Estimate of ~$159 leaves significant margin for higher-than-expected correction rates or additional trial runs.

---

## 6. Timeline

| Day | Phase | Activity | Expected output |
|-----|-------|----------|-----------------|
| **0** | Setup | Provision instance, install toolchains, deploy pipeline, cross-compile `tkc` | Instance ready, 50K task curriculum generated |
| **1** | Trial | Run 500-task capability trial across all candidate models | Model scorecard, pool allocation decided |
| **2** | Batch 1 | Submit 25,000 tasks (Tier 1 + Tier 2 primary), validate returns | ~18,000 accepted programs |
| **3** | Batch 2 | Submit remaining 25,000 tasks + Tier 2 escalation of Batch 1 failures | ~18,000 accepted + ~4,000 escalation |
| **4** | Correction | Correction loops on all remaining failures, gap-filling | ~8,000 accepted |
| **5** | QA | Deduplication pass, holdout isolation check, schema validation, metrics report | ~2,000 final + full corpus QA |
| **6–7** | Buffer | Re-run any failed categories, final packaging, upload corpus | 50,000 validated entries |

### Throughput math

- **API dispatch:** Batch APIs (Anthropic, OpenAI) accept bulk submissions — no rate limit concern. Real-time APIs (Gemini, DeepSeek) at ~1,000 RPM = 200 tasks/min = 12,000 tasks/hour.
- **Validation:** 8 parallel workers × ~20 tasks/min = 160 tasks/min = 9,600 tasks/hour.
- **Bottleneck:** Batch API turnaround (up to 24h for Anthropic/OpenAI batch). The timeline accounts for this with staggered daily submissions.

---

## 7. Validation Pipeline

Every generated program passes through the full pipeline before acceptance. No shortcuts.

### Stage 1 — Compiler validation (automated, zero API cost)

```
tkc --check <program.tk>     → exit code 0 required
gcc -o ref_c <program.c>     → must compile
python3 -c <program.py>      → syntax check
javac <Program.java>          → must compile
```

### Stage 2 — Differential testing (automated, zero API cost)

- Run all 4 implementations against shared test inputs
- Majority vote (≥3 of 4 agree) determines ground truth
- toke disagrees with majority → correction loop
- All disagree → discard task (ambiguous description)

### Stage 3 — Quality scoring (automated, minimal API cost)

| Check | Type | Fail condition |
|-------|------|----------------|
| Compiler clean | Required | `tkc` exit code non-zero |
| Differential agreement | Required | <3 of 4 languages match |
| Output correctness | Required | toke output ≠ majority reference |
| Token efficiency | Scored | `tk_tokens > 2× python_tokens` → flag |
| Holdout isolation | Required | Task ID in `benchmark/hidden_tests/` |
| Embedding dedup | Required | Cosine similarity >0.95 with existing entry |

### Stage 4 — Correction loop (API cost)

Failed programs get structured feedback:

```
Your toke program for task {task_id} produced the following compiler error:
{diagnostic_json}

The correct output should be: {majority_output}

Rewrite the toke program to fix these errors. Here is the toke grammar reference:
{relevant_grammar_subset}
```

Max 3 attempts per task. If all 3 fail, escalate to next tier. If Tier 2 × 3 attempts also fails, discard the task and generate a replacement.

---

## 8. Architecture

```
Cloud Instance
├── corpus-orchestrator/
│   ├── main.py                 (entry point — orchestrates full pipeline)
│   ├── config.py               (API keys, model pool config, thresholds)
│   ├── curriculum.py           (task curriculum generator — 50K specs)
│   │
│   ├── trial/
│   │   ├── runner.py           (capability trial — 500 tasks × N models)
│   │   └── scorer.py           (score models, decide pool allocation)
│   │
│   ├── dispatch/
│   │   ├── pool.py             (model pool manager — routes tasks to providers)
│   │   ├── anthropic.py        (Claude batch API client)
│   │   ├── openai.py           (OpenAI batch API client)
│   │   ├── gemini.py           (Gemini API client)
│   │   ├── deepseek.py         (DeepSeek API client)
│   │   └── base.py             (abstract provider interface)
│   │
│   ├── validate/
│   │   ├── compiler.py         (tkc + gcc + python3 + javac)
│   │   ├── diff_test.py        (run + majority vote)
│   │   └── quality.py          (scoring, dedup, holdout check)
│   │
│   ├── correct/
│   │   ├── loop.py             (structured error feedback → retry)
│   │   └── escalate.py         (tier promotion logic)
│   │
│   ├── store/
│   │   ├── corpus.py           (write validated entries per schema.json)
│   │   └── metrics.py          (real-time dashboard: cost, progress, quality)
│   │
│   └── prompts/
│       ├── system.md           (toke spec excerpt for system prompt)
│       ├── generate.md         (generation prompt template)
│       ├── correct.md          (correction prompt template)
│       └── test_inputs.md      (test input generation template)
│
├── tkc                         (compiler binary — Linux amd64)
├── corpus/                     (output — JSON entries per schema)
├── logs/                       (per-task generation + validation logs)
└── metrics/                    (trial results, model scorecards, cost tracking)
```

---

## 9. Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| All Tier 1 models fail trial (<50% compile rate) | Low | High | Fall back to 100% Tier 2 — budget allows ~35K tasks at Tier 2 prices |
| Batch API turnaround >24h | Medium | Medium | Stagger submissions; use real-time APIs for urgent fills |
| High correction rate (>40% failures) | Medium | Medium | Budget has 3× headroom; shift allocation toward better-scoring models |
| toke-specific syntax is hard for all models | Medium | High | Improve system prompt with more examples; increase spec excerpt; trial will reveal this early |
| Cost overrun | Low | Medium | Real-time cost tracking with automatic pause at $400 |
| Instance failure | Low | Low | Corpus writes are append-only with checkpointing; restart from last checkpoint |
| API provider outage | Low | Low | Multi-provider by design; if one is down, redistribute to others |
| Differential test false negatives | Low | Low | Majority vote across 4 languages; ambiguous tasks discarded |

---

## 10. Comparison to Original Plan

| Dimension | Original (Mac Studio hybrid) | This proposal (cloud API) |
|-----------|------------------------------|--------------------------|
| Hardware | Mac Studio ($7,199) | EC2/Lightsail (~$25/week) |
| API cost | ~$85 | ~$134 |
| Total cost | ~$1,400 (incl hardware pro-rata) | ~$159 |
| Wall clock | ~2–4 weeks (25 tok/s local) | ~5 days |
| Blocking on | Mac Studio purchase & setup | Nothing — can start immediately |
| Local inference | 75% Qwen local | 0% (all API) |
| Model diversity | 1 local + 1 API | 5–7 candidates, best survivors used |
| Quality signal | Single model perspective | Multi-model consensus |

**Key insight:** Going 100% cloud adds ~$49 to the API cost while eliminating the Mac Studio dependency, cutting wall-clock time by 3–4×, and adding multi-model diversity that the original plan lacked.

---

## 11. Success Criteria

The corpus generation run is successful if:

- [ ] 50,000 entries pass full validation (compiler + differential + quality)
- [ ] All 6 Phase A categories are represented proportionally
- [ ] First-pass compile rate across the corpus is ≥60%
- [ ] Zero entries from `benchmark/hidden_tests/` appear in corpus
- [ ] Zero duplicate entries (cosine similarity >0.95)
- [ ] Total API spend ≤$500
- [ ] Total compute spend ≤$100
- [ ] Completed within 7 calendar days
- [ ] Corpus metadata conforms to `corpus/schema.json`
- [ ] Token efficiency metrics collected for all 50K entries

---

## 12. Decision Required

**Recommended:** Proceed with multi-model tiered approach. Estimated cost ~$159, timeline 5–7 days.

**Pre-requisites:**
1. Provision EC2 `c6a.2xlarge` (or equivalent Lightsail 8 vCPU / 16 GB)
2. Obtain API keys: Anthropic, OpenAI, Google AI Studio, DeepSeek (at minimum)
3. Cross-compile `tkc` for Linux amd64 (or build from source on instance)
4. Install toolchains: `gcc`, `python3`, `openjdk-21-jdk`
5. Deploy orchestrator code (Epic 8.1)

**Day 1 gate:** The 500-task capability trial determines whether to proceed. If no model achieves ≥40% first-pass toke compile rate, pause and improve the system prompt / spec excerpt before committing budget.
