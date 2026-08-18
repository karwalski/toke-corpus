"""Tests for the difficulty scaler and chain (Story 9.3.4)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline.difficulty_scaler import DifficultyScaler
from pipeline.difficulty_chain import DifficultyChain


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_ADD = "m=add;f=add(a:i64;b:i64):i64{<a+b}"
SIMPLE_MUL = "m=mul;f=mul(x:i64;y:i64):i64{<x*y}"
SIMPLE_STR = 'm=greet;f=greet(name:$str):$str{<name}'
SIMPLE_BOOL = "m=check;f=check(a:bool):bool{<a}"
NO_PARAMS = "m=constant;f=constant():i64{<42}"
SIMPLE_F64 = "m=half;f=half(x:f64):f64{<x/2.0}"


# ---------------------------------------------------------------------------
# Level 1 -> 2: Input validation
# ---------------------------------------------------------------------------

class TestLevel1To2:
    """Adding input validation guards."""

    def test_numeric_guard_added(self):
        scaler = DifficultyScaler()
        result = scaler.scale_up(SIMPLE_ADD, 1)
        assert result is not None
        assert "if a<0" in result
        assert "<0" in result
        assert "el{" in result

    def test_guard_preserves_original_logic(self):
        scaler = DifficultyScaler()
        result = scaler.scale_up(SIMPLE_ADD, 1)
        assert result is not None
        # The original return expression should still appear in the el branch
        assert "a+b" in result

    def test_string_param_guard(self):
        scaler = DifficultyScaler()
        result = scaler.scale_up(SIMPLE_STR, 1)
        assert result is not None
        assert 'name=""' in result

    def test_no_params_returns_none(self):
        scaler = DifficultyScaler()
        result = scaler.scale_up(NO_PARAMS, 1)
        assert result is None

    def test_bool_param_no_numeric_guard(self):
        """Bool params cannot use <0 guard; no string either -> None."""
        scaler = DifficultyScaler()
        result = scaler.scale_up(SIMPLE_BOOL, 1)
        assert result is None

    def test_f64_guard(self):
        scaler = DifficultyScaler()
        result = scaler.scale_up(SIMPLE_F64, 1)
        assert result is not None
        assert "if x<0" in result
        assert "0.0" in result


# ---------------------------------------------------------------------------
# Level 2 -> 3: Helper function extraction
# ---------------------------------------------------------------------------

class TestLevel2To3:
    """Extracting a helper function."""

    def test_helper_extracted(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        result = scaler.scale_up(level2, 2)
        assert result is not None
        # Should contain two f= declarations
        assert result.count("f=") >= 2

    def test_helper_has_return(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        result = scaler.scale_up(level2, 2)
        assert result is not None
        # The helper should have a return expression with the original logic
        assert "a+b" in result

    def test_helper_called_in_main(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        result = scaler.scale_up(level2, 2)
        assert result is not None
        # Main function should call the helper and bind result to r
        assert "let r=" in result
        assert "<r" in result

    def test_helper_name_derived_from_main(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        result = scaler.scale_up(level2, 2)
        assert result is not None
        # Helper name should be based on "add" -> "addh"
        assert "f=addh(" in result


# ---------------------------------------------------------------------------
# Level 3 -> 4: Array accumulation
# ---------------------------------------------------------------------------

class TestLevel3To4:
    """Converting scalar returns to array accumulation."""

    def test_array_pattern_added(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        level3 = scaler.scale_up(level2, 2)
        assert level3 is not None
        result = scaler.scale_up(level3, 3)
        assert result is not None
        assert "@(i64)" in result
        assert ".push(" in result
        assert ".get(0)" in result

    def test_array_uses_mut(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        level3 = scaler.scale_up(level2, 2)
        assert level3 is not None
        result = scaler.scale_up(level3, 3)
        assert result is not None
        assert "let arr=mut." in result


# ---------------------------------------------------------------------------
# Level 4 -> 5: Error handling
# ---------------------------------------------------------------------------

class TestLevel4To5:
    """Adding error type wrapping."""

    def test_error_type_declared(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        level3 = scaler.scale_up(level2, 2)
        assert level3 is not None
        level4 = scaler.scale_up(level3, 3)
        assert level4 is not None
        result = scaler.scale_up(level4, 4)
        assert result is not None
        assert "e=" in result
        assert "invalid" in result

    def test_error_preserves_module(self):
        scaler = DifficultyScaler()
        level2 = scaler.scale_up(SIMPLE_ADD, 1)
        assert level2 is not None
        level3 = scaler.scale_up(level2, 2)
        assert level3 is not None
        level4 = scaler.scale_up(level3, 3)
        assert level4 is not None
        result = scaler.scale_up(level4, 4)
        assert result is not None
        assert result.startswith("m=add;")


# ---------------------------------------------------------------------------
# Edge cases and invalid inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary and invalid input handling."""

    def test_level_beyond_5_returns_none(self):
        scaler = DifficultyScaler()
        assert scaler.scale_up(SIMPLE_ADD, 5) is None

    def test_level_0_returns_none(self):
        scaler = DifficultyScaler()
        assert scaler.scale_up(SIMPLE_ADD, 0) is None

    def test_empty_source_returns_none(self):
        scaler = DifficultyScaler()
        assert scaler.scale_up("", 1) is None

    def test_malformed_source_returns_none(self):
        scaler = DifficultyScaler()
        assert scaler.scale_up("not valid toke at all", 1) is None


# ---------------------------------------------------------------------------
# Chain integration (mocking tkc)
# ---------------------------------------------------------------------------

class TestDifficultyChain:
    """Chain builder with mocked tkc validation."""

    def test_chain_builds_from_simple_seed(self):
        chain = DifficultyChain()
        with patch.object(chain, "_tkc_check", return_value=True):
            result = chain.build_chain(SIMPLE_ADD)
        # Should have level 1 (seed) + at least level 2
        assert len(result) >= 2
        assert result[0][0] == 1
        assert result[0][1] == SIMPLE_ADD

    def test_chain_stops_on_validation_failure(self):
        chain = DifficultyChain()
        call_count = 0

        def _mock_check(source):
            nonlocal call_count
            call_count += 1
            # Pass seed (1), pass level 2 (2), fail level 3 (3)
            return call_count <= 2

        with patch.object(chain, "_tkc_check", side_effect=_mock_check):
            result = chain.build_chain(SIMPLE_ADD)
        assert len(result) == 2
        assert result[-1][0] == 2

    def test_chain_stops_when_scaler_returns_none(self):
        chain = DifficultyChain()
        with patch.object(chain, "_tkc_check", return_value=True):
            result = chain.build_chain(NO_PARAMS)
        # NO_PARAMS has no params, so level 1->2 should fail (returns None)
        assert len(result) == 1
        assert result[0][0] == 1

    def test_chain_empty_for_invalid_seed(self):
        chain = DifficultyChain()
        with patch.object(chain, "_tkc_check", return_value=False):
            result = chain.build_chain("broken")
        assert result == []

    def test_chain_deterministic(self):
        chain = DifficultyChain()
        with patch.object(chain, "_tkc_check", return_value=True):
            r1 = chain.build_chain(SIMPLE_ADD)
            r2 = chain.build_chain(SIMPLE_ADD)
        assert r1 == r2

    def test_chain_levels_monotonically_increase(self):
        chain = DifficultyChain()
        with patch.object(chain, "_tkc_check", return_value=True):
            result = chain.build_chain(SIMPLE_ADD)
        levels = [lvl for lvl, _ in result]
        assert levels == sorted(levels)
        assert len(levels) == len(set(levels))
