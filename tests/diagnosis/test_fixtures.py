"""Fixture tests and the evaluation table.

The evaluation is a *test*, not a claim in a document: if the engine changes
behaviour, this is the test that fails. The same expectations drive
``python -m autoheal_diagnosis``, which prints the same table.
"""

from __future__ import annotations

import json

import pytest
from autoheal_contracts import DiagnosisResult, FailureEvent
from autoheal_diagnosis import DiagnosisService, fixture_event, fixture_payload
from autoheal_diagnosis.fixtures import (
    EXPECTED_ACTION,
    EXPECTED_CLASSIFIER_RULE,
    EXPECTED_FAILURE_TYPE,
    EXPECTED_HUMAN_REVIEW,
    FIXTURE_NAMES,
    FIXTURES,
)

pytestmark = pytest.mark.diagnosis

SERVICE = DiagnosisService()


class TestFixturesAreHonest:
    def test_every_fixture_validates_against_the_shared_contract(self):
        for name in FIXTURE_NAMES:
            event = FailureEvent.model_validate(fixture_payload(name))
            assert event.incident_id == FIXTURES[name]["incident_id"], name

    def test_fixture_identifiers_are_local_placeholders(self):
        """No real commit SHA and no real pipeline id may be baked in."""
        for name in FIXTURE_NAMES:
            payload = fixture_payload(name)
            assert payload["incident_id"].startswith("inc-"), name
            assert "fixt" in payload["incident_id"], name
            assert payload["repository"].startswith("local-fixture/"), name
            assert payload["run_id"] <= 99, name
            assert payload["commit_sha"].startswith("abc"), name
            assert payload["timestamp"] == "2026-10-01T10:00:00Z", name

    def test_every_fixture_carries_meaningful_logs(self):
        for name in FIXTURE_NAMES:
            assert len(fixture_payload(name)["logs"].strip()) >= 10, name

    def test_expected_tables_cover_every_fixture(self):
        for table in (
            EXPECTED_FAILURE_TYPE,
            EXPECTED_CLASSIFIER_RULE,
            EXPECTED_ACTION,
            EXPECTED_HUMAN_REVIEW,
        ):
            assert set(table) == set(FIXTURE_NAMES)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
class TestEvaluation:
    """One row per fixture. The 'result' column is the assertion."""

    def test_failure_type(self, name):
        event = fixture_event(name)
        result = SERVICE.diagnose(event)
        assert result.failure_type.value == EXPECTED_FAILURE_TYPE[name]

    def test_classifier_rule(self, name):
        event = fixture_event(name)
        assert SERVICE.explain(event).rule_id == EXPECTED_CLASSIFIER_RULE[name]

    def test_recommended_action(self, name):
        event = fixture_event(name)
        result = SERVICE.diagnose(event)
        assert result.recommended_action.value == EXPECTED_ACTION[name]

    def test_human_review_routing(self, name):
        event = fixture_event(name)
        result = SERVICE.diagnose(event)
        assert result.requires_human_review is EXPECTED_HUMAN_REVIEW[name]

    def test_the_result_validates_against_the_contract(self, name):
        event = fixture_event(name)
        result = SERVICE.diagnose(event)
        DiagnosisResult.model_validate(result.model_dump(), context={"failure_event": event})
        assert result.verify_evidence(event).verified is True


class TestEverySupportedCategoryIsCovered:
    def test_dependency_error_is_covered(self):
        covered = {
            name
            for name, expected in EXPECTED_FAILURE_TYPE.items()
            if expected == "dependency_resolution"
        }
        assert "dependency_missing_module" in covered

    def test_syntax_or_import_error_is_covered(self):
        covered = {
            name
            for name, expected in EXPECTED_FAILURE_TYPE.items()
            if expected == "build_compilation"
        }
        assert "syntax_error" in covered
        assert "import_error" in covered
        assert "build_failure" in covered

    def test_test_failure_is_covered(self):
        covered = {
            name for name, expected in EXPECTED_FAILURE_TYPE.items() if expected == "test_failure"
        }
        assert "test_assertion_failure" in covered
        assert "injection_in_logs" in covered

    def test_build_or_config_error_is_covered(self):
        covered = {
            name for name, expected in EXPECTED_FAILURE_TYPE.items() if expected == "configuration"
        }
        assert "configuration_error" in covered

    def test_unknown_is_covered(self):
        covered = {
            name for name, expected in EXPECTED_FAILURE_TYPE.items() if expected == "unknown"
        }
        assert "ambiguous_logs" in covered
        assert "package_name_mention_only" in covered


class TestFixtureRoundTrip:
    def test_a_fixture_serialises_and_revalidates(self):
        event = fixture_event("configuration_error")
        reloaded = FailureEvent.model_validate_json(event.model_dump_json())
        assert reloaded == event

    def test_a_fixture_can_be_read_from_json(self, tmp_path):
        source = fixture_event("syntax_error")
        path = tmp_path / "event.json"
        path.write_text(json.dumps(json.loads(source.model_dump_json())), encoding="utf-8")
        loaded = FailureEvent.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert SERVICE.diagnose(loaded).failure_type == SERVICE.diagnose(source).failure_type
