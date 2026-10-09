"""In-memory incident store.

Fast and dependency-free. Nothing survives a restart -- that is exactly what
the test suite uses, and what a single-process local run may want.
"""

from __future__ import annotations

from autoheal_contracts import IncidentRecord

from .base import (
    IncidentFilter,
    IncidentStore,
    SortOrder,
    StorageConflictError,
    StorageNotFoundError,
    ordering_key,
)

__all__ = ["InMemoryIncidentStore", "ordering_key"]


class InMemoryIncidentStore(IncidentStore):
    """A dict-backed :class:`IncidentStore`.

    Insertion order is preserved so that ``created_at`` ordering and the
    default newest-first page are deterministic.
    """

    def __init__(self) -> None:
        super().__init__()
        self._records: dict[str, IncidentRecord] = {}

    def create(self, record: IncidentRecord) -> IncidentRecord:
        with self.locked():
            if record.incident_id in self._records:
                raise StorageConflictError(record.incident_id)
            self._records[record.incident_id] = record
            return record

    def get(self, incident_id: str) -> IncidentRecord | None:
        with self.locked():
            return self._records.get(incident_id)

    def list(self, criteria: IncidentFilter | None = None) -> tuple[list[IncidentRecord], int]:
        active = criteria or IncidentFilter()
        with self.locked():
            matching = [r for r in self._records.values() if self._matches(r, active)]
        matching.sort(key=ordering_key, reverse=active.order is SortOrder.DESC)
        return matching[active.offset : active.offset + active.limit], len(matching)

    def save(self, record: IncidentRecord) -> IncidentRecord:
        """Update an existing incident.

        ``create`` is the insert path; ``save`` refuses to invent an incident
        that was never created, so an upsert can never silently resurrect a
        deleted one.
        """
        with self.locked():
            if record.incident_id not in self._records:
                raise StorageNotFoundError(record.incident_id)
            self._records[record.incident_id] = record
            return record

    def delete(self, incident_id: str) -> bool:
        with self.locked():
            return self._records.pop(incident_id, None) is not None

    def clear(self) -> None:
        with self.locked():
            self._records.clear()

    def count(self) -> int:
        with self.locked():
            return len(self._records)
