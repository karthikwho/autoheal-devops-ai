"""Shared builders for the diagnosis tests.

Kept out of ``conftest.py`` so they can be imported without pulling in the
fixtures -- the same split the repository already uses in ``tests/_helpers.py``.
"""

from __future__ import annotations

import json
from typing import Any

from autoheal_contracts import FailureEvent
from autoheal_diagnosis.config import DiagnosisSettings
from autoheal_diagnosis.providers import ScriptedProvider
from autoheal_diagnosis.service import DiagnosisService

__all__ = [
    "RULE_ONLY_SETTINGS",
    "build_event",
    "build_service",
    "llm_payload",
    "llm_service",
]

#: Settings that unambiguously disable the optional LLM layer.
RULE_ONLY_SETTINGS = DiagnosisSettings(llm_enabled=False)

#: Base payload, distinct from the one in ``tests/_helpers.py`` only in that it
#: is overridable by log text and reported failure type.
_BASE: dict[str, Any] = {
    "schema_version": "1.0",
    "incident_id": "inc-20261009-ab12cd",
    "repository": "karthikwho/autoheal-devops-ai",
    "commit_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    "workflow_name": "ci",
    "run_id": 123456789,
    "failed_step": "pytest",
    "timestamp": "2026-10-09T09:00:00Z",
}


def build_event(logs: str, failure_type: str = "test_failure", **overrides: Any) -> FailureEvent:
    """A valid ``FailureEvent`` carrying ``logs``.

    Validated through the shared contract, so a test can never drift from it.
    """
    payload: dict[str, Any] = {"logs": logs, "failure_type": failure_type}
    payload.update(overrides)
    return FailureEvent.model_validate({**_BASE, **payload})


def build_service(
    provider: Any = None,
    *,
    llm_enabled: bool = True,
    timeout_seconds: float = 20.0,
    max_response_chars: int = 20_000,
    max_evidence: int = 8,
    strong_rule_confidence: float = 0.8,
) -> DiagnosisService:
    """A service with an explicit, deterministic configuration."""
    return DiagnosisService(
        settings=DiagnosisSettings(
            llm_enabled=llm_enabled,
            llm_timeout_seconds=timeout_seconds,
            llm_max_response_chars=max_response_chars,
            llm_max_evidence=max_evidence,
            strong_rule_confidence=strong_rule_confidence,
        ),
        provider=provider,
    )


def llm_service(response: str, **kwargs: Any) -> DiagnosisService:
    """A service whose optional provider replays ``response``."""
    return build_service(ScriptedProvider(response), **kwargs)


def llm_payload(event: FailureEvent, **overrides: Any) -> str:
    """A valid model response for ``event``, as JSON text.

    The quote is taken from ``event.logs`` so it is verifiable.
    """
    quote = next(
        (line.strip() for line in event.logs.splitlines() if line.strip()),
        "fallback quote",
    )
    payload: dict[str, Any] = {
        "failure_type": event.failure_type.value,
        "probable_cause": (
            "The runner image used by this job did not carry the module the code imports."
        ),
        "evidence": [
            {
                "source": "failure_logs",
                "quote": quote,
                "explanation": "A verbatim line taken from the supplied failure logs.",
            }
        ],
        "confidence": 0.7,
        "recommended_action": "pin_dependency",
        "risk_level": "medium",
        "limitations": (
            "Only the supplied logs were considered; the dependency manifest was not read."
        ),
    }
    payload.update(overrides)
    return json.dumps(payload)
