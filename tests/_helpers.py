"""Shared test payload builders.

Kept out of ``conftest.py`` so it can be imported without pulling in the
fixtures (and without any test package ``__init__`` gymnastics).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from autoheal_contracts import utcnow

__all__ = [
    "LOG_TEXT",
    "base_payload",
    "diagnosis_payload",
    "future_iso",
    "validation_payload",
]

LOG_TEXT = (
    "Run actions/checkout@v4\n"
    "Run pip install -e '.[dev]'\n"
    "============================= test session starts ==============================\n"
    "FAILED tests/test_login.py::test_login_timeout - AssertionError: expected 200 got 500\n"
    "============================== 1 failed, 41 passed in 12.31s ===================\n"
)

BASE_TIMESTAMP = utcnow().isoformat()


def base_payload(**overrides: Any) -> dict[str, Any]:
    """A valid ``FailureEvent`` payload, with ``overrides`` merged in."""
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "incident_id": "inc-20261009-ab12cd",
        "repository": "karthikwho/autoheal-devops-ai",
        "commit_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        "workflow_name": "ci",
        "run_id": 123456789,
        "failed_step": "pytest",
        "failure_type": "test_failure",
        "logs": LOG_TEXT,
        "timestamp": BASE_TIMESTAMP,
    }
    payload.update(overrides)
    return payload


def diagnosis_payload(event, **overrides: Any) -> dict[str, Any]:
    """A ``DiagnosisResult`` payload whose quotes are verifiable against ``event``."""
    quote = "test_login_timeout"
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "incident_id": event.incident_id,
        "failure_type": event.failure_type.value,
        "probable_cause": "The login handler raises after the token-refactor change",
        "evidence": [
            {
                "source": "failure_logs",
                "quote": quote,
                "explanation": "The failing assertion names the exact test that regressed",
                "log_offset": event.logs.find(quote),
            }
        ],
        "confidence": 0.62,
        "recommended_action": "fix_source_code",
        "risk_level": "medium",
        "requires_human_review": True,
        "limitations": "Only the pytest output was inspected; the source diff was not read.",
        "diagnosed_at": utcnow().isoformat(),
    }
    payload.update(overrides)
    return payload


def validation_payload(event, **overrides: Any) -> dict[str, Any]:
    """A ``ValidationResult`` payload for ``event``."""
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "incident_id": event.incident_id,
        "passed": True,
        "tests_executed": 42,
        "tests_failed": 0,
        "validation_summary": "42 tests re-ran after the proposed fix; all reported pass.",
        "validated_at": utcnow().isoformat(),
    }
    payload.update(overrides)
    return payload


def future_iso(days: int = 1) -> str:
    """An ISO timestamp comfortably in the future."""
    return (utcnow() + timedelta(days=days)).isoformat()
