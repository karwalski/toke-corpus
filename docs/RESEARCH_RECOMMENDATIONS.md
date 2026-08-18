# Training corpus overhaul for toke's Gate 2

**The toke project's 197K corpus is fundamentally misconfigured for QLoRA fine-tuning.** Research overwhelmingly shows that 76.5% near-duplicate mutations from only ~8,800 seed programs will cause memorization of narrow patterns rather than generalizable code generation — and that a smaller, more diverse corpus of **15K–30K high-quality examples** would likely produce a superior model. The near-zero error handling coverage (4/197K), low stdlib usage (3.3%), and 93% single-function composition each represent additional gaps that research identifies as critical to address. This report synthesizes findings from over 30 papers to provide specific, research-backed recommendations for corpus restructuring.

## 150K mutations will teach the model to memorize, not generalize

The most consequential finding for the toke project is that **QLoRA has inherently limited capacity to absorb new information**, making data diversity far more valuable than data volume. The paper "LoRA Learns Less and Forgets Less" (Biderman et al., TMLR 2024) demonstrated that LoRA adapters learn perturbations with rank **10–100x lower** than full fine-tuning, creating a hard ceiling on how much new information the adapter can encode. Filling that limited capacity with 150K near-duplicates of ~8,800 seeds is equivalent to training on each unique program ~17 times with trivial variations — a strategy the literature strongly condemns.

Three landmark papers converge on this conclusion. The LIMA study (Zhou et al., NeurIPS 2023) showed that **1,000 carefully curated examples** fine-tuned LLaMA-65B to be preferred over GPT-4 in 43% of human evaluations, and that "scaling up quantity without scaling diversity shows vastly diminishing returns." The QLoRA paper itself (Dettmers et al., NeurIPS 2023) demonstrated that Guanaco 65B achieved **99.3% of ChatGPT performance** using only ~9,800 examples. Most directly relevant, the LeetCodeDataset paper (April 2025) fine-tuned the exact same base model — **Qwen 2.5 Coder 7B** — on just **2,600 model-generated LeetCode solutions** and achieved **79.9% pass@1 on HumanEval**, outperforming models trained on datasets of 9.5K to 111K samples. The key condition: those 2,600 examples were diverse, verified, and high-quality.

The "Data Repetition Beats Data Scaling in Long-CoT SFT" study (Kopiczko et al., 2025) adds nuance — it found that 400 samples trained for 128 epochs outperformed 51,200 samples trained for 1 epoch by **12–26 percentage points**. This suggests that multi-epoch training on truly unique data is highly effective. The practical implication for toke: **aggressively deduplicate to ~8,800–15,000 unique examples, then train for 3–4 epochs** rather than single-pass training on 150K near-duplicates.

## Near-duplicate mutations are the single biggest corpus problem

Research on deduplication is unambiguous: semantic near-duplicates — which is precisely what variable renames and constant perturbations produce — harm model quality. "Deduplicating Training Data Makes Language Models Better" (Lee et al., ACL 2022) found that deduplication allows models to **emit memorized text 10x less frequently** while achieving the same or better accuracy in fewer training steps. SemDeDup (Abbas et al., ICLR 2023) showed that **removing 50% of semantically similar data** caused minimal performance loss while improving out-of-distribution generalization. Most damning for the toke corpus, "The Adverse Effects of Code Duplication in ML Models of Code" (Allamanis, 2019) found that performance metrics were **inflated by up to 100%** when training on duplicated code corpora — with "different versions of same file, configuration files differing in values" cited as canonical near-duplicate types that are directly analogous to toke's variable-rename and constant-perturbation mutations.

The mechanism of harm is well understood. Hernandez et al. (Anthropic, 2022) demonstrated that sequences present just **10 times** in training data are generated **~1,000x more often** than sequences present once — a superlinear memorization effect. With toke's average duplication of ~22x per seed, the model would massively overweight the structural patterns of those 8,800 seeds while failing to learn generalizable toke code generation. The LoRA capacity bottleneck amplifies this: the adapter's limited rank means every parameter wasted on memorizing mutation patterns is a parameter unavailable for learning useful abstractions.

**Recommended deduplication strategy:** Reduce to ~15,000–25,000 examples by keeping each seed program plus at most 1–2 structurally distinct variants (not simple variable renames). Train for 2–4 epochs on this clean corpus. This approach is supported by Muennighoff et al. (NeurIPS 2023), who found that up to 4 epochs of repeated unique data yields results comparable to fresh data, with diminishing returns beyond ~15 repetitions.

| Approach | Corpus size | Expected outcome |
|----------|------------|-----------------|
| Current plan (150K with 76.5% mutations) | ~150K | Memorization of ~8,800 patterns; inflated metrics; poor generalization |
| Moderate dedup (1–2 variants per seed) | ~15K–25K | Better diversity; reduced memorization; stronger generalization |
| Aggressive dedup + multi-epoch | ~8,800 x 3–4 epochs | Highest diversity per example; proven effective in recent studies |
| **Recommended balanced approach** | **~20K after dedup, 2–3 epochs** | **Sweet spot balancing diversity, coverage, and training signal** |

## Grammar-fuzzed data should be removed entirely

No published research supports using syntactically valid but semantically random code as training data for code generation models. The evidence uniformly points to harm. The Phi-1 "Textbooks Are All You Need" paper (Gunasekar et al., 2023) demonstrated that **"textbook quality" data — clear, self-contained, logically structured code** — dramatically outperforms raw code, with a 1.3B model achieving 50.6% on HumanEval using filtered data versus far worse performance on unfiltered data of 5x the volume. OpenCoder's filtering guidelines (Huang et al., ACL 2025) explicitly **remove** "files with poor or minimal logical structure." The "Quality In, Quality Out" study (2025) empirically showed that low-quality code in training data directly produces low-quality generated code.

Grammar-fuzzed data would teach the model that "valid toke code" includes semantic nonsense — random identifiers, incoherent control flow, function signatures with meaningless parameter types. Research on code LLM behavior (EquiBench, 2025) shows models "often rely on syntactic similarity rather than exhibiting robust reasoning about program semantics," meaning fuzz-trained models would learn to produce plausible-looking but functionally broken programs. **Every grammar-fuzzed example in the corpus should be replaced with a semantically meaningful program.**

The recommended alternative for generating diverse toke programs is **OSS-Instruct** (Wei et al., ICML 2024), where existing toke code snippets serve as seeds for LLM-generated programming problems and solutions. MagicoderS achieved **76.8% pass@1 on HumanEval** using this approach, surpassing GPT-3.5-turbo. For toke specifically: feed 50–200 high-quality hand-written toke programs to a strong LLM (GPT-4, Claude) as seeds, generate diverse problems and solutions, then validate all outputs through the toke compiler.

## Four critical gaps demand targeted data augmentation

Beyond the mutation and fuzzing problems, research identifies four specific coverage gaps that must be addressed:

**Error handling (currently 0.002% -> target 3–5%).** With 4 examples out of 197K, the model will learn nothing about error handling. Research on imbalanced fine-tuning data establishes a minimum of **500–2,000 examples** for basic pattern learning, with 1,000–5,000 recommended for complex code generation patterns. The most effective generation strategy is the **Break-It-Fix-It (BIFI) approach** (Yasunaga et al., ICML 2021): systematically introduce errors into correct toke programs, use the toke compiler as a critic to verify fixes, and iteratively bootstrap a breaker-fixer training loop. Replit's Code Repair LLM (2024) validated this at scale by fine-tuning DeepSeek-Coder 7B on ~100K synthetic (code, diagnostic, fix) triples. Each error handling example should follow the triplet format: `(broken_code, diagnostic_message, fixed_code)`. Target **1,000–3,000 error handling examples** covering missing error checks, incorrect error types, unhandled edge cases, and recovery flows.

**Standard library usage (currently 3.3% -> target 15–25%).** CloudAPIBench (AWS, 2024) found that APIs with **<=10 training occurrences** suffer catastrophic hallucination rates — GPT-4o achieved only **38.58% valid invocations** for low-frequency APIs versus dramatically better performance for those with >=100 occurrences. At 3.3% stdlib coverage across ~20K deduplicated examples, each stdlib function likely appears fewer than 10 times, well below the reliability threshold. APIKG4SYN's study of HarmonyOS — a close analog to toke as a new, low-resource framework — showed that even targeted API fine-tuning only reached **25% pass@1**, underscoring how difficult API learning is. Build an API knowledge graph from toke's stdlib documentation and generate diverse usage examples ensuring **each stdlib function appears in >=20 examples** (ideally 50+).

**Program complexity (currently 93% single-function -> target 75–80%).** While single-function programs are the backbone of most code benchmarks, research on Evol-Instruct shows that systematically increasing complexity teaches edge case handling and compositional reasoning. The recommended distribution: **40–50% simple single-function**, **25–30% medium single-function** with multi-step logic and error handling, **10–15% multi-function** programs demonstrating composition and decomposition, and **5–10% multi-class/module** programs showing design patterns. The data-efficient fine-tuning study (2025) found that strategies focused solely on complex data "fail to yield the best performance, as they neglect simpler yet impactful samples" — both ends of the complexity spectrum are necessary.

**Verified correctness (target 100%).** The LeetCodeDataset paper revealed a striking finding: model-generated solutions outperformed human-written ones for SFT on Qwen 2.5 Coder 7B (**79.9% vs. 55.5% on HumanEval**), despite both being correct. KodCode (ACL 2025 Best Paper) achieved state-of-the-art results with <2.5% error rates through pytest-based verification. Every toke training example should be compilable and executable. Run the entire corpus through the toke compiler and remove or fix any failing examples.

## Data mixing and ordering provide modest but real gains

Research on curriculum learning shows **inconsistent but occasionally meaningful benefits** of 1–5% accuracy improvement. "When Do Curricula Work?" (Wu et al., NeurIPS 2020) found that structured ordering helps mainly with limited training budgets and noisy data — both conditions that apply to toke. The practical recommendation is a **two-stage approach** inspired by OpenCoder: Stage 1 trains on broader, more diverse data (including LLM-generated and transpiled examples), and Stage 2 trains exclusively on high-quality, hand-written toke programs. This consistently outperforms single-stage mixed training.

For weighting different data sources, the **Golden Ratio weighting study** (2025) established that when mixing real and synthetic data, the optimal weight for real data is approximately **0.618** (the reciprocal of the golden ratio), and that naive equal mixing is always suboptimal. DoReMi (Xie et al., NeurIPS 2023) demonstrated that optimized data mixing improved average few-shot accuracy by **6.5 percentage points** and reached baseline performance **2.6x faster**. For toke, the recommended source weighting is:

- **Hand-written toke programs**: ~45% of effective training weight (highest quality, closest to target distribution)
- **LLM-generated toke programs**: ~30% (high utility when seeded with real code; use multiple LLMs for diversity)
- **Transpiled programs**: ~15% (useful for coverage but may have artifacts)
- **Curated mutations** (kept after dedup): ~10% (limited augmentation value only)

## Train the tokenizer on seed programs only

BPE tokenizer training is a statistical process that learns merge rules from character-pair frequencies. Training on the mutation-heavy corpus would **poison the vocabulary** with subword units optimized for renamed variables and perturbed constants rather than toke's natural syntax. Hayase et al. (NeurIPS 2024) confirmed that BPE merge lists directly reflect training data frequency distributions, and BoundlessBPE (Schmidt et al., 2025) measured **9% Renyi efficiency degradation** from training-to-evaluation distribution shift even under normal conditions — an effect that would be dramatically amplified by mutation-dominated training data.

**Train the SentencePiece BPE tokenizer exclusively on the ~8,800 seed programs.** This captures toke's natural statistical structure without mutation bias. Set `character_coverage` to **1.0** (all characters matter in code), target a vocabulary of **16,000–32,000 tokens** for a single language, and measure quality via fertility (<1.5 tokens per "word" on held-out toke code). Critically, Dagan et al. (ICML 2024) showed that extending an existing tokenizer rather than replacing it entirely preserves pre-trained embeddings — consider **extending Qwen's 151K-token vocabulary** with toke-specific tokens rather than training from scratch, which would preserve the base model's code understanding while adding toke awareness.

## Recommended corpus composition for Gate 2

Based on the full body of research, here is the specific recommended corpus restructuring:

| Component | Current state | Recommended target | Rationale |
|-----------|--------------|-------------------|-----------|
| **Total corpus size** | 197K (targeting 150K) | **18K–25K unique examples, trained 2–3 epochs** | QLoRA capacity limits; quality >> quantity |
| **Mutations** | 76.5% (~151K) | **<=15% (~3K), only structurally diverse variants** | Near-duplicates cause memorization, not learning |
| **Grammar-fuzzed programs** | Unknown % | **0% (remove entirely)** | Harmful; teaches generation of semantic nonsense |
| **Error handling examples** | 4 total (0.002%) | **1,000–3,000 (5–12%)** | Minimum viable coverage for pattern learning |
| **Stdlib-using programs** | 3.3% | **15–25%** with each function >=20 examples | CloudAPIBench frequency thresholds |
| **Multi-function programs** | ~7% | **20–25%** | Enables compositional code generation |
| **Hand-written programs** | Unknown | **>=45% of training weight** | Highest quality anchor for the distribution |
| **LLM-generated programs** | Unknown | **~30% via OSS-Instruct** | Proven most effective synthetic method |
| **Tokenizer training data** | Full corpus | **Seed programs only (~8,800)** | Prevents vocabulary poisoning from mutations |

## Conclusion

The research consensus is clear: **the toke corpus needs fewer, better examples — not more mutations of the same programs.** The LeetCodeDataset result on the identical Qwen 2.5 Coder 7B base model proves that 2,600 high-quality examples can outperform 110K mediocre ones. The project should aggressively deduplicate to ~20K diverse examples, eliminate grammar-fuzzed data, generate 1,000–3,000 error handling triplets via BIFI bootstrapping, increase stdlib coverage to 15–25% through API knowledge graph-guided synthesis, and train the tokenizer exclusively on seed programs. The most novel and actionable insight is that LoRA's limited-rank adapter creates a hard capacity ceiling that makes data diversity *more* important for QLoRA than for full fine-tuning — every near-duplicate mutation directly competes with genuinely informative examples for the adapter's scarce representational bandwidth. A 20K corpus with these improvements would represent a stronger Gate 2 position than the current 150K target.
