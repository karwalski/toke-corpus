# Qwen Judge Evaluation Rubric

A corpus entry is accepted when ALL criteria are met.

## Criteria

| Criterion | Weight | Fail condition |
|-----------|--------|----------------|
| Compiler clean | required | tkc exits non-zero |
| Differential agreement | required | <3 of 4 reference languages agree |
| Correctness | required | Output does not match majority reference |
| Idiom quality | 0–1 | Score < 0.6 |
| Token efficiency | 0–1 | tk_tokens > 2× Python baseline |
| No holdout leak | required | Task ID appears in benchmark/hidden_tests |

## Scoring

The judge assigns a score from 0.0 to 1.0. Entries scoring below 0.6
on idiom quality are rejected regardless of other criteria.
