"""Tests for the trial runner and scorer.

Uses mocked providers — no real API calls. Verifies:
- TrialRunner with mocked providers
- Output extraction (markdown fences, preamble removal)
- TrialScorer scoring math, model dropping, allocation
- Scorecard JSON serialisation round-trip

Story 8.1.5 — Model capability trial framework.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dispatch.base import GenerateResult, ProviderClient
from generator.curriculum import TaskSpec
from trial.runner import TrialResults, TrialRunner, TrialTaskResult, extract_toke_source
from trial.scorer import (
    DIFF_AGREEMENT_BASELINE,
    WEIGHT_CORRECTION,
    WEIGHT_COST,
    WEIGHT_DIFF_AGREEMENT,
    WEIGHT_FIRST_PASS,
    ModelScore,
    ModelScorecard,
    PoolAllocation,
    TrialScorer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockProvider(ProviderClient):
    """A fake provider that returns a configurable toke program."""

    def __init__(
        self,
        provider_name: str,
        tier_value: int,
        response_text: str,
        cost_per_call: float = 0.001,
    ) -> None:
        self._name = provider_name
        self._tier = tier_value
        self._response_text = response_text
        self._cost_per_call = cost_per_call

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return self._tier

    @property
    def cost_per_input_mtok(self) -> float:
        return 0.15

    @property
    def cost_per_output_mtok(self) -> float:
        return 0.60

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        return GenerateResult(
            text=self._response_text,
            input_tokens=100,
            output_tokens=50,
            model=self._name,
            cost=self._cost_per_call,
            latency_ms=50.0,
        )


def _make_task(task_id: str = "A-MTH-0001", category: str = "A-MTH") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        category=category,
        description="Return the absolute value of an integer.",
        expected_signature="F=abs(x:i64):i64",
        difficulty=1,
        type_hints=["i64"],
        test_input_hint="x=-5 -> 5, x=3 -> 3",
    )


def _make_result(
    task_id: str,
    provider_name: str,
    compiled: bool,
    correction_compiled: bool = False,
    cost: float = 0.001,
) -> TrialTaskResult:
    return TrialTaskResult(
        task_id=task_id,
        provider_name=provider_name,
        compiled=compiled,
        correction_compiled=correction_compiled,
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        latency_ms=50.0,
        error_output=None if compiled else "E1001: syntax error",
    )


# ---------------------------------------------------------------------------
# Tests — extract_toke_source
# ---------------------------------------------------------------------------


class TestExtractTokeSource:
    """Tests for the source extraction helper."""

    def test_plain_source(self) -> None:
        raw = "M=abs;\nF=abs(x:i64):i64{\n  if(x<0){< -x};\n  <x\n};"
        assert extract_toke_source(raw) == raw.strip()

    def test_markdown_fences(self) -> None:
        raw = (
            "Here is the program:\n"
            "```toke\n"
            "M=abs;\nF=abs(x:i64):i64{\n  <x\n};\n"
            "```\n"
            "This should compile."
        )
        result = extract_toke_source(raw)
        assert result.startswith("M=abs;")
        assert "```" not in result
        assert "Here is" not in result

    def test_markdown_fences_tk(self) -> None:
        raw = "```tk\nM=test;\nF=f():i64{<1};\n```"
        result = extract_toke_source(raw)
        assert result == "M=test;\nF=f():i64{<1};"

    def test_markdown_fences_plain(self) -> None:
        raw = "```\nM=test;\nF=f():i64{<1};\n```"
        result = extract_toke_source(raw)
        assert result == "M=test;\nF=f():i64{<1};"

    def test_preamble_text(self) -> None:
        raw = (
            "Sure! Here's a toke program for absolute value:\n\n"
            "M=abs;\nF=abs(x:i64):i64{\n  if(x<0){< -x};\n  <x\n};"
        )
        result = extract_toke_source(raw)
        assert result.startswith("M=abs;")
        assert "Sure!" not in result

    def test_empty_string(self) -> None:
        assert extract_toke_source("") == ""

    def test_no_module_declaration(self) -> None:
        raw = "let x:i64=42;"
        assert extract_toke_source(raw) == raw


# ---------------------------------------------------------------------------
# Tests — TrialRunner
# ---------------------------------------------------------------------------


class TestTrialRunner:
    """Tests for the trial runner with mocked providers."""

    @pytest.fixture
    def valid_source(self) -> str:
        return "M=abs;\nF=abs(x:i64):i64{\n  if(x<0){< -x};\n  <x\n};"

    @pytest.fixture
    def tasks(self) -> list[TaskSpec]:
        return [
            _make_task("A-MTH-0001", "A-MTH"),
            _make_task("A-MTH-0002", "A-MTH"),
        ]

    @patch("trial.runner.validate_toke")
    def test_all_compile(
        self,
        mock_validate: AsyncMock,
        valid_source: str,
        tasks: list[TaskSpec],
    ) -> None:
        """All tasks compile on first pass — no correction needed."""
        mock_validate.return_value = (True, None)

        provider = MockProvider("test-model", 1, valid_source)
        runner = TrialRunner(
            providers=[provider],
            task_specs=tasks,
            tkc_path="tkc",
            system_prompt="system",
            generate_template="{category} {task_description} {expected_signature}",
        )

        results = asyncio.run(runner.run())
        assert len(results.results) == 2
        assert all(r.compiled for r in results.results)
        assert all(not r.correction_compiled for r in results.results)
        assert results.started_at != ""
        assert results.finished_at != ""

    @patch("trial.runner.validate_toke")
    def test_first_fail_correction_passes(
        self,
        mock_validate: AsyncMock,
        valid_source: str,
        tasks: list[TaskSpec],
    ) -> None:
        """First pass fails, correction pass succeeds."""
        # First call fails, second call (correction) succeeds.
        mock_validate.side_effect = [
            (False, "E1001: syntax error"),
            (True, None),
            (False, "E1001: syntax error"),
            (True, None),
        ]

        provider = MockProvider("test-model", 1, valid_source)
        runner = TrialRunner(
            providers=[provider],
            task_specs=tasks,
            tkc_path="tkc",
            system_prompt="system",
            generate_template="{category} {task_description} {expected_signature}",
        )

        results = asyncio.run(runner.run())
        assert len(results.results) == 2
        assert all(not r.compiled for r in results.results)
        assert all(r.correction_compiled for r in results.results)

    @patch("trial.runner.validate_toke")
    def test_multiple_providers(
        self,
        mock_validate: AsyncMock,
        valid_source: str,
        tasks: list[TaskSpec],
    ) -> None:
        """Two providers, two tasks = four results."""
        mock_validate.return_value = (True, None)

        p1 = MockProvider("model-a", 1, valid_source)
        p2 = MockProvider("model-b", 2, valid_source)
        runner = TrialRunner(
            providers=[p1, p2],
            task_specs=tasks,
            tkc_path="tkc",
            system_prompt="system",
            generate_template="{category} {task_description} {expected_signature}",
        )

        results = asyncio.run(runner.run())
        assert len(results.results) == 4

        providers_seen = {r.provider_name for r in results.results}
        assert providers_seen == {"model-a", "model-b"}

    @patch("trial.runner.validate_toke")
    def test_provider_exception_handled(
        self,
        mock_validate: AsyncMock,
        tasks: list[TaskSpec],
    ) -> None:
        """Provider that raises an exception produces a failed result."""
        provider = MockProvider("crash-model", 1, "")
        # Override generate to raise.
        original_generate = provider.generate

        call_count = 0

        async def _raise(system: str, prompt: str) -> GenerateResult:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("API error")

        provider.generate = _raise  # type: ignore[assignment]

        runner = TrialRunner(
            providers=[provider],
            task_specs=tasks,
            tkc_path="tkc",
            system_prompt="system",
            generate_template="{category} {task_description} {expected_signature}",
        )

        results = asyncio.run(runner.run())
        assert len(results.results) == 2
        assert all(not r.compiled for r in results.results)
        assert all(r.error_output == "provider exception" for r in results.results)

    def test_save_results(self) -> None:
        """Results can be serialised to JSON."""
        trial = TrialResults(
            results=[
                _make_result("A-MTH-0001", "model-a", True),
            ],
            started_at="2026-03-29T00:00:00+00:00",
            finished_at="2026-03-29T01:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "results.json"
            TrialRunner.save_results(trial, path)
            data = json.loads(path.read_text())
            assert data["count"] == 1
            assert data["results"][0]["task_id"] == "A-MTH-0001"


# ---------------------------------------------------------------------------
# Tests — TrialScorer
# ---------------------------------------------------------------------------


class TestTrialScorer:
    """Tests for scoring, model dropping, and allocation."""

    def _build_results(
        self,
        provider_configs: list[tuple[str, int, int, int]],
        task_count: int = 10,
    ) -> TrialResults:
        """Build synthetic TrialResults.

        Each config is (provider_name, compiled_count, correction_count,
        total_tasks). compiled_count tasks compile first pass,
        correction_count of the remaining tasks compile on correction.
        """
        results: list[TrialTaskResult] = []
        for pname, compiled, corrected, total in provider_configs:
            for i in range(total):
                is_compiled = i < compiled
                is_corrected = (
                    not is_compiled and i < compiled + corrected
                )
                results.append(
                    _make_result(
                        task_id=f"A-MTH-{i:04d}",
                        provider_name=pname,
                        compiled=is_compiled,
                        correction_compiled=is_corrected,
                        cost=0.001,
                    )
                )
        return TrialResults(
            results=results,
            started_at="2026-03-29T00:00:00+00:00",
            finished_at="2026-03-29T01:00:00+00:00",
        )

    def test_perfect_model(self) -> None:
        """A model that compiles 100% on first pass gets maximum score."""
        trial = self._build_results([("perfect", 10, 0, 10)])
        scorer = TrialScorer(trial)
        scorecard = scorer.score()

        assert len(scorecard.scores) == 1
        s = scorecard.scores[0]
        assert s.provider_name == "perfect"
        assert s.first_pass_compile_rate == 1.0
        assert s.passed is True
        assert len(scorecard.dropped) == 0

    def test_model_below_threshold_dropped(self) -> None:
        """A model below 50% first-pass compile rate is dropped."""
        trial = self._build_results([("weak", 4, 2, 10)])
        scorer = TrialScorer(trial, min_compile_rate=0.50)
        scorecard = scorer.score()

        assert len(scorecard.scores) == 1
        s = scorecard.scores[0]
        assert s.first_pass_compile_rate == 0.4
        assert s.passed is False
        assert "weak" in scorecard.dropped

    def test_correction_rate_calculation(self) -> None:
        """Correction success rate = corrected / failed."""
        # 6 compile, 2 corrected out of 4 failures = 50% correction rate.
        trial = self._build_results([("model-a", 6, 2, 10)])
        scorer = TrialScorer(trial)
        scorecard = scorer.score()

        s = scorecard.scores[0]
        assert s.first_pass_compile_rate == 0.6
        assert s.correction_success_rate == 0.5
        assert s.passed is True

    def test_composite_score_math(self) -> None:
        """Verify the composite score formula."""
        # Single model, 80% compile, 50% correction, normalized cost = 1.0
        trial = self._build_results([("model-a", 8, 1, 10)])
        scorer = TrialScorer(trial)
        scorecard = scorer.score()

        s = scorecard.scores[0]
        expected = (
            WEIGHT_FIRST_PASS * 0.8
            + WEIGHT_CORRECTION * 0.5
            + WEIGHT_COST * 1.0  # single model => normalised to 1.0
            + WEIGHT_DIFF_AGREEMENT * DIFF_AGREEMENT_BASELINE
        )
        assert abs(s.composite_score - round(expected, 4)) < 0.001

    def test_multiple_models_ranked(self) -> None:
        """Models are ranked by composite score, best first."""
        trial = self._build_results([
            ("good", 9, 1, 10),
            ("mediocre", 6, 1, 10),
        ])
        scorer = TrialScorer(trial)
        scorecard = scorer.score()

        assert len(scorecard.scores) == 2
        assert scorecard.scores[0].provider_name == "good"
        assert (
            scorecard.scores[0].composite_score
            >= scorecard.scores[1].composite_score
        )

    def test_recommend_allocation(self) -> None:
        """Surviving models get proportional allocation."""
        trial = self._build_results([
            ("best", 9, 1, 10),
            ("good", 7, 1, 10),
            ("dropped", 3, 1, 10),
        ])
        scorer = TrialScorer(trial, min_compile_rate=0.50)
        allocation = scorer.recommend()

        # "dropped" should not appear in either tier.
        all_names = [n for n, _ in allocation.tier1] + [
            n for n, _ in allocation.tier2
        ]
        assert "dropped" not in all_names
        assert "best" in all_names or "good" in all_names

        # Total allocation should sum to approximately 1.0.
        total = sum(f for _, f in allocation.tier1) + sum(
            f for _, f in allocation.tier2
        )
        assert abs(total - 1.0) < 0.01

    def test_recommend_single_survivor(self) -> None:
        """A single surviving model gets 100% allocation."""
        trial = self._build_results([("solo", 8, 1, 10)])
        scorer = TrialScorer(trial)
        allocation = scorer.recommend()

        assert len(allocation.tier1) == 1
        assert allocation.tier1[0][0] == "solo"
        # With only 1 model, tier2 is empty and its share goes to tier1.
        assert allocation.tier2_minimum_pct == 0.0
        assert abs(allocation.tier1[0][1] - 1.0) < 0.01

    def test_recommend_no_survivors(self) -> None:
        """No models pass — empty allocation."""
        trial = self._build_results([("weak", 2, 1, 10)])
        scorer = TrialScorer(trial, min_compile_rate=0.50)
        allocation = scorer.recommend()
        assert allocation.tier1 == []
        assert allocation.tier2 == []

    def test_per_category_rates(self) -> None:
        """Per-category compile rates are tracked."""
        results = TrialResults(
            results=[
                _make_result("A-MTH-0001", "m", True),
                _make_result("A-MTH-0002", "m", True),
                _make_result("A-STR-0001", "m", False),
                _make_result("A-STR-0002", "m", True),
            ],
            started_at="t0",
            finished_at="t1",
        )
        scorer = TrialScorer(results)
        scorecard = scorer.score()
        s = scorecard.scores[0]
        assert s.per_category["A-MTH"] == 1.0
        assert s.per_category["A-STR"] == 0.5

    def test_scorecard_json_round_trip(self) -> None:
        """Scorecard serialises to JSON and deserialises back."""
        scorecard = ModelScorecard(
            scores=[
                ModelScore(
                    provider_name="model-a",
                    first_pass_compile_rate=0.8,
                    correction_success_rate=0.5,
                    diff_agreement_rate=0.5,
                    cost_per_accepted=0.002,
                    composite_score=0.65,
                    passed=True,
                    per_category={"A-MTH": 0.9, "A-STR": 0.7},
                ),
            ],
            dropped=["model-b"],
            trial_task_count=500,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scorecard.json"
            TrialScorer.save_scorecard(scorecard, path)

            loaded = TrialScorer.load_scorecard(path)
            assert loaded.trial_task_count == 500
            assert len(loaded.scores) == 1
            assert loaded.scores[0].provider_name == "model-a"
            assert loaded.scores[0].first_pass_compile_rate == 0.8
            assert loaded.scores[0].per_category["A-MTH"] == 0.9
            assert loaded.dropped == ["model-b"]

    def test_scorecard_json_structure(self) -> None:
        """Verify the JSON structure matches expected schema."""
        scorecard = ModelScorecard(
            scores=[
                ModelScore(
                    provider_name="m",
                    first_pass_compile_rate=0.8,
                    correction_success_rate=0.5,
                    diff_agreement_rate=0.5,
                    cost_per_accepted=0.002,
                    composite_score=0.65,
                    passed=True,
                    per_category={},
                ),
            ],
            dropped=[],
            trial_task_count=10,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sc.json"
            TrialScorer.save_scorecard(scorecard, path)
            data = json.loads(path.read_text())

            assert "trial_task_count" in data
            assert "scores" in data
            assert "dropped" in data
            assert isinstance(data["scores"], list)
            assert data["scores"][0]["provider_name"] == "m"
            assert "composite_score" in data["scores"][0]
            assert "per_category" in data["scores"][0]
