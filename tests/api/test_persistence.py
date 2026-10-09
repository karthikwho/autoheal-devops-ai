"""Storage and persistence tests.

The persistence guarantee under test is narrow and deliberate: an incident
that was written must still be there after the store is closed and reopened.
No database server, no daemon -- just a file in a temporary directory.
"""

from __future__ import annotations

import json

import pytest
from autoheal_api.storage import (
    FILE_NAME,
    IncidentFilter,
    IncidentStore,
    InMemoryIncidentStore,
    JsonFileIncidentStore,
    SortOrder,
    StorageConflictError,
    StorageNotFoundError,
    create_store,
    ordering_key,
    validate_status_filter,
)
from autoheal_contracts import IncidentStatus
from pydantic import ValidationError

pytestmark = pytest.mark.api


@pytest.fixture
def event(event_factory):
    return event_factory()


@pytest.fixture
def record(event, incident_factory):
    return incident_factory(event)


class TestInMemoryStore:
    def test_create_and_get(self, memory_store, record):
        memory_store.create(record)
        assert memory_store.get(record.incident_id) == record

    def test_duplicate_create_raises(self, memory_store, record):
        memory_store.create(record)
        with pytest.raises(StorageConflictError):
            memory_store.create(record)

    def test_unknown_id_returns_none(self, memory_store, record):
        assert memory_store.get(record.incident_id) is None

    def test_get_or_raise(self, memory_store, record):
        memory_store.create(record)
        assert memory_store.get_or_raise(record.incident_id) == record
        with pytest.raises(StorageNotFoundError):
            memory_store.get_or_raise("inc-20261001-zzzzzz")

    def test_save_upserts(self, memory_store, record, diagnosis_factory):
        memory_store.create(record)
        updated = record.attach_diagnosis(diagnosis_factory(record.failure_event))
        memory_store.save(updated)
        assert memory_store.get(record.incident_id).diagnosis is not None

    def test_save_an_unknown_record_raises(self, memory_store, record):
        with pytest.raises(StorageNotFoundError):
            memory_store.save(record)

    def test_delete(self, memory_store, record):
        memory_store.create(record)
        assert memory_store.delete(record.incident_id) is True
        assert memory_store.delete(record.incident_id) is False

    def test_clear(self, memory_store, record):
        memory_store.create(record)
        memory_store.clear()
        assert memory_store.count() == 0

    def test_nothing_survives_in_memory(self, memory_store, record):
        memory_store.create(record)
        assert InMemoryIncidentStore().count() == 0


class TestJsonFileStore:
    def test_create_and_get(self, json_store, record):
        json_store.create(record)
        assert json_store.get(record.incident_id) == record

    def test_file_is_created_in_the_configured_directory(self, json_store, record):
        assert not json_store.path.exists()
        json_store.create(record)
        assert json_store.path.exists()
        assert json_store.path.name == FILE_NAME

    def test_file_contains_one_json_object_per_line(self, json_store, record):
        json_store.create(record)
        lines = json_store.path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["incident_id"] == record.incident_id

    def test_duplicate_create_raises(self, json_store, record):
        json_store.create(record)
        with pytest.raises(StorageConflictError):
            json_store.create(record)

    def test_clear_removes_the_file(self, json_store, record):
        json_store.create(record)
        json_store.clear()
        assert not json_store.path.exists()

    def test_corrupt_line_raises_a_clear_error(self, tmp_path, record):
        path = tmp_path / "incidents.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(ValueError) as exc:
            JsonFileIncidentStore(path)
        assert "not valid JSON" in str(exc.value)

    def test_unparseable_record_raises_a_validation_error(self, tmp_path):
        path = tmp_path / "incidents.jsonl"
        path.write_text(json.dumps({"incident_id": "inc-20261001-abcdef"}) + "\n", encoding="utf-8")
        with pytest.raises(ValidationError):
            JsonFileIncidentStore(path)

    def test_blank_lines_are_ignored(self, tmp_path, record):
        path = tmp_path / "incidents.jsonl"
        path.write_text("\n" + record.model_dump_json() + "\n\n", encoding="utf-8")
        store = JsonFileIncidentStore(path)
        assert store.count() == 1


class TestPersistence:
    def test_incidents_survive_a_reopen(self, json_store_factory, record):
        """The core persistence guarantee: close, reopen, still there."""
        first = json_store_factory("reopen")
        first.create(record)

        second = json_store_factory("reopen")
        restored = second.get(record.incident_id)
        assert restored is not None
        assert restored == record

    def test_several_incidents_survive_a_reopen(
        self, json_store_factory, event_factory, incident_factory
    ):
        store = json_store_factory("multi")
        for index in range(3):
            store.create(incident_factory(event_factory(incident_id=f"inc-20261009-00000{index}")))

        reopened = json_store_factory("multi")
        assert reopened.count() == 3
        assert all(reopened.get(f"inc-20261009-00000{i}") is not None for i in range(3))

    def test_updates_survive_a_reopen(self, json_store_factory, record, diagnosis_factory):
        store = json_store_factory("update")
        store.create(record)
        store.save(record.attach_diagnosis(diagnosis_factory(record.failure_event)))

        reopened = json_store_factory("update")
        assert reopened.get(record.incident_id).status is IncidentStatus.AWAITING_REVIEW

    def test_deletes_survive_a_reopen(self, json_store_factory, record):
        store = json_store_factory("delete")
        store.create(record)
        store.delete(record.incident_id)
        assert json_store_factory("delete").count() == 0

    def test_a_failed_write_leaves_the_previous_file_intact(
        self, json_store_factory, record, event_factory, incident_factory, monkeypatch
    ):
        """Atomic replace: a crash mid-write must not truncate the store."""
        store = json_store_factory("atomic")
        store.create(record)

        other = incident_factory(event_factory(incident_id="inc-20261009-ff00aa"))

        import autoheal_api.storage.json_file as json_file_module

        def boom(_self):
            raise OSError("disk full")

        monkeypatch.setattr(json_file_module.JsonFileIncidentStore, "_flush", boom, raising=True)
        with pytest.raises(OSError):
            store.create(other)

        # The original record is untouched on disk and in memory.
        assert len(store.path.read_text(encoding="utf-8").strip().splitlines()) == 1
        assert not list(store.path.parent.glob("*.tmp*"))

    def test_api_survives_a_reopen_through_the_endpoint(self, tmp_path, record):
        """End-to-end: POST, new app instance on the same directory, GET."""
        from autoheal_api import create_app
        from autoheal_api.config import Settings
        from fastapi.testclient import TestClient

        from tests._helpers import base_payload

        settings = Settings(app_name="t", version="t", data_dir=tmp_path, store_backend="json_file")

        with TestClient(create_app(settings)) as client:
            assert client.post("/api/v1/incidents", json=base_payload()).status_code == 201

        # A brand-new application object, pointed at the same data directory.
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/incidents/inc-20261009-ab12cd")
            assert response.status_code == 200
            assert response.json()["status"] == "received"


class TestFilteringAndOrdering:
    def _fill(self, store, event_factory, incident_factory):
        specs = [
            ("inc-20261001-aaaaaa", "2026-10-01T10:00:00Z", "karthikwho/one"),
            ("inc-20261005-bbbbbb", "2026-10-05T10:00:00Z", "karthikwho/two"),
            ("inc-20261006-cccccc", "2026-10-06T12:00:00Z", "karthikwho/one"),
        ]
        for incident_id, timestamp, repository in specs:
            store.create(
                incident_factory(
                    event_factory(
                        incident_id=incident_id, timestamp=timestamp, repository=repository
                    )
                )
            )

    def test_default_order_is_newest_failure_first(
        self, memory_store, event_factory, incident_factory
    ):
        self._fill(memory_store, event_factory, incident_factory)
        items, total = memory_store.list()
        assert total == 3
        assert [r.incident_id for r in items] == [
            "inc-20261006-cccccc",
            "inc-20261005-bbbbbb",
            "inc-20261001-aaaaaa",
        ]

    def test_ascending_order(self, memory_store, event_factory, incident_factory):
        self._fill(memory_store, event_factory, incident_factory)
        items, _ = memory_store.list(IncidentFilter(order=SortOrder.ASC))
        assert [r.incident_id for r in items][0] == "inc-20261001-aaaaaa"

    def test_ordering_key_is_deterministic(self, memory_store, event_factory, incident_factory):
        self._fill(memory_store, event_factory, incident_factory)
        keys = [ordering_key(r) for r in memory_store.list()[0]]
        assert keys == sorted(keys, reverse=True)

    def test_filter_by_repository(self, memory_store, event_factory, incident_factory):
        self._fill(memory_store, event_factory, incident_factory)
        items, total = memory_store.list(IncidentFilter(repository="karthikwho/one"))
        assert total == 2
        assert {r.failure_event.repository for r in items} == {"karthikwho/one"}
        # Newest failure first within the filter.
        assert items[0].incident_id == "inc-20261006-cccccc"

    def test_pagination_slices_the_page(self, memory_store, event_factory, incident_factory):
        self._fill(memory_store, event_factory, incident_factory)
        page, total = memory_store.list(IncidentFilter(limit=2, offset=1))
        assert len(page) == 2
        assert total == 3

    def test_negative_offset_is_rejected_by_the_api(self, client):
        assert client.get("/api/v1/incidents?offset=-5").status_code == 422


class TestStoreFactory:
    def test_memory_backend_returns_the_in_memory_store(self, tmp_path):
        assert isinstance(create_store("memory"), InMemoryIncidentStore)

    def test_json_file_backend_returns_the_file_store(self, tmp_path):
        store = create_store("json_file", str(tmp_path))
        assert isinstance(store, JsonFileIncidentStore)
        assert store.path == tmp_path / FILE_NAME

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError) as exc:
            create_store("postgres_please")
        assert "unknown store backend" in str(exc.value)

    def test_incident_store_is_an_interface(self):
        """The API depends on the interface, not on a specific backend."""
        for backend in (InMemoryIncidentStore(), JsonFileIncidentStore()):
            assert isinstance(backend, IncidentStore)


class TestStatusFilterValidation:
    def test_valid_statuses_are_accepted(self):
        for status in IncidentStatus:
            assert validate_status_filter(status.value) is status

    def test_none_and_blank_mean_no_filter(self):
        assert validate_status_filter(None) is None
        assert validate_status_filter("   ") is None

    def test_case_insensitive(self):
        assert validate_status_filter("RECEIVED") is IncidentStatus.RECEIVED

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError) as exc:
            validate_status_filter("aliens")
        assert "unknown status" in str(exc.value)
        assert "received" in str(exc.value)
