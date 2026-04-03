"""Quality scoring for validated corpus entries.

Assigns a 0.0--1.0 score based on compiler pass, differential
agreement, token efficiency, and output match. Entries below the
acceptance threshold or failing required checks are rejected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import tiktoken

from validate.compiler import CompileResult
from validate.diff_test import DiffResult

logger = logging.getLogger(__name__)

# Scoring weights — must sum to 1.0.
WEIGHT_COMPILE: float = 0.3
WEIGHT_DIFF: float = 0.3
WEIGHT_TOKEN_EFF: float = 0.2
WEIGHT_OUTPUT_MATCH: float = 0.2

# Acceptance threshold.
ACCEPTANCE_THRESHOLD: float = 0.6

# Token efficiency ceiling: toke tokens > this multiple of python tokens
# is flagged as inefficient.
TOKEN_EFFICIENCY_CEILING: float = 2.0

# Shared tiktoken encoding — loaded once on first use.
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    """Return the shared cl100k_base encoding, loading lazily."""
    global _encoding  # noqa: PLW0603
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def _count_tokens(text: str) -> int:
    """Count tokens using the cl100k_base encoding."""
    return len(_get_encoding().encode(text))


@dataclass
class QualityScore:
    """Quality assessment for a single corpus entry."""

    accepted: bool
    score: float
    reasons: list[str] = field(default_factory=list)


class QualityScorer:
    """Score and gate corpus entries before acceptance.

    The *holdout_task_ids* parameter is **required** and must be a
    non-empty set.  This is a hard invariant: the pipeline must never
    run without an explicit holdout set, otherwise evaluation tasks
    could leak into training data.
    """

    def __init__(
        self,
        holdout_task_ids: set[str],
    ) -> None:
        if not isinstance(holdout_task_ids, set) or len(holdout_task_ids) == 0:
            raise ValueError(
                "holdout_task_ids is required and must be a non-empty set. "
                "The corpus pipeline refuses to run without an explicit "
                "holdout set — evaluation tasks would leak into training data."
            )
        self.holdout_task_ids: set[str] = holdout_task_ids

    def score(
        self,
        task_id: str,
        toke_src: str,
        python_src: str,
        compile_result: CompileResult,
        diff_result: DiffResult,
    ) -> QualityScore:
        """Compute quality score and acceptance decision."""
        reasons: list[str] = []
        partial_scores: dict[str, float] = {}

        # ---- Required: compiler clean --------------------------------
        compiler_clean = compile_result.success
        partial_scores["compile"] = WEIGHT_COMPILE if compiler_clean else 0.0
        if not compiler_clean:
            reasons.append(
                f"compiler failed: exit_code={compile_result.exit_code}"
            )

        # ---- Required: differential agreement ------------------------
        diff_passed = diff_result.passed
        partial_scores["diff"] = WEIGHT_DIFF if diff_passed else 0.0
        if not diff_passed:
            reason = diff_result.discarded_reason or "toke disagrees with majority"
            reasons.append(f"differential test failed: {reason}")

        # ---- Token efficiency ----------------------------------------
        toke_tokens = _count_tokens(toke_src)
        python_tokens = _count_tokens(python_src)
        if python_tokens > 0:
            ratio = toke_tokens / python_tokens
        else:
            ratio = 0.0

        if ratio > TOKEN_EFFICIENCY_CEILING:
            partial_scores["token_eff"] = 0.0
            reasons.append(
                f"token inefficient: toke/python ratio={ratio:.2f} "
                f"(>{TOKEN_EFFICIENCY_CEILING}x)"
            )
        else:
            # Scale linearly: ratio 0 -> full score, ratio 2 -> 0.
            efficiency = max(0.0, 1.0 - ratio / TOKEN_EFFICIENCY_CEILING)
            partial_scores["token_eff"] = WEIGHT_TOKEN_EFF * efficiency

        # ---- Output match (toke agrees with majority) ----------------
        if diff_result.toke_agrees:
            partial_scores["output_match"] = WEIGHT_OUTPUT_MATCH
        else:
            partial_scores["output_match"] = 0.0
            if "differential test failed" not in " ".join(reasons):
                reasons.append("toke output does not match majority")

        # ---- Holdout isolation (hard reject) -------------------------
        holdout_rejected = task_id in self.holdout_task_ids
        if holdout_rejected:
            reasons.append(
                f"task_id '{task_id}' is in the holdout set — rejected"
            )

        # ---- Final score ---------------------------------------------
        total = sum(partial_scores.values())
        # Clamp to [0.0, 1.0].
        total = max(0.0, min(1.0, total))

        accepted = (
            total >= ACCEPTANCE_THRESHOLD
            and compiler_clean
            and diff_passed
            and not holdout_rejected
        )

        return QualityScore(
            accepted=accepted,
            score=round(total, 4),
            reasons=reasons,
        )
