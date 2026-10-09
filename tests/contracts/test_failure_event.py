"""Contract tests: ``FailureEvent``.

Covers the valid payload, every required field, enum values, and the format
rules on identifiers, timestamps and logs.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from autoheal_contracts import CONTRACT_SCHEMA_VERSION, FailureEvent, FailureType, utcnow
from pydantic import ValidationError

from tests._helpers import base_payload

pytestmark = pytest.mark.contracts


class TestValidPayload:
    def test_minimal_valid_payload(self):
        event = FailureEvent.model_validate(base_payload())
        assert event.incident_id == "inc-20261009-ab12cd"
        assert event.failure_type is FailureType.TEST_FAILURE
        assert event.schema_version == CONTRACT_SCHEMA_VERSION

    def test_optional_fields_default_to_none(self):
        event = FailureEvent.model_validate(base_payload())
        assert event.branch is None
        assert event.job_name is None
        assert event.run_url is None

    def test_optional_fields_accepted(self):
        event = FailureEvent.model_validate(
            base_payload(branch="main", job_name="build", pull_request_number=7)
        )
        assert event.branch == "main"
        assert event.pull_request_number == 7

    def test_every_failure_type_round_trips(self):
        for failure_type in FailureType:
            event = FailureEvent.model_validate(base_payload(failure_type=failure_type))
            assert event.failure_type is failure_type

    def test_commit_sha_is_normalised_to_lowercase(self):
        event = FailureEvent.model_validate(base_payload(commit_sha="A1B2C3D4E5F6"))
        assert event.commit_sha == "a1b2c3d4e5f6"

    def test_short_commit_sha_accepted(self):
        event = FailureEvent.model_validate(base_payload(commit_sha="a1b2c3d"))
        assert len(event.commit_sha) == 7

    def test_timestamp_serialises_with_zulu_suffix(self):
        dumped = FailureEvent.model_validate(base_payload()).model_dump()
        assert dumped["timestamp"].endswith("Z")
        assert "+00:00" not in dumped["timestamp"]

    def test_offset_timestamp_is_normalised_to_utc(self):
        event = FailureEvent.model_validate(base_payload(timestamp="2026-01-01T14:00:00+02:00"))
        assert event.model_dump()["timestamp"] == "2026-01-01T12:00:00Z"

    def test_logs_are_preserved_byte_for_byte(self):
        raw = "line one   \n\n\tline two\n"
        event = FailureEvent.model_validate(base_payload(logs=raw))
        assert event.logs == raw  # not stripped: evidence quotes must stay comparable


class TestMissingRequiredFields:
    @pytest.mark.parametrize(
        "field",
        [
            "incident_id",
            "repository",
            "commit_sha",
            "workflow_name",
            "run_id",
            "failed_step",
            "failure_type",
            "logs",
            "timestamp",
        ],
    )
    def test_missing_field_is_rejected(self, field):
        payload = base_payload()
        del payload[field]
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(payload)
        types = {error["type"] for error in exc.value.errors()}
        assert types & {"missing", "custom_error"}, types

    def test_schema_version_defaults_to_the_current_contract(self):
        payload = base_payload()
        del payload["schema_version"]
        assert FailureEvent.model_validate(payload).schema_version == CONTRACT_SCHEMA_VERSION

    def test_empty_payload_reports_every_required_field(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate({})
        missing = {
            str(error["loc"][0]) for error in exc.value.errors() if error["type"] == "missing"
        }
        assert missing == {
            "incident_id",
            "repository",
            "commit_sha",
            "workflow_name",
            "run_id",
            "failed_step",
            "failure_type",
            "logs",
            "timestamp",
        }


class TestInvalidValues:
    def test_unknown_failure_type_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(base_payload(failure_type="aliens"))
        assert any(error["type"] == "enum" for error in exc.value.errors())

    def test_wrong_schema_version_is_rejected(self):
        with pytest.raises(ValidationError):
            FailureEvent.model_validate(base_payload(schema_version="2.0"))

    def test_unknown_field_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(base_payload(unexpected="value"))
        assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())

    def test_repository_must_be_owner_slash_name(self):
        for bad in ["no-slash", "https://github.com/owner/repo", "owner/"]:
            with pytest.raises(ValidationError):
                FailureEvent.model_validate(base_payload(repository=bad))

    def test_commit_sha_must_be_hex(self):
        for bad in ["xyz", "a" * 6, "g" * 40, "a" * 65]:
            with pytest.raises(ValidationError):
                FailureEvent.model_validate(base_payload(commit_sha=bad))

    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(base_payload(timestamp="2026-10-09T12:00:00"))
        assert "timezone" in str(exc.value).lower()

    def test_malformed_timestamp_is_rejected(self):
        for bad in ["not-a-date", "09/10/2026", ""]:
            with pytest.raises(ValidationError):
                FailureEvent.model_validate(base_payload(timestamp=bad))

    def test_future_timestamp_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(base_payload(timestamp=utcnow() + timedelta(days=1)))
        assert "future" in str(exc.value).lower()

    def test_timestamp_within_clock_skew_is_accepted(self):
        event = FailureEvent.model_validate(
            base_payload(timestamp=utcnow() + timedelta(seconds=60))
        )
        assert event.timestamp is not None

    def test_negative_run_id_is_rejected(self):
        with pytest.raises(ValidationError):
            FailureEvent.model_validate(base_payload(run_id=-1))

    def test_non_numeric_run_id_is_rejected(self):
        # A JSON number is coerced to int by design; text that is not a number
        # is not.
        with pytest.raises(ValidationError):
            FailureEvent.model_validate(base_payload(run_id="not-a-number"))

    def test_blank_logs_are_rejected(self):
        for bad in ["", "   \n\t  "]:
            with pytest.raises(ValidationError):
                FailureEvent.model_validate(base_payload(logs=bad))

    def test_trivially_short_logs_are_rejected(self):
        with pytest.raises(ValidationError) as exc:
            FailureEvent.model_validate(base_payload(logs="boom"))
        assert "evidence" in str(exc.value)

    def test_oversized_logs_are_rejected(self):
        with pytest.raises(ValidationError):
            FailureEvent.model_validate(base_payload(logs="x" * 500_001))

    def test_incident_id_format_is_enforced(self):
        for bad in ["", "INC-1", "inc-2026109-ab12cd", "not-an-id"]:
            with pytest.raises(ValidationError):
                FailureEvent.model_validate(base_payload(incident_id=bad))

    def test_invalid_run_url_is_rejected(self):
        with pytest.raises(ValidationError):
            FailureEvent.model_validate(base_payload(run_url="ftp://example.com/run/1"))


class TestSerialisation:
    def test_json_round_trip(self):
        event = FailureEvent.model_validate(base_payload())
        restored = FailureEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_json_schema_exposes_constraints(self):
        schema = FailureEvent.model_json_schema()
        assert schema["properties"]["schema_version"]["const"] == CONTRACT_SCHEMA_VERSION
        assert "pattern" in schema["properties"]["repository"]
