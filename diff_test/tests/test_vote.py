"""
Tests for diff_test.vote — Story 2.11.1.

Tests majority voting across exact, normalised, and numeric strategies.
"""
from __future__ import annotations

import pytest

from diff_test.vote import majority_vote, _majority_threshold


# ---------------------------------------------------------------------------
# _majority_threshold
# ---------------------------------------------------------------------------

class TestMajorityThreshold:
    def test_threshold_1(self) -> None:
        assert _majority_threshold(1) == 1

    def test_threshold_2(self) -> None:
        assert _majority_threshold(2) == 2

    def test_threshold_3(self) -> None:
        assert _majority_threshold(3) == 2

    def test_threshold_4(self) -> None:
        assert _majority_threshold(4) == 3

    def test_threshold_5(self) -> None:
        assert _majority_threshold(5) == 3


# ---------------------------------------------------------------------------
# Unanimous agreement
# ---------------------------------------------------------------------------

class TestUnanimous:
    def test_all_agree_exact(self) -> None:
        outputs = {"python": "42", "go": "42", "c": "42"}
        result, langs = majority_vote(outputs)
        assert result == "42"
        assert sorted(langs) == ["c", "go", "python"]

    def test_all_agree_two_langs(self) -> None:
        outputs = {"python": "hello", "go": "hello"}
        result, langs = majority_vote(outputs)
        assert result == "hello"
        assert sorted(langs) == ["go", "python"]


# ---------------------------------------------------------------------------
# Majority vote (2 of 3 agree)
# ---------------------------------------------------------------------------

class TestMajorityVote:
    def test_two_of_three_agree(self) -> None:
        outputs = {"python": "42", "go": "42", "c": "43"}
        result, langs = majority_vote(outputs)
        assert result == "42"
        assert sorted(langs) == ["go", "python"]

    def test_two_of_three_different_outlier(self) -> None:
        outputs = {"python": "hello", "go": "world", "c": "hello"}
        result, langs = majority_vote(outputs)
        assert result == "hello"
        assert sorted(langs) == ["c", "python"]

    def test_three_of_four_agree(self) -> None:
        outputs = {"python": "x", "go": "x", "c": "x", "toke": "y"}
        result, langs = majority_vote(outputs)
        assert result == "x"
        assert "toke" not in langs


# ---------------------------------------------------------------------------
# Two-way tie
# ---------------------------------------------------------------------------

class TestTwoWayTie:
    def test_two_way_tie_four_voters(self) -> None:
        """2 vs 2 — no strict majority (need >2 of 4 = 3)."""
        outputs = {"python": "a", "go": "b", "c": "a", "toke": "b"}
        result, langs = majority_vote(outputs)
        assert result == ""
        assert langs == []

    def test_two_way_tie_two_voters(self) -> None:
        """2 voters, both different — need >1 = 2 to agree."""
        outputs = {"python": "x", "go": "y"}
        result, langs = majority_vote(outputs)
        assert result == ""
        assert langs == []


# ---------------------------------------------------------------------------
# All disagree
# ---------------------------------------------------------------------------

class TestAllDisagree:
    def test_three_way_split(self) -> None:
        outputs = {"python": "a", "go": "b", "c": "c"}
        result, langs = majority_vote(outputs)
        assert result == ""
        assert langs == []

    def test_four_way_split(self) -> None:
        outputs = {"python": "1", "go": "2", "c": "3", "toke": "4"}
        result, langs = majority_vote(outputs)
        assert result == ""
        assert langs == []


# ---------------------------------------------------------------------------
# Single voter (degenerate case)
# ---------------------------------------------------------------------------

class TestSingleVoter:
    def test_single_voter_is_majority(self) -> None:
        """With 1 voter, threshold is 1, so the single voter wins."""
        outputs = {"python": "42"}
        result, langs = majority_vote(outputs)
        assert result == "42"
        assert langs == ["python"]

    def test_empty_outputs(self) -> None:
        result, langs = majority_vote({})
        assert result == ""
        assert langs == []


# ---------------------------------------------------------------------------
# Whitespace-normalised matching
# ---------------------------------------------------------------------------

class TestWhitespaceNormalised:
    def test_trailing_newline_match(self) -> None:
        outputs = {"python": "42\n", "go": "42", "c": "42\n"}
        result, langs = majority_vote(outputs)
        assert result.strip() == "42"
        assert len(langs) >= 2

    def test_extra_spaces_match(self) -> None:
        outputs = {"python": "hello  world", "go": "hello world", "c": "hello world"}
        result, langs = majority_vote(outputs)
        assert len(langs) >= 2

    def test_whitespace_only_differences(self) -> None:
        outputs = {"python": " 42 ", "go": "42", "c": "  42  "}
        result, langs = majority_vote(outputs)
        assert result.strip() == "42"
        assert len(langs) >= 2


# ---------------------------------------------------------------------------
# Numeric near-equality
# ---------------------------------------------------------------------------

class TestNumericVoting:
    def test_float_near_equal(self) -> None:
        outputs = {
            "python": "3.14159265",
            "go": "3.14159266",
            "c": "3.14159265",
        }
        result, langs = majority_vote(outputs)
        assert len(langs) >= 2
        assert float(result) == pytest.approx(3.14159265, rel=1e-6)

    def test_integer_as_float(self) -> None:
        outputs = {"python": "42", "go": "42.0", "c": "42"}
        result, langs = majority_vote(outputs)
        assert len(langs) >= 2

    def test_non_numeric_not_matched(self) -> None:
        """Non-numeric outputs don't trigger numeric voting."""
        outputs = {"python": "hello", "go": "world", "c": "hello"}
        result, langs = majority_vote(outputs)
        # Should match via exact or normalised, not numeric
        assert result == "hello"
        assert sorted(langs) == ["c", "python"]

    def test_mixed_numeric_and_text(self) -> None:
        """Some outputs numeric, some not — numeric can't form majority."""
        outputs = {"python": "42", "go": "42", "c": "not a number"}
        # Exact match: python and go agree on "42"
        result, langs = majority_vote(outputs)
        assert result == "42"
        assert sorted(langs) == ["go", "python"]
