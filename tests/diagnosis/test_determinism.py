"""Determinism: the rule engine is repeatable and offline.

Run the same input repeatedly and the output must be byte-identical apart from
the diagnosis timestamp. No network, no credentials, no randomness.
"""

from __future__ import annotations

import json

import pytest
from autoheal_diagnosis import DiagnosisService, classify_event, diagnose
from autoheal_diagnosis.fixtures import FIXTURES, fixture_event

pytestmark = pytest.mark.diagnosis

SERVICE = DiagnosisService()


def _without_timestamp(result) -> dict:
    payload = json.loads(result.model_dump_json())
    payload.pop("diagnosed_at")
    return payload


class TestRepeatability:
    def test_the_same_fixture_diagnoses_identically_every_time(self):
        event = fixture_event("dependency_missing_module")
        first = _without_timestamp(SERVICE.diagnose(event))
        for _ in range(5):
            assert _without_timestamp(SERVICE.diagnose(event)) == first

    @pytest.mark.parametrize("name", list(FIXTURES))
    def test_every_fixture_is_deterministic(self, name):
        event = fixture_event(name)
        runs = {_dump(SERVICE.diagnose(event)) for _ in range(3)}
        assert len(runs) == 1, name

    def test_classification_is_deterministic(self):
        event = fixture_event("test_assertion_failure")
        runs = {classify_event(event) for _ in range(5)}
        assert len(runs) == 1


class TestOfflineOperation:
    def test_rule_mode_needs_no_environment(self, monkeypatch):
        monkeypatch.delenv("AUTOHEAL_DIAGNOSIS_LLM_ENABLED", raising=False)
        monkeypatch.delenv("AUTOHEAL_DIAGNOSIS_LLM_PROVIDER", raising=False)
        result = diagnose(fixture_event("test_assertion_failure"))
        assert result.failure_type.value == "test_failure"

    def test_rule_mode_needs_no_provider(self):
        result = diagnose(fixture_event("build_failure"))
        assert result.recommended_action.value == "fix_source_code"

    def test_settings_default_to_disabled(self):
        from autoheal_diagnosis import DiagnosisSettings

        assert DiagnosisSettings().llm_enabled is False


def _dump(result) -> str:
    payload = json.loads(result.model_dump_json())
    payload.pop("diagnosed_at")
    return json.dumps(payload, sort_keys=True)


def test_default_settings_do_not_read_the_real_environment(monkeypatch):
    """A stray AUTOHEAL_DIAGNOSIS_* variable must not change rule-only results."""
    monkeypatch.setenv("AUTOHEAL_DIAGNOSIS_LLM_ENABLED", "true")
    monkeypatch.setenv("AUTOHEAL_DIAGNOSIS_LLM_PROVIDER", "stub")
    event = fixture_event("dependency_missing_module")
    baseline = _dump(DiagnosisService(settings=_rule_only()).diagnose(event))
    assert _dump(DiagnosisService().diagnose(event)) == baseline


def _rule_only():
    from autoheal_diagnosis import DiagnosisSettings

    return DiagnosisSettings(llm_enabled=False)
