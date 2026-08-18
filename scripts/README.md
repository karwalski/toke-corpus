# Scripts Directory

Pipeline scripts for corpus generation, validation, transformation, and training data preparation.

## Core Pipeline (active, used for training data generation)

| Script | Purpose |
|--------|---------|
| `prepare_training_data.py` | **Primary** — assembles train.jsonl/eval.jsonl from the deduplicated corpus with quality gates, source weighting, and chat-format output |
| `compile_check_corpus.py` | Runs `tkc --check` on all corpus records, writes results to each record |
| `phase2_syntax_audit.py` | Scans corpus for Phase 2 syntax violations (uppercase, camelCase, etc.) |
| `phase2_autofix_v2.py` | Autofixes `str`→`$str` and match-arm casing issues |
| `canonicalise_tk_source.py` | Strips newlines/comments from tk_source (string-aware) |
| `classify_complexity_dedup.py` | Classifies all records into simple/medium/complex/application tiers |
| `quality_score_corpus.py` | Scores each record on coherence, complexity, stdlib usage, error handling |
| `deduplicate_corpus.py` | Structural hash deduplication (197K→188K) |
| `validate_schema.py` | Migrates/validates records against schema v2 |
| `generate_manifest.py` | Generates MANIFEST.jsonl joining corpus with scores and complexity |
| `extract_imports.py` | Extracts imported_modules and flags unresolved imports |
| `curate_tokenizer_set.py` | Curates mutation-free programs for tokenizer training |
| `validate_training_format.py` | Validates chat-format training data (role structure, surface checks) |

## Corpus Generation (used during corpus expansion, not routine)

| Script | Purpose |
|--------|---------|
| `bifi_bootstrap.py` | Bootstrap Iterative Fix — generates error-repair triplets |
| `validate_error_triples.py` | Validates error triple records |
| `package_error_triplets.py` | Packages error triples for instruction tuning |
| `expand_compositions.py` | Generates multi-function composition programs |
| `generate_from_seeds.py` | Generates programs from seed task specifications |
| `run_oss_instruct_local.py` | OSS-Instruct generation using local models |
| `stdlib_knowledge_graph.py` | Builds stdlib API usage graph |
| `run_mutations.py` / `validate_mutations*.py` | Mutation generation and validation |
| `run_fuzzer.py` / `validate_fuzzed.py` | Grammar fuzzing |
| `run_*_gen.py` / `run_*_pipeline.py` | Various generation pipelines |
| `ec2_generate_all.py` | EC2-based batch generation |

## Analysis and Audit

| Script | Purpose |
|--------|---------|
| `audit_phase2_syntax.py` | Full audit of Phase 2 syntax across corpus, task specs, benchmarks |
| `disinfect_task_specs.py` | Removes Phase 1 contamination from task specs |
| `runtime_check_corpus.py` | Compiles and runs corpus programs, captures output |
| `judge_runtime_output.py` | LLM-judged output correctness |
| `compute_hashes.py` | SHA-256 hashes and Merkle root for integrity |
| `extract_clean.py` | Extracts open-weight-only subset |
| `license_check.py` | License compliance checking |

## Transformation

| Script | Purpose |
|--------|---------|
| `convert_all_to_phase2.py` | Converts corpus from Phase 1 to Phase 2 syntax |
| `phase2_revert_match_arm_sigils.py` | Reverts incorrect match-arm sigil autofix |
| `classify_complexity.py` | Original complexity classifier (superseded by `classify_complexity_dedup.py`) |

## Training and Evaluation Support

| Script | Purpose |
|--------|---------|
| `compiler_reward.py` | Compiler-as-verifier reward model for RL |
| `negative_examples.py` | Generates broken programs for contrastive training |
| `parallel_corpus.py` / `parallel_expand.py` | Python-toke parallel corpus |
| `translation_finetune.py` / `eval_translation.py` | Translation fine-tuning data prep |
| `ifd_selection.py` | IFD-based data selection |
| `dynamic_packing.py` | Dynamic packing for context window utilization |
| `tarot_curriculum.py` | TAROT-style test-driven curriculum |
| `evol_instruct.py` | Evol-Instruct complexity escalation |
| `reverse_oss_instruct.py` | Reverse OSS-Instruct (corpus→problem descriptions) |
| `execution_feedback.py` | Execution feedback annotation |
| `differential_sweep.py` | Differential testing sweep |
