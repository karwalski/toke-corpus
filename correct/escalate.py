"""Escalation engine for correction failures.

When a tier-1 provider exhausts its correction attempts, the engine
escalates to tier-2 providers in sequence. If all providers fail,
the task is marked for replacement.

Story 8.1.8 — Correction loop and escalation engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from correct.loop import CorrectionAttempt, CorrectionLoop, CorrectionResult
from dispatch.base import ProviderClient
from generator.curriculum import TaskSpec
from validate.compiler import CompileResult
from validate.diff_test import DiffResult

if TYPE_CHECKING:
    from dispatch.pool import PoolManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EscalationResult:
    """Aggregate result of the full escalation chain for one task."""

    task_id: str
    success: bool
    final_source: str | None
    tier1_attempts: list[CorrectionAttempt]
    tier2_attempts: list[CorrectionAttempt]
    replacement_task: TaskSpec | None
    total_cost: float


# ---------------------------------------------------------------------------
# EscalationEngine
# ---------------------------------------------------------------------------


class EscalationEngine:
    """Orchestrate correction across multiple provider tiers.

    Escalation chain:
      1. Tier-1 provider correction loop (max_attempts per loop).
      2. Tier-2 provider[0] correction loop.
      3. Tier-2 provider[1] correction loop (if available).
      4. If all fail, mark the task for replacement.
    """

    def __init__(
        self,
        correction_loop: CorrectionLoop,
        pool_manager: PoolManager | None = None,
    ) -> None:
        self.correction_loop = correction_loop
        self.pool_manager = pool_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_failure(
        self,
        task: TaskSpec,
        original_source: str,
        compile_result: CompileResult,
        diff_result: DiffResult | None,
        tier1_provider: ProviderClient,
        tier2_providers: list[ProviderClient],
        system_prompt: str,
    ) -> EscalationResult:
        """Run the full escalation chain for a failed task.

        Returns an ``EscalationResult`` with attempts from all tiers
        and a replacement task if everything failed.
        """
        tier1_attempts: list[CorrectionAttempt] = []
        tier2_attempts: list[CorrectionAttempt] = []
        total_cost = 0.0

        # --- Tier 1 correction -------------------------------------------
        tier1_result = await self.correction_loop.correct(
            task=task,
            original_source=original_source,
            compile_result=compile_result,
            diff_result=diff_result,
            provider=tier1_provider,
            system_prompt=system_prompt,
        )
        tier1_attempts.extend(tier1_result.attempts)
        total_cost += tier1_result.total_cost

        if tier1_result.success:
            logger.info("Task %s corrected by tier-1 provider", task.task_id)
            return EscalationResult(
                task_id=task.task_id,
                success=True,
                final_source=tier1_result.final_source,
                tier1_attempts=tier1_attempts,
                tier2_attempts=tier2_attempts,
                replacement_task=None,
                total_cost=total_cost,
            )

        # --- Tier 2 escalation -------------------------------------------
        # Use the last failed source from tier 1 as the starting point.
        current_source = original_source
        current_compile = compile_result
        if tier1_result.attempts:
            last = tier1_result.attempts[-1]
            current_source = last.source
            # Build a synthetic CompileResult from the last attempt's error.
            current_compile = CompileResult(
                language="toke",
                success=False,
                exit_code=1,
                stdout="",
                stderr=last.error_output,
                duration_ms=0.0,
            )

        for idx, provider in enumerate(tier2_providers):
            logger.info(
                "Escalating task %s to tier-2 provider %s (%d/%d)",
                task.task_id,
                provider.name,
                idx + 1,
                len(tier2_providers),
            )

            tier2_result = await self.correction_loop.correct(
                task=task,
                original_source=current_source,
                compile_result=current_compile,
                diff_result=diff_result,
                provider=provider,
                system_prompt=system_prompt,
            )
            tier2_attempts.extend(tier2_result.attempts)
            total_cost += tier2_result.total_cost

            if tier2_result.success:
                logger.info(
                    "Task %s corrected by tier-2 provider %s",
                    task.task_id,
                    provider.name,
                )
                return EscalationResult(
                    task_id=task.task_id,
                    success=True,
                    final_source=tier2_result.final_source,
                    tier1_attempts=tier1_attempts,
                    tier2_attempts=tier2_attempts,
                    replacement_task=None,
                    total_cost=total_cost,
                )

            # Feed forward the last failure for the next tier-2 provider.
            if tier2_result.attempts:
                last = tier2_result.attempts[-1]
                current_source = last.source
                current_compile = CompileResult(
                    language="toke",
                    success=False,
                    exit_code=1,
                    stdout="",
                    stderr=last.error_output,
                    duration_ms=0.0,
                )

        # --- All tiers exhausted — generate replacement -------------------
        logger.warning(
            "All correction tiers exhausted for task %s — generating replacement",
            task.task_id,
        )
        replacement = await self.replace_task(task)

        return EscalationResult(
            task_id=task.task_id,
            success=False,
            final_source=None,
            tier1_attempts=tier1_attempts,
            tier2_attempts=tier2_attempts,
            replacement_task=replacement,
            total_cost=total_cost,
        )

    # ------------------------------------------------------------------
    # Replacement task generation
    # ------------------------------------------------------------------

    async def replace_task(self, failed_task: TaskSpec) -> TaskSpec | None:
        """Generate a replacement task in the same category.

        Returns a new ``TaskSpec`` with a ``-R`` suffix appended to the
        original task ID, or ``None`` if replacement is not possible.
        """
        replacement_id = f"{failed_task.task_id}-R"
        logger.info(
            "Generating replacement task %s for failed %s",
            replacement_id,
            failed_task.task_id,
        )

        # Build a replacement spec in the same category with the same
        # difficulty. The description is preserved so the orchestrator
        # can re-generate from scratch with a fresh prompt.
        return TaskSpec(
            task_id=replacement_id,
            category=failed_task.category,
            description=failed_task.description,
            expected_signature=failed_task.expected_signature,
            difficulty=failed_task.difficulty,
            type_hints=list(failed_task.type_hints),
            test_input_hint=failed_task.test_input_hint,
        )
