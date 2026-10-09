"""Shared test fixtures and payload builders.

Two rules hold everywhere in this suite:

1. **No shared state.** Every test that touches storage gets a fresh temporary
   directory, so no test can read another test's incidents and none can write
   into the developer's real data directory.
2. **No external world.** Nothing here reaches GitHub, an LLM provider or the
   network. The only network-capable client (``httpx2``) is only ever pointed
   at the in-process ASGI application.

Payload builders live in ``tests/_helpers.py``; this module exposes them as
fixtures so tests never import across test packages.
"""

from __future__ import annotations

from typing import Any

import pytest
from autoheal_api import create_app
from autoheal_api.config import Settings
from autoheal_api.storage import (
    InMemoryIncidentStore,
    JsonFileIncidentStore,
)
from fastapi.testclient import TestClient

from tests._helpers import (
    LOG_TEXT,
    base_payload,
    diagnosis_payload,
    validation_payload,
)

__all__ = [
    "LOG_TEXT",
    "base_payload",
    "client",
    "diagnosis_factory",
    "diagnosis_payload",
    "event_factory",
    "incident_factory",
    "json_store",
    "json_store_factory",
    "memory_client",
    "memory_store",
    "validation_factory",
    "validation_payload",
]


@pytest.fixture
def event_factory():
    """Callable returning a valid ``FailureEvent`` with overrides applied."""

    def _make(**overrides: Any):
        from autoheal_contracts import FailureEvent

        return FailureEvent.model_validate(base_payload(**overrides))

    return _make


@pytest.fixture
def diagnosis_factory():
    """Callable returning a ``DiagnosisResult`` verifiable against an event."""

    def _make(event, **overrides: Any):
        from autoheal_contracts import DiagnosisResult

        return DiagnosisResult.model_validate(
            diagnosis_payload(event, **overrides), context={"failure_event": event}
        )

    return _make


@pytest.fixture
def validation_factory():
    """Callable returning a ``ValidationResult`` for an event."""

    def _make(event, **overrides: Any):
        from autoheal_contracts import ValidationResult

        return ValidationResult.model_validate(validation_payload(event, **overrides))

    return _make


@pytest.fixture
def incident_factory():
    """Callable returning a fresh ``RECEIVED`` ``IncidentRecord``."""

    def _make(event=None):
        from autoheal_contracts import IncidentRecord

        if event is None:
            from autoheal_contracts import FailureEvent

            event = FailureEvent.model_validate(base_payload())
        return IncidentRecord.from_failure_event(event)

    return _make


@pytest.fixture
def memory_store() -> InMemoryIncidentStore:
    """An isolated in-memory store."""
    return InMemoryIncidentStore()


@pytest.fixture
def json_store(tmp_path) -> JsonFileIncidentStore:
    """A JSON-file store rooted in a per-test temporary directory."""
    return JsonFileIncidentStore(tmp_path / "incidents.jsonl")


@pytest.fixture
def json_store_factory(tmp_path):
    """Factory for JSON stores in isolated directories, for persistence tests."""

    def _make(name: str = "store") -> JsonFileIncidentStore:
        return JsonFileIncidentStore(tmp_path / name / "incidents.jsonl")

    return _make


def _settings(tmp_path=None, backend: str = "json_file") -> Settings:
    """Build settings that never point at a real data directory."""
    return Settings(
        app_name="autoheal-api-test",
        version="0.0.0-test",
        data_dir=(tmp_path / "data") if tmp_path is not None else None,
        store_backend=backend,
        default_page_size=50,
        max_page_size=200,
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    """A TestClient whose app uses an isolated JSON-file store."""
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


@pytest.fixture
def memory_client() -> TestClient:
    """A TestClient backed by the in-memory store (no filesystem at all)."""
    with TestClient(create_app(_settings(backend="memory"))) as test_client:
        yield test_client
