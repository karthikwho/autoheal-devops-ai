"""Storage contracts for incidents.

The API never talks to a concrete store: it depends on :class:`IncidentStore`
only. Swapping the bundled JSON-file store for SQLite (or anything else) means
adding one implementation of this ABC and changing ``AUTOHEAL_STORE_BACKEND``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from autoheal_contracts import IncidentRecord
from autoheal_contracts.enums import IncidentStatus

__all__ = [
    "IncidentFilter",
    "IncidentStore",
    "SortOrder",
    "StorageConflictError",
    "StorageNotFoundError",
    "ordering_key",
    "validate_status_filter",
]


class StorageNotFoundError(KeyError):
    """Raised by a store when an incident id is unknown."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(incident_id)
        self.incident_id = incident_id


class StorageConflictError(ValueError):
    """Raised by a store on a create that collides with an existing id."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(incident_id)
        self.incident_id = incident_id


class SortOrder(StrEnum):
    """Direction of the ``created_at`` ordering."""

    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class IncidentFilter:
    """Query parameters for listing incidents."""

    status: IncidentStatus | None = None
    repository: str | None = None
    limit: int = 50
    offset: int = 0
    order: SortOrder = SortOrder.DESC

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value if self.status else None,
            "repository": self.repository,
            "limit": self.limit,
            "offset": self.offset,
            "order": self.order.value,
        }


def ordering_key(record: IncidentRecord) -> tuple:
    """Sort key for listing: newest *failure* first.

    Deliberately not ``created_at``: two incidents ingested in the same second
    would otherwise have an arbitrary order, and the operator's question is
    "what failed most recently", not "what did I POST last". ``incident_id``
    breaks ties so the order is fully deterministic.
    """
    return (record.failure_event.timestamp, record.incident_id)


def validate_status_filter(raw: str | None) -> IncidentStatus | None:
    """Turn an incoming ``status`` query parameter into an enum value.

    Raises ``ValueError`` for anything that is not a lifecycle state, so an
    unknown status is a 422 rather than a silently ignored filter.
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    try:
        return IncidentStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in IncidentStatus)
        raise ValueError(f"unknown status '{raw}'; allowed values: {allowed}") from exc


class IncidentStore(ABC):
    """Persistence interface for incident aggregates.

    Implementations must be safe to share between threads, because FastAPI runs
    sync endpoints in a thread pool. Every store therefore owns a re-entrant
    lock exposed through :meth:`locked`.
    """

    def __init__(self) -> None:
        self._lock = RLock()

    # -- helpers shared by every implementation --------------------------
    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialise a read-modify-write sequence."""
        with self._lock:
            yield

    @staticmethod
    def _matches(record: IncidentRecord, criteria: IncidentFilter) -> bool:
        if criteria.status is not None and record.status is not criteria.status:
            return False
        if criteria.repository and record.failure_event.repository != criteria.repository:
            return False
        return True

    @abstractmethod
    def create(self, record: IncidentRecord) -> IncidentRecord:
        """Insert a new incident.

        Raises :class:`StorageConflictError` if the id is taken.
        """

    @abstractmethod
    def get(self, incident_id: str) -> IncidentRecord | None:
        """Return the incident, or ``None`` if it does not exist."""

    def get_or_raise(self, incident_id: str) -> IncidentRecord:
        """Return the incident or raise :class:`StorageNotFoundError`."""
        record = self.get(incident_id)
        if record is None:
            raise StorageNotFoundError(incident_id)
        return record

    @abstractmethod
    def list(self, criteria: IncidentFilter | None = None) -> tuple[list[IncidentRecord], int]:
        """Return ``(page, total_matching)`` for the given criteria."""

    @abstractmethod
    def save(self, record: IncidentRecord) -> IncidentRecord:
        """Upsert an existing incident (used once diagnosis/validation land).

        ``create`` is the insert path. ``save`` raises
        :class:`StorageNotFoundError` for an unknown id so an update can never
        silently invent an incident.
        """

    @abstractmethod
    def delete(self, incident_id: str) -> bool:
        """Remove an incident. Returns ``True`` if something was deleted."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every incident. Used by tests and local resets."""

    @abstractmethod
    def count(self) -> int:
        """Number of stored incidents."""
