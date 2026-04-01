"""Tests for the correction loop and escalation engine.

Story 8.1.8 — Correction loop and escalation engine.
"""
from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from correct.escalate import EscalationEngine, EscalationResult
from correct.loop import (
    CorrectionAttempt,
    CorrectionLoop,
    CorrectionResult,
    extract_toke_source,
)
from dispatch.base import GenerateResult, ProviderClient
from generator.curriculum import TaskSpec
from validate.compiler import CompileResult
from validate.diff_test import DiffResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TASK = TaskSpec(
    task_id="A-MTH-0001",
    category="A-MTH",
    description="Add two i64 numbers.",
    expected_signature="F=add(a:i64;b:i64):i64",
    difficulty=1,
    type_hints=["i64"],
    test_input_hint="add(2;3) -> 5",
)

FAILING_COMPILE = CompileResult(
    language="toke",
    success=False,
    exit_code=1,
    stdout="",
    stderr='error: expected ";" after expression',
    duration_ms=5.0,
)

PASSING_COMPILE = CompileResult(
    language="toke",
    success=True,
    exit_code=0,
    stdout="",
    stderr="",
    duration_ms=3.0,
)

DIFF_RESULT = DiffResult(
    passed=False,
    languages_agreed=["python", "c", "java"],
    majority_output="5",
    outputs={"toke": "6", "python": "5", "c": "5", "java": "5"},
    toke_agrees=False,
)

CORRECTED_SOURCE = "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"
BAD_SOURCE = "M=add;\nF=add(a:i64;b:i64):i64{<a+b"  # Missing closing brace.


def _make_provider(name: str = "mock-haiku", tier: int = 1) -> ProviderClient:
    """Create a mock ProviderClient."""
    provider = AsyncMock(spec=ProviderClient)
    provider.name = name
    provider.tier = tier
    provider.cost_per_input_mtok = 0.25
    provider.cost_per_output_mtok = 1.25
    return provider


def _gen_result(text: str, cost: float = 0.001) -> GenerateResult:
    """Create a GenerateResult with sensible defaults."""
    return GenerateResult(
        text=text,
        input_tokens=100,
        output_tokens=50,
        model="mock-model",
        cost=cost,
        latency_ms=200.0,
    )


def _success_subprocess() -> subprocess.CompletedProcess[str]:
    """Simulate tkc --check returning success."""
    return subprocess.CompletedProcess(
        args=["tkc", "--check", "/tmp/test.tk"],
        returncode=0,
        stdout="",
        stderr="",
    )


def _failure_subprocess(stderr: str = "error: syntax") -> subprocess.CompletedProcess[str]:
    """Simulate tkc --check returning failure."""
    return subprocess.CompletedProcess(
        args=["tkc", "--check", "/tmp/test.tk"],
        returncode=1,
        stdout="",
        stderr=stderr,
    )


# ===========================================================================
# Source extraction tests
# ===========================================================================


class TestExtractTokeSource:
    """Test extract_toke_source with various LLM response formats."""

    def test_plain_module_source(self) -> None:
        """Plain toke source without fences."""
        text = "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"
        assert extract_toke_source(text) == text

    def test_markdown_fences(self) -> None:
        """Source wrapped in markdown fences."""
        text = "Here is the corrected code:\n```toke\nM=add;\nF=add(a:i64;b:i64):i64{<a+b};\n```\nDone."
        assert extract_toke_source(text) == "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"

    def test_plain_fences(self) -> None:
        """Source wrapped in plain ``` fences (no language tag)."""
        text = "```\nM=test;\nF=foo():i64{<42};\n```"
        assert extract_toke_source(text) == "M=test;\nF=foo():i64{<42};"

    def test_tk_fences(self) -> None:
        """Source wrapped in ```tk fences."""
        text = "```tk\nM=test;\nF=bar():i64{<1};\n```"
        assert extract_toke_source(text) == "M=test;\nF=bar():i64{<1};"

    def test_multiple_fences_prefer_module(self) -> None:
        """Multiple code blocks — prefer the one with M= declaration."""
        text = (
            "Here is some JSON:\n```json\n{\"key\": 1}\n```\n"
            "And the toke code:\n```\nM=add;\nF=add(a:i64;b:i64):i64{<a+b};\n```"
        )
        assert extract_toke_source(text) == "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"

    def test_m_equals_detection_in_prose(self) -> None:
        """M= declaration embedded in prose without fences."""
        text = (
            "I fixed the error. The corrected program is:\n\n"
            "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"
        )
        result = extract_toke_source(text)
        assert result.startswith("M=add;")

    def test_fallback_raw_text(self) -> None:
        """No fences, no M= — fallback to raw text."""
        text = "  some raw text  "
        assert extract_toke_source(text) == "some raw text"


# ===========================================================================
# CorrectionLoop tests
# ===========================================================================


class TestCorrectionLoop:
    """Test the CorrectionLoop class."""

    @pytest.mark.asyncio
    async def test_success_on_first_retry(self) -> None:
        """Provider returns valid source on the first correction attempt."""
        provider = _make_provider()
        provider.generate.return_value = _gen_result(CORRECTED_SOURCE, cost=0.002)

        loop = CorrectionLoop(max_attempts=3)

        with patch.object(
            CorrectionLoop, "_check_source", return_value=_success_subprocess()
        ):
            result = await loop.correct(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                provider=provider,
                system_prompt="You are a toke expert.",
            )

        assert result.success is True
        assert result.final_source == CORRECTED_SOURCE
        assert len(result.attempts) == 1
        assert result.attempts[0].compiled is True
        assert result.total_cost == pytest.approx(0.002)
        assert result.escalated is False

    @pytest.mark.asyncio
    async def test_success_on_third_retry(self) -> None:
        """Provider fails twice then succeeds on the third attempt."""
        provider = _make_provider()
        provider.generate.side_effect = [
            _gen_result(BAD_SOURCE, cost=0.001),
            _gen_result(BAD_SOURCE, cost=0.001),
            _gen_result(CORRECTED_SOURCE, cost=0.002),
        ]

        loop = CorrectionLoop(max_attempts=3)

        check_results = [
            _failure_subprocess("error: missing brace"),
            _failure_subprocess("error: missing brace"),
            _success_subprocess(),
        ]

        with patch.object(
            CorrectionLoop, "_check_source", side_effect=check_results
        ):
            result = await loop.correct(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                provider=provider,
                system_prompt="You are a toke expert.",
            )

        assert result.success is True
        assert result.final_source == CORRECTED_SOURCE
        assert len(result.attempts) == 3
        assert result.attempts[0].compiled is False
        assert result.attempts[1].compiled is False
        assert result.attempts[2].compiled is True
        assert result.total_cost == pytest.approx(0.004)

    @pytest.mark.asyncio
    async def test_all_attempts_fail(self) -> None:
        """All three correction attempts fail."""
        provider = _make_provider()
        provider.generate.return_value = _gen_result(BAD_SOURCE, cost=0.001)

        loop = CorrectionLoop(max_attempts=3)

        with patch.object(
            CorrectionLoop,
            "_check_source",
            return_value=_failure_subprocess("error: syntax"),
        ):
            result = await loop.correct(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=None,
                provider=provider,
                system_prompt="You are a toke expert.",
            )

        assert result.success is False
        assert result.final_source is None
        assert len(result.attempts) == 3
        assert all(not a.compiled for a in result.attempts)
        assert result.total_cost == pytest.approx(0.003)

    @pytest.mark.asyncio
    async def test_uses_latest_source_each_attempt(self) -> None:
        """Each attempt receives the latest failed source, not the original."""
        provider = _make_provider()
        source_v1 = "M=add;\nF=add(a:i64;b:i64):i64{<a+b"
        source_v2 = "M=add;\nF=add(a:i64;b:i64):i64{<a+b};"
        provider.generate.side_effect = [
            _gen_result(source_v1, cost=0.001),
            _gen_result(source_v2, cost=0.001),
        ]

        loop = CorrectionLoop(max_attempts=2)

        check_results = [
            _failure_subprocess("error: missing semicolon"),
            _success_subprocess(),
        ]

        with patch.object(
            CorrectionLoop, "_check_source", side_effect=check_results
        ):
            result = await loop.correct(
                task=SAMPLE_TASK,
                original_source="M=add;\nbroken",
                compile_result=FAILING_COMPILE,
                diff_result=None,
                provider=provider,
                system_prompt="Fix it.",
            )

        assert result.success is True
        # Verify the provider was called twice.
        assert provider.generate.call_count == 2
        # The second call's prompt should contain source_v1 (from first failed attempt),
        # not the original "M=add;\nbroken".
        second_call_prompt = provider.generate.call_args_list[1][0][1]
        assert source_v1 in second_call_prompt

    @pytest.mark.asyncio
    async def test_correction_template_used(self) -> None:
        """When a correction template is provided, it is used."""
        template = (
            "Task: {task_description}\n"
            "Code:\n{original_code}\n"
            "Error:\n{diagnostic_json}\n"
            "Expected:\n{expected_output}\n"
            "Grammar:\n{grammar_subset}"
        )
        provider = _make_provider()
        provider.generate.return_value = _gen_result(CORRECTED_SOURCE, cost=0.001)

        loop = CorrectionLoop(max_attempts=1, correction_template=template)

        with patch.object(
            CorrectionLoop, "_check_source", return_value=_success_subprocess()
        ):
            result = await loop.correct(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                provider=provider,
                system_prompt="System.",
            )

        assert result.success is True
        # Verify the template was expanded.
        call_prompt = provider.generate.call_args[0][1]
        assert "Add two i64 numbers." in call_prompt


# ===========================================================================
# EscalationEngine tests
# ===========================================================================


class TestEscalationEngine:
    """Test the EscalationEngine class."""

    @pytest.mark.asyncio
    async def test_tier1_success_no_escalation(self) -> None:
        """Tier-1 correction succeeds — no escalation needed."""
        tier1 = _make_provider("haiku", tier=1)
        tier2 = [_make_provider("sonnet", tier=2)]

        loop = CorrectionLoop(max_attempts=3)
        engine = EscalationEngine(correction_loop=loop)

        successful_result = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=True,
            final_source=CORRECTED_SOURCE,
            attempts=[
                CorrectionAttempt(
                    attempt_number=1,
                    provider_name="haiku",
                    source=CORRECTED_SOURCE,
                    compiled=True,
                    diff_passed=False,
                    error_output="",
                    cost=0.002,
                ),
            ],
            escalated=False,
            total_cost=0.002,
        )

        with patch.object(loop, "correct", return_value=successful_result) as mock_correct:
            result = await engine.handle_failure(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                tier1_provider=tier1,
                tier2_providers=tier2,
                system_prompt="Fix.",
            )

        assert result.success is True
        assert result.final_source == CORRECTED_SOURCE
        assert len(result.tier1_attempts) == 1
        assert len(result.tier2_attempts) == 0
        assert result.replacement_task is None
        assert result.total_cost == pytest.approx(0.002)
        # Correction loop should only be called once (tier 1).
        mock_correct.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier2_success(self) -> None:
        """Tier-1 fails, tier-2 provider[0] succeeds."""
        tier1 = _make_provider("haiku", tier=1)
        tier2 = [_make_provider("sonnet", tier=2)]

        loop = CorrectionLoop(max_attempts=3)
        engine = EscalationEngine(correction_loop=loop)

        failed_attempt = CorrectionAttempt(
            attempt_number=1,
            provider_name="haiku",
            source=BAD_SOURCE,
            compiled=False,
            diff_passed=False,
            error_output="error: syntax",
            cost=0.001,
        )
        tier1_fail = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=False,
            final_source=None,
            attempts=[failed_attempt] * 3,
            escalated=False,
            total_cost=0.003,
        )
        tier2_success = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=True,
            final_source=CORRECTED_SOURCE,
            attempts=[
                CorrectionAttempt(
                    attempt_number=1,
                    provider_name="sonnet",
                    source=CORRECTED_SOURCE,
                    compiled=True,
                    diff_passed=False,
                    error_output="",
                    cost=0.005,
                ),
            ],
            escalated=False,
            total_cost=0.005,
        )

        with patch.object(
            loop, "correct", side_effect=[tier1_fail, tier2_success]
        ):
            result = await engine.handle_failure(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                tier1_provider=tier1,
                tier2_providers=tier2,
                system_prompt="Fix.",
            )

        assert result.success is True
        assert result.final_source == CORRECTED_SOURCE
        assert len(result.tier1_attempts) == 3
        assert len(result.tier2_attempts) == 1
        assert result.replacement_task is None
        assert result.total_cost == pytest.approx(0.008)

    @pytest.mark.asyncio
    async def test_all_fail_replacement(self) -> None:
        """All tiers fail — replacement task is generated."""
        tier1 = _make_provider("haiku", tier=1)
        tier2 = [
            _make_provider("sonnet", tier=2),
            _make_provider("opus", tier=2),
        ]

        loop = CorrectionLoop(max_attempts=3)
        engine = EscalationEngine(correction_loop=loop)

        failed_attempt = CorrectionAttempt(
            attempt_number=1,
            provider_name="any",
            source=BAD_SOURCE,
            compiled=False,
            diff_passed=False,
            error_output="error: syntax",
            cost=0.001,
        )
        fail_result = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=False,
            final_source=None,
            attempts=[failed_attempt] * 3,
            escalated=False,
            total_cost=0.003,
        )

        with patch.object(loop, "correct", return_value=fail_result):
            result = await engine.handle_failure(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                tier1_provider=tier1,
                tier2_providers=tier2,
                system_prompt="Fix.",
            )

        assert result.success is False
        assert result.final_source is None
        assert len(result.tier1_attempts) == 3
        # 2 tier-2 providers x 3 attempts each = 6 attempts.
        assert len(result.tier2_attempts) == 6
        assert result.replacement_task is not None
        assert result.replacement_task.task_id == "A-MTH-0001-R"
        assert result.replacement_task.category == "A-MTH"
        # Total cost: 3 calls x 0.003 each = 0.009.
        assert result.total_cost == pytest.approx(0.009)

    @pytest.mark.asyncio
    async def test_cost_tracking_across_all_tiers(self) -> None:
        """Total cost accumulates correctly across all escalation tiers."""
        tier1 = _make_provider("haiku", tier=1)
        tier2 = [_make_provider("sonnet", tier=2)]

        loop = CorrectionLoop(max_attempts=3)
        engine = EscalationEngine(correction_loop=loop)

        tier1_result = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=False,
            final_source=None,
            attempts=[
                CorrectionAttempt(
                    attempt_number=i,
                    provider_name="haiku",
                    source=BAD_SOURCE,
                    compiled=False,
                    diff_passed=False,
                    error_output="error",
                    cost=0.001,
                )
                for i in range(1, 4)
            ],
            escalated=False,
            total_cost=0.003,
        )
        tier2_result = CorrectionResult(
            task_id=SAMPLE_TASK.task_id,
            success=True,
            final_source=CORRECTED_SOURCE,
            attempts=[
                CorrectionAttempt(
                    attempt_number=1,
                    provider_name="sonnet",
                    source=CORRECTED_SOURCE,
                    compiled=True,
                    diff_passed=False,
                    error_output="",
                    cost=0.010,
                ),
            ],
            escalated=False,
            total_cost=0.010,
        )

        with patch.object(
            loop, "correct", side_effect=[tier1_result, tier2_result]
        ):
            result = await engine.handle_failure(
                task=SAMPLE_TASK,
                original_source=BAD_SOURCE,
                compile_result=FAILING_COMPILE,
                diff_result=DIFF_RESULT,
                tier1_provider=tier1,
                tier2_providers=tier2,
                system_prompt="Fix.",
            )

        assert result.total_cost == pytest.approx(0.013)


# ===========================================================================
# EscalationEngine.replace_task tests
# ===========================================================================


class TestReplaceTask:
    """Test replacement task generation."""

    @pytest.mark.asyncio
    async def test_replacement_preserves_category(self) -> None:
        """Replacement task keeps the same category and difficulty."""
        loop = CorrectionLoop(max_attempts=3)
        engine = EscalationEngine(correction_loop=loop)

        replacement = await engine.replace_task(SAMPLE_TASK)

        assert replacement is not None
        assert replacement.task_id == "A-MTH-0001-R"
        assert replacement.category == SAMPLE_TASK.category
        assert replacement.difficulty == SAMPLE_TASK.difficulty
        assert replacement.description == SAMPLE_TASK.description
        assert replacement.expected_signature == SAMPLE_TASK.expected_signature
