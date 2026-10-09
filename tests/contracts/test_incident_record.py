"""Contract tests: ``IncidentRecord``.

The aggregate must stay consistent with the payloads it carries: a status may
never claim an outcome the validation does not support, and no payload may
belong to a different incident.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from autoheal_contracts import (
    CONTRACT_SCHEMA_VERSION,
    IncidentRecord,
    IncidentStatus,
)
from pydantic import ValidationError

from tests._helpers import diagnosis_payload, validation_payload

pytestmark = pytest.mark.contracts


@pytest.fixture
def event(event_factory):
    return event_factory()


@pytest.fixture
def record(event, incident_factory):
    return incident_factory(event)


@pytest.fixture
def diagnosed(record, diagnosis_factory):
    return record.attach_diagnosis(diagnosis_factory(record.failure_event))


class TestFreshRecord:
    def test_starts_received_with_no_diagnosis_or_validation(self, record, event):
        assert record.status is IncidentStatus.RECEIVED
        assert record.diagnosis is None
        assert record.validation is None
        assert record.failure_event == event
        assert record.schema_version == CONTRACT_SCHEMA_VERSION

    def test_history_starts_at_received(self, record):
        assert [t.status for t in record.status_history] == [IncidentStatus.RECEIVED]

    def test_created_at_not_before_the_failure(self, record):
        assert record.created_at >= record.failure_event.timestamp

    def test_a_record_before_the_failure_is_rejected(self, event):
        with pytest.raises(ValueError) as exc:
            IncidentRecord.from_failure_event(event, now=event.timestamp - timedelta(minutes=1))
        assert "before the failure" in str(exc.value)

    def test_is_recovery_verified_is_false_without_validation(self, record):
        assert record.is_recovery_verified is False

    def test_unknown_failure_type_needs_review(self, event_factory, incident_factory):
        event = event_factory(failure_type="unknown")
        assert incident_factory(event).needs_human_review is True


class TestDiagnosisAttachment:
    def test_high_confidence_low_risk_moves_to_diagnosed(self, record, diagnosis_factory):
        event = record.failure_event
        diagnosis = diagnosis_factory(event, risk_level="low", requires_human_review=False)
        updated = record.attach_diagnosis(diagnosis)
        assert updated.status is IncidentStatus.DIAGNOSED
        assert updated.diagnosis is diagnosis

    def test_human_review_diagnosis_moves_to_awaiting_review(self, record, diagnosis_factory):
        updated = record.attach_diagnosis(diagnosis_factory(record.failure_event))
        assert updated.status is IncidentStatus.AWAITING_REVIEW
        assert updated.needs_human_review is True

    def test_history_records_the_transition(self, record, diagnosis_factory):
        updated = record.attach_diagnosis(diagnosis_factory(record.failure_event))
        assert [t.status for t in updated.status_history] == [
            IncidentStatus.RECEIVED,
            IncidentStatus.AWAITING_REVIEW,
        ]

    def test_original_record_is_not_mutated(self, record, diagnosis_factory):
        record.attach_diagnosis(diagnosis_factory(record.failure_event))
        assert record.status is IncidentStatus.RECEIVED
        assert record.diagnosis is None

    def test_updated_at_moves_forward(self, record, diagnosis_factory):
        updated = record.attach_diagnosis(diagnosis_factory(record.failure_event))
        assert updated.updated_at >= record.updated_at

    def test_received_status_rejects_a_diagnosis(self, record, diagnosis_factory):
        """The status and the payload must be applied together, never split."""
        with pytest.raises(ValidationError) as exc:
            record.evolve(diagnosis=diagnosis_factory(record.failure_event))
        assert "must not carry a diagnosis" in str(exc.value)

    def test_diagnosed_status_requires_a_diagnosis(self, record):
        with pytest.raises(ValidationError) as exc:
            record.evolve(status=IncidentStatus.DIAGNOSED)
        assert "requires a diagnosis" in str(exc.value)


class TestValidationAttachment:
    def test_passing_validation_moves_to_validated(self, diagnosed, validation_factory):
        event = diagnosed.failure_event
        updated = diagnosed.attach_validation(validation_factory(event))
        assert updated.status is IncidentStatus.VALIDATED
        assert updated.is_recovery_verified is True
        assert updated.is_terminal is True

    def test_failing_validation_moves_to_validation_failed(self, diagnosed, validation_factory):
        event = diagnosed.failure_event
        updated = diagnosed.attach_validation(
            validation_factory(event, passed=False, tests_failed=2)
        )
        assert updated.status is IncidentStatus.VALIDATION_FAILED
        assert updated.is_recovery_verified is False

    def test_validated_status_rejects_a_failing_validation(self, diagnosed, validation_factory):
        with pytest.raises(ValidationError) as exc:
            diagnosed.evolve(
                validation=validation_factory(diagnosed.failure_event, passed=False),
                status=IncidentStatus.VALIDATED,
            )
        assert "passing validation" in str(exc.value)

    def test_validation_failed_status_rejects_a_passing_validation(
        self, diagnosed, validation_factory
    ):
        with pytest.raises(ValidationError):
            diagnosed.evolve(
                validation=validation_factory(diagnosed.failure_event),
                status=IncidentStatus.VALIDATION_FAILED,
            )

    def test_validating_status_requires_a_validation(self, diagnosed, validation_factory):
        with pytest.raises(ValidationError):
            diagnosed.evolve(status=IncidentStatus.VALIDATING)


class TestCrossPayloadConsistency:
    def test_diagnosis_from_another_incident_is_rejected(self, record):
        from autoheal_contracts import DiagnosisResult

        other = DiagnosisResult.model_validate(
            diagnosis_payload(record.failure_event, incident_id="inc-20261009-zzzzzz")
        )
        with pytest.raises(ValidationError) as exc:
            record.attach_diagnosis(other)
        assert "incident_id mismatch" in str(exc.value)

    def test_validation_from_another_incident_is_rejected(self, diagnosed):
        from autoheal_contracts import ValidationResult

        other = ValidationResult.model_validate(
            validation_payload(diagnosed.failure_event, incident_id="inc-20261009-zzzzzz")
        )
        with pytest.raises(ValidationError) as exc:
            diagnosed.attach_validation(other)
        assert "incident_id mismatch" in str(exc.value)

    def test_updated_at_before_created_at_is_rejected(self, record):
        with pytest.raises(ValidationError) as exc:
            record.evolve(updated_at=record.created_at - timedelta(minutes=5))
        assert "updated_at predates created_at" in str(exc.value)

    def test_history_must_end_with_the_current_status(self, record):
        with pytest.raises(ValidationError) as exc:
            record.evolve(status=IncidentStatus.ABANDONED)
        assert "must end with the current status" in str(exc.value)

    def test_history_must_start_at_received(self, record):
        with pytest.raises(ValidationError) as exc:
            record.evolve(status_history=[{"status": "diagnosed", "at": record.created_at}])
        assert "must start with 'received'" in str(exc.value)

    def test_timestamps_are_serialised_with_zulu_suffix(self, record):
        dumped = record.model_dump()
        assert dumped["created_at"].endswith("Z")
        assert dumped["updated_at"].endswith("Z")

    def test_json_round_trip(self, diagnosed, validation_factory):
        full = diagnosed.attach_validation(validation_factory(diagnosed.failure_event))
        assert IncidentRecord.model_validate_json(full.model_dump_json()) == full
