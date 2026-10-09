"""Diagnosis service tests: the ``FailureEvent`` -> ``DiagnosisResult`` contract.

Covers the output shape, the human-review routing, the reconciliation with the
reported failure type, and the honesty properties (evidence traceable, cautious
language, limitations always present).
"""

from __future__ import annotations

import pytest
from autoheal_contracts import (
    CONFIDENCE_HUMAN_REVIEW_THRESHOLD,
    CONTRACT_SCHEMA_VERSION,
    DiagnosisResult,
    EvidenceSource,
    FailureType,
    RecommendedAction,
    RiskLevel,
)
from autoheal_diagnosis import diagnose

pytestmark = pytest.mark.diagnosis

DEPENDENCY_LOG = (
    "Run actions/checkout@v4\n"
    "Run pip install -e .\n"
    "Traceback (most recent call last):\n"
    '  File "src/app/main.py", line 5, in <module>\n'
    "    import requests\n"
    "ModuleNotFoundError: No module named 'requests'\n"
    "Error: Process completed with exit code 1.\n"
)

TEST_LOG = (
    "Run pytest -q\n"
    "FAILED tests/test_cart.py::test_total_with_discount - AssertionError: expected 2, got 3\n"
    "============================== 1 failed, 5 passed in 0.42s ===========================\n"
)

AMBIGUOUS_LOG = (
    "Run ./scripts/deploy.sh\n[info] starting deployment\nProcess completed with exit code 7.\n"
)


class TestOutputShape:
    def test_returns_a_validated_diagnosis_result(self, service, make_event):
        result = service.diagnose(make_event(DEPENDENCY_LOG, "dependency_resolution"))
        assert isinstance(result, DiagnosisResult)

    def test_incident_id_is_preserved(self, service, make_event):
        event = make_event(
            DEPENDENCY_LOG, "dependency_resolution", incident_id="inc-20261009-zzzzzz"
        )
        assert service.diagnose(event).incident_id == "inc-20261009-zzzzzz"

    def test_schema_version_is_the_contract_version(self, service, make_event):
        result = service.diagnose(make_event(DEPENDENCY_LOG, "dependency_resolution"))
        assert result.schema_version == CONTRACT_SCHEMA_VERSION

    def test_failure_type_comes_from_the_event(self, service, make_event):
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert result.failure_type is FailureType.TEST_FAILURE

    def test_probable_cause_is_present_and_non_trivial(self, service, make_event):
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert len(result.probable_cause) >= 10
        assert result.probable_cause.strip() == result.probable_cause

    def test_evidence_is_structured_and_verifiable(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = service.diagnose(event)
        assert len(result.evidence) >= 1
        for item in result.evidence:
            assert item.source is EvidenceSource.FAILURE_LOGS
            assert item.quote in event.logs
            assert len(item.explanation) >= 10

    def test_every_quote_offset_is_the_real_offset(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = service.diagnose(event)
        for item in result.evidence:
            assert item.log_offset == event.logs.find(item.quote)

    def test_the_contracts_own_verifier_reports_no_unverified_quotes(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = service.diagnose(event)
        report = result.verify_evidence(event)
        assert report.logs_available is True
        assert report.verified is True
        assert report.unverified_quotes == ()

    def test_confidence_is_bounded(self, service, make_event):
        for logs, ftype in (
            (DEPENDENCY_LOG, "dependency_resolution"),
            (TEST_LOG, "test_failure"),
            (AMBIGUOUS_LOG, "unknown"),
        ):
            result = service.diagnose(make_event(logs, ftype))
            assert 0.0 <= result.confidence <= 1.0

    def test_confidence_is_heuristic_not_calibrated(self, service, make_event):
        """The engine emits a discrete ladder, not a fitted probability."""
        result = service.diagnose(make_event(DEPENDENCY_LOG, "dependency_resolution"))
        assert result.confidence in {0.0, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85}

    def test_risk_level_and_action_are_contract_enums(self, service, make_event):
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert result.risk_level in set(RiskLevel)
        assert result.recommended_action in set(RecommendedAction)

    def test_limitations_are_always_present(self, service, make_event):
        for logs, ftype in (
            (DEPENDENCY_LOG, "dependency_resolution"),
            (TEST_LOG, "test_failure"),
            (AMBIGUOUS_LOG, "unknown"),
        ):
            result = service.diagnose(make_event(logs, ftype))
            assert len(result.limitations) >= 10
            assert "hypothesis" in result.limitations
            assert "validation" in result.limitations

    def test_diagnosed_at_is_not_before_the_failure(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = service.diagnose(event)
        assert result.diagnosed_at >= event.timestamp

    def test_diagnosis_producer_is_recorded(self, service, make_event):
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert result.diagnosis_producer == "autoheal-diagnosis-rules@0.1.0"


class TestReconciliationWithTheReportedType:
    def test_evidence_that_agrees_with_the_event_is_reported(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = service.diagnose(event)
        assert result.failure_type is FailureType.DEPENDENCY_RESOLUTION
        assert result.recommended_action is RecommendedAction.PIN_DEPENDENCY

    def test_evidence_that_disagrees_escalates_instead_of_relabelling(self, service, make_event):
        """The contract forbids changing the failure type, so it escalates."""
        event = make_event(DEPENDENCY_LOG, "timeout")
        result = service.diagnose(event)
        assert result.failure_type is FailureType.TIMEOUT
        assert result.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
        assert result.requires_human_review is True
        assert result.confidence <= 0.4
        assert "dependency_resolution" in result.probable_cause

    def test_a_contradiction_is_disclosed_in_the_limitations(self, service, make_event):
        result = service.diagnose(make_event(DEPENDENCY_LOG, "timeout"))
        assert "disagree" in result.limitations
        assert "relabel" in result.limitations

    def test_a_reported_type_the_engine_does_not_cover_escalates(self, service, make_event):
        event = make_event(TEST_LOG, "security_scan")
        result = service.diagnose(event)
        assert result.failure_type is FailureType.SECURITY_SCAN
        assert result.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
        assert result.requires_human_review is True


class TestHumanReviewRouting:
    def test_unknown_always_requires_review(self, service, make_event):
        result = service.diagnose(make_event(AMBIGUOUS_LOG, "unknown"))
        assert result.requires_human_review is True
        assert result.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN

    def test_low_risk_low_doubt_work_does_not_force_review(self, service, make_event):
        """Not everything is escalated: a plain assertion failure is safe to hand on."""
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert result.risk_level is RiskLevel.LOW
        assert result.requires_human_review is False

    def test_a_dependency_change_always_requires_review(self, service, make_event):
        result = service.diagnose(make_event(DEPENDENCY_LOG, "dependency_resolution"))
        assert result.risk_level is RiskLevel.MEDIUM
        assert result.requires_human_review is True

    def test_low_confidence_always_requires_review(self, service, make_event):
        result = service.diagnose(make_event(AMBIGUOUS_LOG, "unknown"))
        assert result.confidence < CONFIDENCE_HUMAN_REVIEW_THRESHOLD
        assert result.requires_human_review is True

    def test_no_result_ever_skips_the_review_the_contract_requires(self, service, make_event):
        """Construct the payload through the engine and let the contract judge it."""
        for logs, ftype in (
            (DEPENDENCY_LOG, "dependency_resolution"),
            (TEST_LOG, "test_failure"),
            (AMBIGUOUS_LOG, "unknown"),
            (DEPENDENCY_LOG, "timeout"),
        ):
            result = service.diagnose(make_event(logs, ftype))
            DiagnosisResult.model_validate(
                result.model_dump(), context={"failure_event": make_event(logs, ftype)}
            )


class TestCautiousLanguage:
    @pytest.mark.parametrize("phrase", ["definitely", "must have", "obviously", "clearly the"])
    def test_probable_cause_avoids_certainty(self, service, make_event, phrase):
        for logs, ftype in ((DEPENDENCY_LOG, "dependency_resolution"), (TEST_LOG, "test_failure")):
            assert phrase not in service.diagnose(make_event(logs, ftype)).probable_cause.lower()

    def test_probable_cause_describes_behaviour_not_intent(self, service, make_event):
        result = service.diagnose(make_event(TEST_LOG, "test_failure"))
        assert "expected" in result.probable_cause
        assert "developer" not in result.probable_cause


class TestInputImmutability:
    def test_the_event_is_not_mutated(self, service, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        before = event.model_dump_json()
        service.diagnose(event)
        assert event.model_dump_json() == before

    def test_the_event_is_not_mutated_by_an_unknown_result(self, service, make_event):
        event = make_event(AMBIGUOUS_LOG, "unknown")
        before = event.model_dump_json()
        service.diagnose(event)
        assert event.model_dump_json() == before


class TestPublicInterface:
    def test_module_level_diagnose_matches_the_service(self, make_event):
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = diagnose(event)
        assert isinstance(result, DiagnosisResult)
        assert result.failure_type is FailureType.DEPENDENCY_RESOLUTION

    def test_diagnose_accepts_use_llm_without_a_provider(self, make_event):
        """Rule-only mode must work with the LLM flag on and no provider."""
        event = make_event(DEPENDENCY_LOG, "dependency_resolution")
        result = diagnose(event, use_llm=True)
        assert result.failure_type is FailureType.DEPENDENCY_RESOLUTION

    def test_explain_reports_the_rule_that_produced_the_answer(self, service, make_event):
        classification = service.explain(make_event(DEPENDENCY_LOG, "dependency_resolution"))
        assert classification.rule_id == "dependency-missing-module"

    def test_unknown_result_builds_the_safe_fallback(self, make_event):
        from autoheal_diagnosis import unknown_result

        result = unknown_result(make_event(AMBIGUOUS_LOG, "unknown"))
        assert result.failure_type is FailureType.UNKNOWN
        assert result.requires_human_review is True
        assert result.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
