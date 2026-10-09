"""Contract tests: ``DiagnosisResult``.

The headline behaviour under test is that an evidence quote is checked against
the failure logs, so a diagnosis cannot cite text that is not in the log. Every
"route to human review" invariant is covered too.
"""

from __future__ import annotations

import pytest
from autoheal_contracts import (
    CONFIDENCE_HUMAN_REVIEW_THRESHOLD,
    CONTRACT_SCHEMA_VERSION,
    DiagnosisResult,
    EvidenceItem,
    EvidenceSource,
    FailureType,
    RecommendedAction,
    RiskLevel,
)
from pydantic import ValidationError

from tests._helpers import diagnosis_payload

pytestmark = pytest.mark.contracts

VALID_QUOTE = "test_login_timeout"


@pytest.fixture
def event(event_factory):
    return event_factory()


@pytest.fixture
def diagnose():
    """Validate a diagnosis payload against its event (context supplied)."""

    def _make(event, payload):
        return DiagnosisResult.model_validate(payload, context={"failure_event": event})

    return _make


class TestValidPayload:
    def test_minimal_valid_payload(self, event, diagnose):
        diagnosis = diagnose(event, diagnosis_payload(event))
        assert diagnosis.confidence == 0.62
        assert diagnosis.failure_type is FailureType.TEST_FAILURE
        assert diagnosis.recommended_action is RecommendedAction.FIX_SOURCE_CODE
        assert diagnosis.risk_level is RiskLevel.MEDIUM

    def test_evidence_is_structured(self, event, diagnose):
        item = diagnose(event, diagnosis_payload(event)).evidence[0]
        assert isinstance(item, EvidenceItem)
        assert item.source is EvidenceSource.FAILURE_LOGS
        assert item.quote == VALID_QUOTE
        assert isinstance(item.explanation, str) and len(item.explanation) >= 10

    def test_every_evidence_source_is_accepted(self, event):
        for source in EvidenceSource:
            item = {
                "source": source.value,
                "quote": VALID_QUOTE,
                "explanation": "A quoted line with a stated reason",
            }
            # log_offset is only meaningful for a log-sourced quote
            if source is EvidenceSource.FAILURE_LOGS:
                item["log_offset"] = event.logs.find(VALID_QUOTE)
            diagnosis = DiagnosisResult.model_validate(
                diagnosis_payload(event, evidence=[item]),
                context={"failure_event": event},
            )
            assert diagnosis.evidence[0].source is source

    def test_log_offset_is_rejected_for_non_log_sources(self, event):
        with pytest.raises(ValidationError) as exc:
            DiagnosisResult.model_validate(
                diagnosis_payload(
                    event,
                    evidence=[
                        {
                            "source": "workflow_definition",
                            "quote": VALID_QUOTE,
                            "explanation": "A quoted line with a stated reason",
                            "log_offset": 0,
                        }
                    ],
                ),
                context={"failure_event": event},
            )
        assert "log_offset" in str(exc.value)

    def test_evidence_without_logs_is_not_verified(self, event):
        diagnosis = DiagnosisResult.model_validate(diagnosis_payload(event))
        report = diagnosis.verify_evidence(event)
        assert report.logs_available is True
        assert report.verified is True
        assert report.unverified_quotes == ()

    def test_absent_logs_are_reported_as_unverified(self, event):
        diagnosis = DiagnosisResult.model_validate(diagnosis_payload(event))
        report = diagnosis.verify_evidence(None)
        assert report.logs_available is False
        assert report.verified is False
        assert report.unverified_quotes == (VALID_QUOTE,)


class TestMissingRequiredFields:
    @pytest.mark.parametrize(
        "field",
        [
            "incident_id",
            "failure_type",
            "probable_cause",
            "evidence",
            "confidence",
            "recommended_action",
            "risk_level",
            "requires_human_review",
            "limitations",
            "diagnosed_at",
        ],
    )
    def test_missing_field_is_rejected(self, event, field):
        payload = diagnosis_payload(event)
        del payload[field]
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(payload, context={"failure_event": event})

    def test_schema_version_defaults_to_the_current_contract(self, event):
        payload = diagnosis_payload(event)
        del payload["schema_version"]
        diagnosis = DiagnosisResult.model_validate(payload, context={"failure_event": event})
        assert diagnosis.schema_version == CONTRACT_SCHEMA_VERSION

    def test_empty_evidence_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            DiagnosisResult.model_validate(
                diagnosis_payload(event, evidence=[]), context={"failure_event": event}
            )
        assert "at least 1 item" in str(exc.value)

    def test_evidence_item_needs_quote_and_explanation(self, event):
        for incomplete in (
            {"source": "failure_logs", "explanation": "some explanation here"},
            {"source": "failure_logs", "quote": VALID_QUOTE},
        ):
            with pytest.raises(ValidationError):
                DiagnosisResult.model_validate(
                    diagnosis_payload(event, evidence=[incomplete]),
                    context={"failure_event": event},
                )

    def test_limitations_are_mandatory(self, event):
        """A diagnosis that states no limitations is not accepted."""
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(
                diagnosis_payload(event, limitations=""), context={"failure_event": event}
            )


class TestConfidenceBounds:
    @pytest.mark.parametrize("value", [-0.001, -1, 1.001, 2, 100])
    def test_out_of_range_confidence_is_rejected(self, event, value):
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(
                diagnosis_payload(event, confidence=value, requires_human_review=True),
                context={"failure_event": event},
            )

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0, 0.999])
    def test_in_range_confidence_is_accepted(self, event, value):
        diagnosis = DiagnosisResult.model_validate(
            diagnosis_payload(event, confidence=value, requires_human_review=True),
            context={"failure_event": event},
        )
        assert diagnosis.confidence == value

    def test_low_confidence_forces_human_review(self, event):
        below = round(CONFIDENCE_HUMAN_REVIEW_THRESHOLD - 0.01, 2)
        with pytest.raises(ValidationError) as exc:
            DiagnosisResult.model_validate(
                diagnosis_payload(event, confidence=below, requires_human_review=False),
                context={"failure_event": event},
            )
        assert "requires_human_review" in str(exc.value)


class TestHumanReviewInvariants:
    def test_high_risk_forces_human_review(self, event):
        with pytest.raises(ValidationError) as exc:
            DiagnosisResult.model_validate(
                diagnosis_payload(event, risk_level="high", requires_human_review=False),
                context={"failure_event": event},
            )
        assert "risk_level" in str(exc.value)

    def test_critical_risk_forces_human_review(self, event):
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(
                diagnosis_payload(event, risk_level="critical", requires_human_review=False),
                context={"failure_event": event},
            )

    def test_low_risk_does_not_force_human_review(self, event):
        diagnosis = DiagnosisResult.model_validate(
            diagnosis_payload(event, risk_level="low", requires_human_review=False),
            context={"failure_event": event},
        )
        assert diagnosis.requires_human_review is False

    def test_unknown_failure_type_forces_human_review(self, event_factory):
        event = event_factory(failure_type="unknown")
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(
                diagnosis_payload(event, requires_human_review=False),
                context={"failure_event": event},
            )

    def test_escalation_forces_human_review(self, event):
        with pytest.raises(ValidationError):
            DiagnosisResult.model_validate(
                diagnosis_payload(
                    event, recommended_action="escalate_to_human", requires_human_review=False
                ),
                context={"failure_event": event},
            )


class TestEvidenceVerification:
    def test_fabricated_quote_is_rejected(self, event, diagnose):
        """The core safety property: a quote must exist in the logs."""
        with pytest.raises(ValidationError) as exc:
            diagnose(
                event,
                diagnosis_payload(
                    event,
                    evidence=[
                        {
                            "source": "failure_logs",
                            "quote": "this never happened in the build log",
                            "explanation": "Invented evidence used to look credible",
                        }
                    ],
                ),
            )
        assert "not found in failure logs" in str(exc.value)

    def test_misreported_log_offset_is_rejected(self, event, diagnose):
        with pytest.raises(ValidationError) as exc:
            diagnose(
                event,
                diagnosis_payload(
                    event,
                    evidence=[
                        {
                            "source": "failure_logs",
                            "quote": VALID_QUOTE,
                            "explanation": "The failing assertion names the regressed test",
                            "log_offset": 999_999,
                        }
                    ],
                ),
            )
        assert "offset mismatch" in str(exc.value)

    def test_correct_log_offset_is_accepted(self, event):
        diagnosis = DiagnosisResult.model_validate(
            diagnosis_payload(
                event,
                evidence=[
                    {
                        "source": "failure_logs",
                        "quote": VALID_QUOTE,
                        "explanation": "The failing assertion names the regressed test",
                        "log_offset": event.logs.find(VALID_QUOTE),
                    }
                ],
            ),
            context={"failure_event": event},
        )
        assert diagnosis.evidence[0].log_offset == event.logs.find(VALID_QUOTE)

    def test_quote_edit_changes_nothing(self, event):
        """Substring of the quote is not the quote."""
        diagnosis = DiagnosisResult.model_validate(diagnosis_payload(event))
        assert diagnosis.evidence[0].find_in_logs(event.logs) == event.logs.find(VALID_QUOTE)
        assert diagnosis.evidence[0].find_in_logs("no logs here") is None

    def test_non_log_sources_are_not_required_to_match_logs(self, event):
        diagnosis = DiagnosisResult.model_validate(
            diagnosis_payload(
                event,
                evidence=[
                    {
                        "source": "workflow_definition",
                        "quote": "runs-on: ubuntu-latest",
                        "explanation": "Quoted from the workflow file, not the log",
                    }
                ],
            ),
            context={"failure_event": event},
        )
        assert diagnosis.evidence[0].is_log_verifiable is False
        report = diagnosis.verify_evidence(event)
        assert report.checks[0].note is not None


class TestCoherenceWithFailureEvent:
    def test_incident_id_mismatch_is_rejected(self, event, diagnose):
        with pytest.raises(ValidationError) as exc:
            diagnose(event, diagnosis_payload(event, incident_id="inc-20261009-zzzzzz"))
        assert "incident_id mismatch" in str(exc.value)

    def test_failure_type_mismatch_is_rejected(self, event, diagnose):
        with pytest.raises(ValidationError) as exc:
            diagnose(event, diagnosis_payload(event, failure_type="timeout"))
        assert "failure_type mismatch" in str(exc.value)

    def test_diagnosis_before_failure_is_rejected(self, event, diagnose):
        with pytest.raises(ValidationError) as exc:
            diagnose(event, diagnosis_payload(event, diagnosed_at="2020-01-01T00:00:00Z"))
        assert "predates the failure" in str(exc.value)

    def test_diagnosis_validates_standalone_without_context(self, event):
        """A diagnosis may be validated without its event; quotes are then only
        checkable through ``verify_evidence``."""
        diagnosis = DiagnosisResult.model_validate(
            diagnosis_payload(
                event,
                evidence=[
                    {
                        "source": "failure_logs",
                        "quote": "nothing like this exists",
                        "explanation": "Would be rejected if the event were in context",
                    }
                ],
            )
        )
        assert diagnosis.verify_evidence(event).verified is False


class TestSerialisation:
    def test_json_round_trip(self, event):
        diagnosis = DiagnosisResult.model_validate(diagnosis_payload(event))
        assert DiagnosisResult.model_validate_json(diagnosis.model_dump_json()) == diagnosis
