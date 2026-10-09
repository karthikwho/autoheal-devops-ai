"""Incident endpoints.

The request body of ``POST`` is the shared contract itself
(:class:`~autoheal_contracts.failure_event.FailureEvent`), not a hand-rolled
DTO. Members 1 and 2 therefore validate against the same object the contract
tests enforce, and there is no second schema that could drift from it.
"""

from __future__ import annotations

from autoheal_contracts import FailureEvent, IncidentRecord
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from autoheal_api.deps import IncidentFilterDep, SettingsDep, StoreDep
from autoheal_api.errors import (
    BusinessRuleError,
    IncidentAlreadyExistsError,
    IncidentNotFoundError,
)
from autoheal_api.schemas import IncidentListResponse, IncidentSummary
from autoheal_api.storage import StorageConflictError, StorageNotFoundError

__all__ = ["router"]

router = APIRouter(prefix="/api/v1", tags=["incidents"])


@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="List incidents",
)
def list_incidents(
    criteria: IncidentFilterDep,
    store: StoreDep,
    settings: SettingsDep,
) -> IncidentListResponse:
    """Return a page of incident records, newest first.

    An empty list is a valid response: a freshly started service has no
    incidents. Filters ``status`` and ``repository``; paging ``limit`` and
    ``offset``; ordering ``order`` (``asc`` | ``desc``). Pagination bounds come
    from settings, so a caller cannot request an unbounded page.
    """
    page, total = store.list(criteria)
    return IncidentListResponse(
        items=[_summary(record) for record in page],
        pagination={"limit": criteria.limit, "offset": criteria.offset},
        total=total,
    )


@router.post(
    "/incidents",
    response_model=IncidentRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Create an incident from a FailureEvent",
    responses={
        201: {"description": "Incident created."},
        409: {"description": "An incident with this id already exists."},
        422: {"description": "The payload violates the AutoHeal contracts."},
    },
)
def create_incident(
    body: FailureEvent,
    store: StoreDep,
    settings: SettingsDep,
) -> JSONResponse:
    """Ingest a validated :class:`FailureEvent` and open a ``RECEIVED`` incident.

    Returns ``201 Created`` with a ``Location`` header pointing at the new
    resource. Posting the same ``incident_id`` twice returns ``409 Conflict``
    rather than overwriting, so an at-least-once ingester is safe to retry.
    """
    del settings  # kept for symmetry; no per-request settings are used here

    try:
        record = IncidentRecord.from_failure_event(body)
    except ValueError as exc:
        raise BusinessRuleError(
            "the failure event cannot open an incident", details={"reason": str(exc)}
        ) from exc

    try:
        record = store.create(record)
    except StorageConflictError as exc:
        raise IncidentAlreadyExistsError(
            f"an incident with id '{exc.incident_id}' already exists",
            details={"incident_id": exc.incident_id},
        ) from exc

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=record.model_dump(mode="json"),
        headers={"Location": f"/api/v1/incidents/{record.incident_id}"},
    )


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentRecord,
    summary="Retrieve one incident",
    responses={
        200: {"description": "The incident record."},
        404: {"description": "No incident with that id."},
    },
)
def get_incident(incident_id: str, store: StoreDep) -> IncidentRecord:
    """Return one incident record, or ``404`` if the id is unknown."""
    try:
        return store.get_or_raise(incident_id)
    except StorageNotFoundError as exc:
        raise IncidentNotFoundError(
            f"no incident with id '{incident_id}'",
            details={"incident_id": incident_id},
        ) from exc


def _summary(record: IncidentRecord) -> IncidentSummary:
    """Compact list-view rendering of a record."""
    return IncidentSummary(
        incident_id=record.incident_id,
        status=record.status.value,
        repository=record.failure_event.repository,
        workflow_name=record.failure_event.workflow_name,
        failure_type=record.failure_event.failure_type.value,
        created_at=record.created_at.isoformat().replace("+00:00", "Z"),
        updated_at=record.updated_at.isoformat().replace("+00:00", "Z"),
        diagnosis_attached=record.diagnosis is not None,
        validation_attached=record.validation is not None,
        recovery_verified=record.is_recovery_verified,
    )
