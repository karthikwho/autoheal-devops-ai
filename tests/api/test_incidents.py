"""API tests: the incident endpoints.

Every test uses the fixtures from ``tests/conftest.py``, which build an
application whose store lives in a per-test temporary directory. Nothing here
touches GitHub, an LLM provider or the network.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


def _payload(**overrides):
    from tests._helpers import base_payload

    return base_payload(**overrides)


class TestCreateIncident:
    def test_create_returns_201(self, client):
        response = client.post("/api/v1/incidents", json=_payload())
        assert response.status_code == 201

    def test_create_returns_the_incident_record(self, client):
        body = client.post("/api/v1/incidents", json=_payload()).json()
        assert body["incident_id"] == "inc-20261009-ab12cd"
        assert body["status"] == "received"
        assert body["diagnosis"] is None
        assert body["validation"] is None
        assert body["schema_version"] == "1.0"

    def test_create_returns_a_location_header(self, client):
        response = client.post("/api/v1/incidents", json=_payload())
        assert response.headers["location"] == "/api/v1/incidents/inc-20261009-ab12cd"

    def test_created_record_embeds_the_failure_event(self, client):
        body = client.post("/api/v1/incidents", json=_payload()).json()
        assert body["failure_event"]["repository"] == "karthikwho/autoheal-devops-ai"
        assert body["failure_event"]["failure_type"] == "test_failure"
        assert "test_login_timeout" in body["failure_event"]["logs"]

    def test_duplicate_id_returns_409(self, client):
        client.post("/api/v1/incidents", json=_payload())
        response = client.post("/api/v1/incidents", json=_payload())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "incident_already_exists"

    def test_distinct_ids_are_accepted(self, client):
        first = _payload()
        second = _payload(incident_id="inc-20261009-ff00aa")
        assert client.post("/api/v1/incidents", json=first).status_code == 201
        assert client.post("/api/v1/incidents", json=second).status_code == 201

    def test_optional_fields_are_echoed(self, client):
        body = client.post(
            "/api/v1/incidents", json=_payload(branch="main", pull_request_number=7)
        ).json()
        assert body["failure_event"]["branch"] == "main"
        assert body["failure_event"]["pull_request_number"] == 7

    def test_commit_sha_is_normalised_in_the_response(self, client):
        body = client.post("/api/v1/incidents", json=_payload(commit_sha="A1B2C3D4E5F6")).json()
        assert body["failure_event"]["commit_sha"] == "a1b2c3d4e5f6"


class TestInvalidCreatePayloads:
    def test_empty_body_is_rejected(self, client):
        response = client.post("/api/v1/incidents", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "contract_violation"

    def test_missing_required_field_is_rejected(self, client):
        payload = _payload()
        del payload["commit_sha"]
        response = client.post("/api/v1/incidents", json=payload)
        assert response.status_code == 422
        errors = response.json()["error"]["details"]["errors"]
        assert any(error["field"].endswith("commit_sha") for error in errors)

    def test_invalid_enum_is_rejected(self, client):
        response = client.post("/api/v1/incidents", json=_payload(failure_type="aliens"))
        assert response.status_code == 422
        errors = response.json()["error"]["details"]["errors"]
        assert any(error["type"] == "enum" for error in errors)

    def test_unknown_field_is_rejected(self, client):
        response = client.post("/api/v1/incidents", json=_payload(unexpected="value"))
        assert response.status_code == 422
        errors = response.json()["error"]["details"]["errors"]
        assert any(error["type"] == "extra_forbidden" for error in errors)

    def test_naive_timestamp_is_rejected(self, client):
        response = client.post("/api/v1/incidents", json=_payload(timestamp="2026-10-09T12:00:00"))
        assert response.status_code == 422

    def test_bad_repository_format_is_rejected(self, client):
        response = client.post("/api/v1/incidents", json=_payload(repository="nope"))
        assert response.status_code == 422

    def test_nothing_is_persisted_when_the_payload_is_invalid(self, client):
        client.post("/api/v1/incidents", json=_payload(failure_type="aliens"))
        assert client.get("/api/v1/incidents").json()["total"] == 0


class TestGetIncident:
    def test_get_returns_the_created_incident(self, client):
        created = client.post("/api/v1/incidents", json=_payload()).json()
        fetched = client.get(f"/api/v1/incidents/{created['incident_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == created

    def test_unknown_incident_returns_404(self, client):
        response = client.get("/api/v1/incidents/inc-20261001-zzzzzz")
        assert response.status_code == 404

    def test_404_body_is_structured(self, client):
        body = client.get("/api/v1/incidents/inc-20261001-zzzzzz").json()
        assert body["error"]["code"] == "incident_not_found"
        assert body["error"]["details"]["incident_id"] == "inc-20261001-zzzzzz"

    def test_malformed_incident_id_returns_404_not_500(self, client):
        """An id that cannot exist is still a 404, never a server error."""
        response = client.get("/api/v1/incidents/definitely-not-an-id")
        assert response.status_code == 404

    def test_get_does_not_leak_another_incident(self, client):
        client.post("/api/v1/incidents", json=_payload())
        body = client.get("/api/v1/incidents/inc-20261001-zzzzzz").json()
        assert "inc-20261009-ab12cd" not in str(body)


class TestListIncidents:
    def test_empty_list_is_a_valid_response(self, client):
        response = client.get("/api/v1/incidents")
        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "pagination": {"limit": 50, "offset": 0},
            "total": 0,
        }

    def test_created_incidents_are_listed(self, client):
        client.post("/api/v1/incidents", json=_payload())
        body = client.get("/api/v1/incidents").json()
        assert body["total"] == 1
        assert body["items"][0]["incident_id"] == "inc-20261009-ab12cd"

    def test_list_is_newest_first_by_default(self, client):
        client.post("/api/v1/incidents", json=_payload())
        client.post(
            "/api/v1/incidents",
            json=_payload(incident_id="inc-20261009-ff00aa", timestamp="2026-10-09T06:00:00Z"),
        )
        items = client.get("/api/v1/incidents").json()["items"]
        assert [i["incident_id"] for i in items] == [
            "inc-20261009-ab12cd",
            "inc-20261009-ff00aa",
        ]

    def test_ascending_order(self, client):
        client.post("/api/v1/incidents", json=_payload())
        client.post(
            "/api/v1/incidents",
            json=_payload(incident_id="inc-20261009-ff00aa", timestamp="2026-10-09T06:00:00Z"),
        )
        items = client.get("/api/v1/incidents?order=asc").json()["items"]
        assert [i["incident_id"] for i in items] == [
            "inc-20261009-ff00aa",
            "inc-20261009-ab12cd",
        ]

    def test_limit_and_offset(self, client):
        for index in range(3):
            client.post(
                "/api/v1/incidents",
                json=_payload(incident_id=f"inc-20261009-00000{index}", run_id=index),
            )
        page = client.get("/api/v1/incidents?limit=2&offset=0").json()
        assert len(page["items"]) == 2
        assert page["total"] == 3
        rest = client.get("/api/v1/incidents?limit=2&offset=2").json()
        assert len(rest["items"]) == 1

    def test_filter_by_status(self, client):
        client.post("/api/v1/incidents", json=_payload())
        body = client.get("/api/v1/incidents?status=received").json()
        assert body["total"] == 1
        assert client.get("/api/v1/incidents?status=resolved").json()["total"] == 0

    def test_filter_by_repository(self, client):
        client.post("/api/v1/incidents", json=_payload(repository="karthikwho/other-repo"))
        body = client.get("/api/v1/incidents?repository=karthikwho/other-repo").json()
        assert body["total"] == 1
        assert client.get("/api/v1/incidents?repository=karthikwho/nope").json()["total"] == 0

    def test_unknown_status_filter_is_rejected(self, client):
        response = client.get("/api/v1/incidents?status=aliens")
        assert response.status_code == 422
        assert "aliens" in response.json()["detail"]

    def test_unknown_order_is_rejected(self, client):
        assert client.get("/api/v1/incidents?order=sideways").status_code == 422

    def test_limit_above_maximum_is_rejected(self, client):
        response = client.get("/api/v1/incidents?limit=100000")
        assert response.status_code == 422

    def test_negative_offset_is_rejected(self, client):
        assert client.get("/api/v1/incidents?offset=-1").status_code == 422

    def test_summary_reports_recovery_flags(self, client):
        client.post("/api/v1/incidents", json=_payload())
        item = client.get("/api/v1/incidents").json()["items"][0]
        assert item["diagnosis_attached"] is False
        assert item["validation_attached"] is False
        assert item["recovery_verified"] is False

    def test_a_listed_failure_is_not_reported_as_recovered(self, client):
        """Reporting a failure must never look like a verified recovery."""
        client.post("/api/v1/incidents", json=_payload())
        item = client.get("/api/v1/incidents").json()["items"][0]
        assert item["status"] == "received"
        assert item["recovery_verified"] is False


class TestOpenApi:
    def test_openapi_documents_every_endpoint(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths) == {
            "/health",
            "/api/v1/incidents",
            "/api/v1/incidents/{incident_id}",
        }

    def test_post_schema_requires_the_contract_fields(self, client):
        schema = client.get("/openapi.json").json()
        body = schema["paths"]["/api/v1/incidents"]["post"]["requestBody"]
        ref = body["content"]["application/json"]["schema"]["$ref"]
        properties = schema["components"]["schemas"][ref.split("/")[-1]]["properties"]
        assert {"repository", "commit_sha", "failure_type", "logs"} <= set(properties)

    def test_docs_are_served(self, client):
        assert client.get("/docs").status_code == 200
