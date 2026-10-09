"""Local file-backed incident store.

One JSON Lines file, one incident per line. Every mutation rewrites the file
through a temporary file and :func:`os.replace`, so a crash mid-write leaves
the previous contents intact rather than a truncated record.

This is a *local development* store: it is single-host, single-file and fully
scan-on-start. It exists so that ``GET`` after a restart returns what was
``POST``ed, proving the persistence contract without dragging in PostgreSQL,
Redis, Celery or any other service. Replacing it with SQLite is a single new
implementation of :class:`IncidentStore`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autoheal_contracts import IncidentRecord

from .base import (
    IncidentFilter,
    IncidentStore,
    SortOrder,
    StorageConflictError,
    StorageNotFoundError,
    ordering_key,
)

__all__ = ["FILE_NAME", "JsonFileIncidentStore"]

#: File name used inside the configured data directory.
FILE_NAME = "incidents.jsonl"


class JsonFileIncidentStore(IncidentStore):
    """A JSONL-backed :class:`IncidentStore`."""

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__()
        self._path = Path(path) if path is not None else Path(FILE_NAME)
        self._records: dict[str, IncidentRecord] = {}
        self._load()

    # -- internals -------------------------------------------------------
    @property
    def path(self) -> Path:
        """Absolute path of the backing JSONL file."""
        return self._path

    def _load(self) -> None:
        """Read the file if it exists. A malformed line is an error, not noise."""
        with self.locked():
            if not self._path.exists():
                return
            with self._path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"{self._path}:{line_number} is not valid JSON: {exc.msg}"
                        ) from exc
                    record = IncidentRecord.model_validate(payload)
                    self._records[record.incident_id] = record

    def _flush(self) -> None:
        """Rewrite the file atomically.

        Must be called while holding the lock. Writing directly to the live
        file would leave a truncated record if the process died mid-write.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f".tmp{os.getpid()}")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                for record in self._records.values():
                    handle.write(record.model_dump_json())
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    # -- IncidentStore ---------------------------------------------------
    def create(self, record: IncidentRecord) -> IncidentRecord:
        with self.locked():
            if record.incident_id in self._records:
                raise StorageConflictError(record.incident_id)
            self._records[record.incident_id] = record
            self._flush()
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
        with self.locked():
            if record.incident_id not in self._records:
                raise StorageNotFoundError(record.incident_id)
            self._records[record.incident_id] = record
            self._flush()
            return record

    def delete(self, incident_id: str) -> bool:
        with self.locked():
            if self._records.pop(incident_id, None) is None:
                return False
            self._flush()
            return True

    def clear(self) -> None:
        with self.locked():
            self._records.clear()
            self._path.unlink(missing_ok=True)

    def count(self) -> int:
        with self.locked():
            return len(self._records)

    def as_dicts(self) -> list[dict[str, Any]]:
        """Every record as a JSON-safe dict. Used by diagnostics and tests."""
        with self.locked():
            return [record.model_dump(mode="json") for record in self._records.values()]
