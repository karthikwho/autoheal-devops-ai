"""Health endpoint."""

from __future__ import annotations

from autoheal_contracts import CONTRACT_SCHEMA_VERSION
from fastapi import APIRouter

from autoheal_api.deps import SettingsDep, StoreDep
from autoheal_api.schemas import HealthResponse

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description=(
        "Liveness check. Reports the store backend in use and the contract "
        "schema version the service enforces. It performs no I/O of its own, "
        "so it stays cheap enough for an uptime probe."
    ),
)
def health(store: StoreDep, settings: SettingsDep) -> HealthResponse:
    """Return a healthy service status plus service and store metadata."""
    from autoheal_contracts import utcnow

    return HealthResponse(
        status="healthy",
        service=settings.app_name,
        version=settings.version,
        contract_schema_version=CONTRACT_SCHEMA_VERSION,
        store=settings.store_backend,
        incidents=store.count(),
        time=utcnow().isoformat().replace("+00:00", "Z"),
    )
