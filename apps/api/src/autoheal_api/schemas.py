"""Response schemas for the AutoHeal API.

Request bodies are the shared contracts themselves -- ``POST`` accepts a
:class:`~autoheal_contracts.failure_event.FailureEvent` directly, so there is
no second schema to keep in sync. These types describe the HTTP *envelope*
(pagination, health) and the error body.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

__all__ = [
    "ErrorBody",
    "ErrorResponse",
    "HealthResponse",
    "IncidentListResponse",
    "IncidentSummary",
    "Pagination",
]

Short = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class ContractEnvelope(BaseModel):
    """Base for API schemas: unknown keys rejected, like the contracts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Pagination(ContractEnvelope):
    """Offset pagination state echoed back to the caller."""

    limit: int
    offset: int


class IncidentSummary(ContractEnvelope):
    """A compact view of an incident used in list responses."""

    incident_id: str
    status: str
    repository: str
    workflow_name: str
    failure_type: str
    created_at: str
    updated_at: str
    diagnosis_attached: bool
    validation_attached: bool
    recovery_verified: bool


class IncidentListResponse(ContractEnvelope):
    """Envelope for ``GET /api/v1/incidents``."""

    items: list[IncidentSummary]
    pagination: Pagination
    total: int


class HealthResponse(ContractEnvelope):
    """Response of ``GET /health``."""

    status: Literal["healthy"] = "healthy"
    service: str
    version: str
    contract_schema_version: str
    store: str
    incidents: int
    time: str


class ErrorBody(ContractEnvelope):
    """The ``error`` object shared by every failure response."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractEnvelope):
    """Envelope for every non-2xx response."""

    error: ErrorBody
