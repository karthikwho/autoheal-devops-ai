"""Contract tests: ``ValidationResult``.

These lock down the rule that a *proposed* fix is never a *validated* fix: a
pass with no executed tests, or a pass with failures, is rejected outright.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from autoheal_contracts import ValidationResult, utcnow
from pydantic import ValidationError

from tests._helpers import validation_payload

pytestmark = pytest.mark.contracts


@pytest.fixture
def event(event_factory):
    return event_factory()


@pytest.fixture
def validate(event):
    """Validate a validation payload for ``event``."""

    def _make(payload, **overrides):
        return ValidationResult.model_validate(
            validation_payload(event, **{**payload, **overrides})
        )

    return _make


class TestValidPayload:
    def test_minimal_valid_payload(self, event, validate):
        result = validate({"validation_summary": "All 42 tests passed after the fix."})
        assert result.passed is True
        assert result.tests_executed == 42
        assert result.tests_failed == 0

    def test_scope_defaults_to_test_suite(self, event, validate):
        assert validate({}).validation_scope.value == "test_suite"

    def test_every_scope_is_accepted(self, event):
        from autoheal_contracts import ValidationScope

        for scope in ValidationScope:
            result = ValidationResult.model_validate(
                validation_payload(event, validation_scope=scope.value)
            )
            assert result.validation_scope is scope

    def test_test_failure_details_are_accepted(self, event):
        result = ValidationResult.model_validate(
            validation_payload(
                event,
                passed=False,
                tests_failed=1,
                failure_details=[
                    {
                        "test_name": "tests/test_login.py::test_login_timeout",
                        "message": "expected 200 got 500",
                        "file_path": "tests/test_login.py",
                        "line_number": 42,
                    }
                ],
            )
        )
        assert result.failure_details[0].line_number == 42

    def test_duration_and_metadata_are_accepted(self, event):
        result = ValidationResult.model_validate(
            validation_payload(
                event,
                command="pytest -q",
                environment="ubuntu-latest",
                duration_seconds=12.34,
                remediation_reference="rem-0001",
            )
        )
        assert result.duration_seconds == 12.34
        assert result.remediation_reference == "rem-0001"


class TestMissingRequiredFields:
    @pytest.mark.parametrize(
        "field",
        [
            "incident_id",
            "passed",
            "tests_executed",
            "tests_failed",
            "validation_summary",
            "validated_at",
        ],
    )
    def test_missing_field_is_rejected(self, event, field):
        payload = validation_payload(event)
        del payload[field]
        with pytest.raises(ValidationError):
            ValidationResult.model_validate(payload)

    def test_empty_payload_reports_every_required_field(self):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate({})
        missing = {
            str(error["loc"][0]) for error in exc.value.errors() if error["type"] == "missing"
        }
        assert missing == {
            "incident_id",
            "passed",
            "tests_executed",
            "tests_failed",
            "validation_summary",
            "validated_at",
        }


class TestProposedIsNotValidated:
    """The invariants that stop a skipped suite masquerading as a green run."""

    def test_pass_with_no_tests_executed_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(
                validation_payload(
                    event,
                    passed=True,
                    tests_executed=0,
                    tests_failed=0,
                    validation_summary="No tests were run because the runner was unavailable.",
                )
            )
        assert "tests_executed >= 1" in str(exc.value)

    def test_pass_with_failures_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(validation_payload(event, passed=True, tests_failed=1))
        assert "tests_failed == 0" in str(exc.value)

    def test_failures_exceeding_executions_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(
                validation_payload(event, passed=False, tests_executed=3, tests_failed=5)
            )
        assert "cannot exceed" in str(exc.value)

    def test_more_failure_details_than_failures_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(
                validation_payload(
                    event,
                    passed=False,
                    tests_executed=2,
                    tests_failed=1,
                    failure_details=[
                        {"test_name": "test_a", "message": "boom"},
                        {"test_name": "test_b", "message": "boom"},
                    ],
                )
            )
        assert "tests_failed is 1" in str(exc.value)

    def test_failure_without_tests_is_accepted_but_not_verified(self, event):
        """A run that could not execute anything must be able to fail."""
        result = ValidationResult.model_validate(
            validation_payload(
                event,
                passed=False,
                tests_executed=0,
                tests_failed=0,
                validation_summary="The runner crashed before any test started.",
            )
        )
        assert result.is_recovery_verified is False
        assert "no tests were executed" in result.status_note

    def test_failing_run_is_not_recovery(self, event):
        result = ValidationResult.model_validate(
            validation_payload(event, passed=False, tests_failed=2)
        )
        assert result.is_recovery_verified is False
        assert "NOT verified" in result.status_note

    def test_green_run_is_recovery(self, event, validate):
        result = validate({})
        assert result.is_recovery_verified is True
        assert "recovery is verified for this scope only" in result.status_note

    def test_validation_summary_is_mandatory(self, event):
        with pytest.raises(ValidationError):
            ValidationResult.model_validate(validation_payload(event, validation_summary=""))

    def test_unknown_scope_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(
                validation_payload(event, validation_scope="reading_the_tea_leaves")
            )
        assert any(error["type"] == "enum" for error in exc.value.errors())

    def test_negative_counters_are_rejected(self, event):
        for field in ("tests_executed", "tests_failed"):
            with pytest.raises(ValidationError):
                ValidationResult.model_validate(validation_payload(event, **{field: -1}))

    def test_duration_must_not_be_negative(self, event):
        with pytest.raises(ValidationError):
            ValidationResult.model_validate(validation_payload(event, duration_seconds=-1))


class TestTimestamps:
    def test_future_validated_at_is_rejected(self, event):
        with pytest.raises(ValidationError) as exc:
            ValidationResult.model_validate(
                validation_payload(event, validated_at=utcnow() + timedelta(days=1))
            )
        assert "future" in str(exc.value).lower()

    def test_validated_at_is_serialised_with_zulu_suffix(self, event):
        result = ValidationResult.model_validate(validation_payload(event))
        assert result.model_dump()["validated_at"].endswith("Z")


class TestSerialisation:
    def test_json_round_trip(self, event):
        result = ValidationResult.model_validate(validation_payload(event))
        assert ValidationResult.model_validate_json(result.model_dump_json()) == result
