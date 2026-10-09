"""Storage backend selection.

Adding a new backend means implementing :class:`IncidentStore` and adding one
branch to :func:`create_store`. Nothing else in the API changes.
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    IncidentFilter,
    IncidentStore,
    SortOrder,
    StorageConflictError,
    StorageNotFoundError,
    ordering_key,
    validate_status_filter,
)
from .json_file import FILE_NAME, JsonFileIncidentStore
from .memory import InMemoryIncidentStore

__all__ = [
    "FILE_NAME",
    "IncidentFilter",
    "IncidentStore",
    "InMemoryIncidentStore",
    "JsonFileIncidentStore",
    "SortOrder",
    "StorageConflictError",
    "StorageNotFoundError",
    "create_store",
    "ordering_key",
    "validate_status_filter",
]


def create_store(backend: str, data_dir: str | None = None) -> IncidentStore:
    """Instantiate the store named by ``backend``.

    ``memory``
        No persistence; used by tests and by local single-shot runs.
    ``json_file``
        A single JSONL file under ``data_dir``; the default for local runs.
    """
    if backend == "memory":
        return InMemoryIncidentStore()
    if backend == "json_file":
        target = Path(data_dir) if data_dir else Path(".")
        return JsonFileIncidentStore(target / FILE_NAME)

    raise ValueError(f"unknown store backend: {backend!r} (expected 'memory' or 'json_file')")
