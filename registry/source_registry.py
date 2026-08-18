"""Task source registry — complete catalog of open-source task datasets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSource:
    name: str                  # e.g. "humaneval", "exercism-python"
    display_name: str          # e.g. "HumanEval", "Exercism (Python)"
    size: int                  # approximate number of tasks/problems
    license: str               # e.g. "MIT", "CC-BY-SA-3.0"
    usage: str                 # "evaluation" | "training" | "split"
    split_ratio: float | None  # e.g. 0.8 for 80/20 (None if not split)
    url: str                   # primary URL for the dataset
    category: str              # "benchmark" | "exercise" | "algorithm" | "domain"
    notes: str                 # free-text notes


# ---------------------------------------------------------------------------
# Full registry
# ---------------------------------------------------------------------------

REGISTRY: list[TaskSource] = [
    # ---- Benchmark datasets (evaluation only) ----
    TaskSource(
        name="humaneval",
        display_name="HumanEval",
        size=164,
        license="MIT",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/openai/human-eval",
        category="benchmark",
        notes="OpenAI hand-written Python problems",
    ),
    TaskSource(
        name="humanevalplus",
        display_name="HumanEval+ (EvalPlus)",
        size=164,
        license="MIT",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/evalplus/evalplus",
        category="benchmark",
        notes="Extended test cases for HumanEval",
    ),
    TaskSource(
        name="mbpp",
        display_name="MBPP",
        size=1000,
        license="MIT",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/google-research/google-research/tree/master/mbpp",
        category="benchmark",
        notes="Mostly Basic Python Programming benchmark",
    ),
    TaskSource(
        name="mbppplus",
        display_name="MBPP+ (EvalPlus)",
        size=1000,
        license="MIT",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/evalplus/evalplus",
        category="benchmark",
        notes="Extended test cases for MBPP",
    ),
    TaskSource(
        name="livecodebench",
        display_name="LiveCodeBench",
        size=0,
        license="CC-BY-4.0",
        usage="evaluation",
        split_ratio=None,
        url="https://livecodebench.github.io/",
        category="benchmark",
        notes="Rolling monthly benchmark, size varies",
    ),
    TaskSource(
        name="multiple",
        display_name="MultiPL-E",
        size=0,
        license="MIT",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/nuprl/MultiPL-E",
        category="benchmark",
        notes="HumanEval+MBPP translated to 18 languages",
    ),
    TaskSource(
        name="mbxp",
        display_name="MBXP",
        size=0,
        license="CC-BY-4.0",
        usage="evaluation",
        split_ratio=None,
        url="https://github.com/amazon-science/mbxp-exec-eval",
        category="benchmark",
        notes="MBPP translated to 13 languages",
    ),

    # ---- Benchmark datasets (80/20 split) ----
    TaskSource(
        name="apps",
        display_name="APPS",
        size=10000,
        license="CC-BY-SA",
        usage="split",
        split_ratio=0.8,
        url="https://github.com/hendrycks/apps",
        category="benchmark",
        notes="Automated Programming Progress Standard",
    ),
    TaskSource(
        name="codecontests",
        display_name="CodeContests",
        size=13000,
        license="Apache-2.0",
        usage="split",
        split_ratio=0.8,
        url="https://github.com/google-deepmind/code_contests",
        category="benchmark",
        notes="Competitive programming problems from DeepMind",
    ),
    TaskSource(
        name="taco",
        display_name="TACO",
        size=25000,
        license="MIT",
        usage="split",
        split_ratio=0.8,
        url="https://github.com/FlagOpen/TACO",
        category="benchmark",
        notes="Topics in Algorithmic COde generation",
    ),
    TaskSource(
        name="leetcodedataset",
        display_name="LeetCodeDataset",
        size=3100,
        license="MIT",
        usage="split",
        split_ratio=0.8,
        url="https://github.com/doocs/leetcode",
        category="benchmark",
        notes="LeetCode problems dataset",
    ),

    # ---- Exercise repositories (training) ----
    TaskSource(
        name="exercism-python",
        display_name="Exercism (Python)",
        size=350,
        license="CC-BY-SA-3.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/exercism/python",
        category="exercise",
        notes="Python track exercises",
    ),
    TaskSource(
        name="exercism-go",
        display_name="Exercism (Go)",
        size=300,
        license="CC-BY-SA-3.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/exercism/go",
        category="exercise",
        notes="Go track exercises",
    ),
    TaskSource(
        name="exercism-rust",
        display_name="Exercism (Rust)",
        size=250,
        license="CC-BY-SA-3.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/exercism/rust",
        category="exercise",
        notes="Rust track exercises",
    ),
    TaskSource(
        name="exercism-c",
        display_name="Exercism (C)",
        size=200,
        license="CC-BY-SA-3.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/exercism/c",
        category="exercise",
        notes="C track exercises",
    ),
    TaskSource(
        name="rosettacode",
        display_name="Rosetta Code",
        size=1000,
        license="GFDL-1.2",
        usage="training",
        split_ratio=None,
        url="https://rosettacode.org/",
        category="exercise",
        notes="Multi-language programming tasks",
    ),
    TaskSource(
        name="projecteuler",
        display_name="Project Euler solutions",
        size=300,
        license="varies",
        usage="training",
        split_ratio=None,
        url="https://projecteuler.net/",
        category="exercise",
        notes="Mathematical/computational problems, ~200-400 solutions available",
    ),
    TaskSource(
        name="codewars",
        display_name="Codewars kata",
        size=8000,
        license="CC-BY-SA",
        usage="training",
        split_ratio=None,
        url="https://www.codewars.com/",
        category="exercise",
        notes="Community kata challenges",
    ),
    TaskSource(
        name="adventofcode",
        display_name="Advent of Code solutions",
        size=250,
        license="varies",
        usage="training",
        split_ratio=None,
        url="https://adventofcode.com/",
        category="exercise",
        notes="Annual programming puzzle event solutions",
    ),

    # ---- Algorithm collections (training) ----
    TaskSource(
        name="thealgorithms-python",
        display_name="TheAlgorithms/Python",
        size=1500,
        license="MIT",
        usage="training",
        split_ratio=None,
        url="https://github.com/TheAlgorithms/Python",
        category="algorithm",
        notes="Algorithm implementations in Python",
    ),
    TaskSource(
        name="thealgorithms-go",
        display_name="TheAlgorithms/Go",
        size=400,
        license="MIT",
        usage="training",
        split_ratio=None,
        url="https://github.com/TheAlgorithms/Go",
        category="algorithm",
        notes="Algorithm implementations in Go",
    ),
    TaskSource(
        name="thealgorithms-c",
        display_name="TheAlgorithms/C",
        size=500,
        license="MIT",
        usage="training",
        split_ratio=None,
        url="https://github.com/TheAlgorithms/C",
        category="algorithm",
        notes="Algorithm implementations in C",
    ),
    TaskSource(
        name="williamfiset-algorithms",
        display_name="William Fiset's algorithms",
        size=100,
        license="MIT",
        usage="training",
        split_ratio=None,
        url="https://github.com/williamfiset/Algorithms",
        category="algorithm",
        notes="Java algorithm implementations with video tutorials",
    ),
    TaskSource(
        name="30-seconds-of-python",
        display_name="30-seconds-of-python",
        size=300,
        license="CC-BY-4.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/30-seconds/30-seconds-of-python",
        category="algorithm",
        notes="Short Python code snippets",
    ),
    TaskSource(
        name="30-seconds-of-code",
        display_name="30-seconds-of-code",
        size=300,
        license="CC-BY-4.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/30-seconds/30-seconds-of-code",
        category="algorithm",
        notes="Short JavaScript code snippets",
    ),
    TaskSource(
        name="gobyexample",
        display_name="go-by-example",
        size=80,
        license="CC-BY-3.0",
        usage="training",
        split_ratio=None,
        url="https://gobyexample.com/",
        category="algorithm",
        notes="Go examples with annotations",
    ),
    TaskSource(
        name="rustbyexample",
        display_name="Rust by Example",
        size=150,
        license="MIT/Apache-2.0",
        usage="training",
        split_ratio=None,
        url="https://doc.rust-lang.org/rust-by-example/",
        category="algorithm",
        notes="Rust examples with annotations",
    ),

    # ---- Domain-specific (training) ----
    TaskSource(
        name="buildyourownx",
        display_name="Build Your Own X",
        size=200,
        license="various",
        usage="training",
        split_ratio=None,
        url="https://github.com/codecrafters-io/build-your-own-x",
        category="domain",
        notes="Project-based tutorials for building tools from scratch",
    ),
    TaskSource(
        name="realworld",
        display_name="Realworld (gothinkster)",
        size=1,
        license="MIT",
        usage="training",
        split_ratio=None,
        url="https://github.com/gothinkster/realworld",
        category="domain",
        notes="1 spec, many implementations across frameworks",
    ),
    TaskSource(
        name="cpython-testsuite",
        display_name="CPython test suite",
        size=500,
        license="PSF-2.0",
        usage="training",
        split_ratio=None,
        url="https://github.com/python/cpython/tree/main/Lib/test",
        category="domain",
        notes="CPython standard library test functions",
    ),
    TaskSource(
        name="sqlbolt",
        display_name="SQLBolt",
        size=20,
        license="CC-BY-SA-4.0",
        usage="training",
        split_ratio=None,
        url="https://sqlbolt.com/",
        category="domain",
        notes="Interactive SQL exercises",
    ),

    # ---- Existing toke holdouts (evaluation only) ----
    TaskSource(
        name="gate1-holdout",
        display_name="Gate 1 held-out tasks",
        size=1000,
        license="proprietary",
        usage="evaluation",
        split_ratio=None,
        url="",
        category="benchmark",
        notes="Internal toke Gate 1 evaluation holdout",
    ),
    TaskSource(
        name="gate2-hidden",
        display_name="Gate 2 hidden tests",
        size=0,
        license="proprietary",
        usage="evaluation",
        split_ratio=None,
        url="",
        category="benchmark",
        notes="Internal toke Gate 2 evaluation holdout, TBD",
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_INDEX: dict[str, TaskSource] = {s.name: s for s in REGISTRY}


def get_source(name: str) -> TaskSource:
    """Look up a source by its unique name. Raises KeyError if not found."""
    if name not in _INDEX:
        raise KeyError(f"Unknown source: {name!r}")
    return _INDEX[name]


def get_training_sources() -> list[TaskSource]:
    """Return all sources whose usage is 'training'."""
    return [s for s in REGISTRY if s.usage == "training"]


def get_evaluation_sources() -> list[TaskSource]:
    """Return all sources whose usage is 'evaluation'."""
    return [s for s in REGISTRY if s.usage == "evaluation"]


def get_split_sources() -> list[TaskSource]:
    """Return all sources whose usage is 'split' (80/20)."""
    return [s for s in REGISTRY if s.usage == "split"]


def get_sources_by_category(category: str) -> list[TaskSource]:
    """Return all sources matching the given category."""
    return [s for s in REGISTRY if s.category == category]
