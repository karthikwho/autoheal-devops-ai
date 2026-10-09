"""Shared fixtures for the diagnosis test package.

The diagnosis engine only ever sees a :class:`~autoheal_contracts.FailureEvent`,
so these fixtures build one from inline log text. The root ``tests/conftest.py``
supplies the repository-wide ``event_factory``; this module adds the
diagnosis-specific pieces on top of it.
"""

from __future__ import annotations

import pytest
from autoheal_diagnosis.service import DiagnosisService

from tests.diagnosis._helpers import (
    RULE_ONLY_SETTINGS,
    build_event,
    build_service,
)

__all__ = [
    "classify",
    "diagnose",
    "make_event",
    "make_service",
    "service",
]


@pytest.fixture
def make_event():
    """Build a valid ``FailureEvent`` from log text."""
    return build_event


@pytest.fixture
def service() -> DiagnosisService:
    """A rule-only service: no provider, no credentials, no network."""
    return DiagnosisService(settings=RULE_ONLY_SETTINGS)


@pytest.fixture
def make_service():
    """Build a service with an explicit configuration."""
    return build_service


@pytest.fixture
def diagnose(service):
    """Diagnose a synthetic event with the deterministic engine."""

    def _diagnose(logs: str, failure_type: str = "test_failure", **overrides):
        return service.diagnose(build_event(logs, failure_type=failure_type, **overrides))

    return _diagnose


@pytest.fixture
def classify():
    """Classify a synthetic event's logs with the deterministic engine."""
    from autoheal_diagnosis.classifier import classify_event

    def _classify(logs: str, failure_type: str = "unknown", **overrides):
        return classify_event(build_event(logs, failure_type=failure_type, **overrides))

    return _classify
