"""Structured API errors and their HTTP rendering.

Every error response has the same envelope::

    {"error": {"code": "...", "message": "...", "details": {...}}}

so that the ingestion and diagnosis modules can branch on ``code`` rather than
on human-readable text.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

__all__ = [
    "ApiError",
    "BusinessRuleError",
    "IncidentAlreadyExistsError",
    "IncidentNotFoundError",
    "contract_violation_handler",
    "api_error_handler",
]


class ApiError(Exception):
    """Base class for errors that map to a JSON response."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.headers: dict[str, str] | None = headers

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class IncidentNotFoundError(ApiError):
    """The requested incident does not exist (HTTP 404)."""

    status_code = 404
    code = "incident_not_found"


class IncidentAlreadyExistsError(ApiError):
    """A create collided with an existing incident id (HTTP 409)."""

    status_code = 409
    code = "incident_already_exists"


class BusinessRuleError(ApiError):
    """The request was well formed but violates a contract invariant (422)."""

    status_code = 422
    code = "contract_violation"


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    """Render any :class:`ApiError` using the shared envelope."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload(), headers=exc.headers)


async def contract_violation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Render Pydantic/FastAPI validation failures in the same envelope.

    Keeps the underlying field errors, because "missing required field" and
    "invalid enum value" need to be distinguishable by the caller.
    """
    details: dict[str, Any] = {"errors": []}
    for error in exc.errors():
        details["errors"].append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "type": error.get("type"),
                "message": error.get("msg"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "contract_violation",
                "message": "The payload does not satisfy the AutoHeal contracts.",
                "details": details,
            }
        },
    )
