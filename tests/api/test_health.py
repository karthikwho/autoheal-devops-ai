"""API tests: the health endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


class TestHealth:
    def test_health_reports_healthy(self, memory_client):
        response = memory_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"

    def test_health_reports_service_metadata(self, memory_client):
        body = memory_client.get("/health").json()
        assert body["service"] == "autoheal-api-test"
        assert body["version"] == "0.0.0-test"
        assert body["store"] == "memory"

    def test_health_reports_the_contract_version(self, memory_client):
        body = memory_client.get("/health").json()
        assert body["contract_schema_version"] == "1.0"

    def test_health_reports_a_zulu_timestamp(self, memory_client):
        body = memory_client.get("/health").json()
        assert body["time"].endswith("Z")

    def test_health_counts_stored_incidents(self, client):
        assert client.get("/health").json()["incidents"] == 0
        client.post("/api/v1/incidents", json=_payload())
        assert client.get("/health").json()["incidents"] == 1

    def test_health_needs_no_authentication_and_no_external_call(self, memory_client):
        # No Authorization header, no GitHub token, no LLM key -- and it still answers.
        response = memory_client.get("/health", headers={"X-Trace": "abc"})
        assert response.status_code == 200

    def test_unknown_route_is_404(self, memory_client):
        assert memory_client.get("/nope").status_code == 404


def _payload(**overrides):
    from tests._helpers import base_payload

    return base_payload(**overrides)
