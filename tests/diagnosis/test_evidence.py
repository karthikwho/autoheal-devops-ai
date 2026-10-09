"""Evidence tests: quotes must be traceable to the supplied input.

The headline property is that the engine cannot cite text that is not in the
log. These tests check both directions: the happy path (a real quote with the
right offset) and the rejection path (a quote that was invented).
"""

from __future__ import annotations

import pytest
from autoheal_contracts import EvidenceSource
from autoheal_contracts.diagnosis import find_log_offset
from autoheal_diagnosis.evidence import (
    Evidence,
    evidence_from_match,
    fallback_evidence,
    line_containing,
    split_log_lines,
    verified_evidence,
)

pytestmark = pytest.mark.diagnosis

LOGS = (
    "Run actions/checkout@v4\n"
    "Run pytest -q\n"
    "FAILED tests/test_cart.py::test_total_with_discount - AssertionError: expected 2, got 3\n"
    "============================== 1 failed, 5 passed in 0.42s ===========================\n"
)


class TestQuoteExtraction:
    def test_quote_is_the_whole_line_containing_the_match(self):
        import re

        match = re.compile(r"AssertionError:\s*[^\n]*").search(LOGS)
        assert match is not None
        item = evidence_from_match(LOGS, match, "A failing assertion is reported here.")
        assert item.quote == (
            "FAILED tests/test_cart.py::test_total_with_discount - AssertionError: expected 2, got 3"
        )
        assert item.source is EvidenceSource.FAILURE_LOGS

    def test_quote_is_a_substring_of_the_logs(self):
        import re

        match = re.compile(r"expected 2, got 3").search(LOGS)
        assert match is not None
        item = evidence_from_match(LOGS, match, "The observed value differs from the expected one.")
        assert item.quote in LOGS

    def test_log_offset_matches_the_real_position(self):
        import re

        match = re.compile(r"AssertionError").search(LOGS)
        assert match is not None
        item = evidence_from_match(LOGS, match, "An assertion error is reported.")
        assert item.log_offset == LOGS.find(item.quote)

    def test_line_containing_uses_the_match_position(self):
        assert line_containing(LOGS, LOGS.index("AssertionError")).startswith("FAILED")

    def test_line_containing_handles_the_last_line(self):
        logs = "first line\nsecond line"
        assert line_containing(logs, logs.index("second")) == "second line"

    def test_significant_lines_are_trimmed_but_not_reordered(self):
        assert split_log_lines(LOGS)[0] == "Run actions/checkout@v4"
        assert split_log_lines("\n\n   \n") == []


class TestFallbackEvidence:
    def test_fallback_quote_exists_in_the_logs(self):
        item = fallback_evidence(LOGS)
        assert item.quote in LOGS
        assert item.log_offset == LOGS.find(item.quote)

    def test_fallback_quote_prefers_an_error_like_line(self):
        item = fallback_evidence(
            "[info] starting\n[info] done\nProcess completed with exit code 7.\n"
        )
        assert item.quote == "Process completed with exit code 7."

    def test_fallback_quote_falls_back_to_the_body(self):
        """Degenerate logs still yield a legal, present quote."""
        logs = "a\nb\nc\nd"  # 10 significant characters in total
        item = fallback_evidence(logs)
        assert item.quote in logs
        assert len(item.quote) >= 3


class TestVerification:
    def test_a_present_quote_is_kept(self):
        kept, dropped = verified_evidence(
            (Evidence(EvidenceSource.FAILURE_LOGS, "expected 2, got 3", "An explanation here."),),
            LOGS,
        )
        assert len(kept) == 1
        assert dropped == ()

    def test_a_fabricated_quote_is_dropped(self):
        kept, dropped = verified_evidence(
            (Evidence(EvidenceSource.FAILURE_LOGS, "this never happened", "An invented quote."),),
            LOGS,
        )
        assert kept == ()
        assert len(dropped) == 1
        assert dropped[0].quote == "this never happened"

    def test_a_paraphrased_quote_is_dropped(self):
        """Paraphrase is not a quote."""
        kept, _ = verified_evidence(
            (Evidence(EvidenceSource.FAILURE_LOGS, "expected two, got three", "Paraphrased."),),
            LOGS,
        )
        assert kept == ()

    def test_a_truncated_quote_is_still_a_quote(self):
        kept, _ = verified_evidence(
            (Evidence(EvidenceSource.FAILURE_LOGS, "expected 2", "A prefix of a real line."),),
            LOGS,
        )
        assert len(kept) == 1

    def test_find_log_offset_distinguishes_absent_from_position_zero(self):
        assert find_log_offset("no quote here", "no") == 0
        assert find_log_offset("abc", "zzz") is None
