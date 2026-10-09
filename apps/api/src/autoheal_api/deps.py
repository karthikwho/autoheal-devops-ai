"""Shared dependencies for the AutoHeal API routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from autoheal_api.config import Settings
from autoheal_api.storage import IncidentFilter, IncidentStore, SortOrder

__all__ = [
    "get_incident_store",
    "get_settings",
    "SettingsDep",
    "StoreDep",
    "IncidentFilterDep",
]


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` the application was built with."""
    settings: Settings = request.app.state.settings
    return settings


def get_incident_store(request: Request) -> IncidentStore:
    """Return the store bound to this application instance.

    Tests build an app with their own settings, so this resolves per-app rather
    than at import time.
    """
    store: IncidentStore = request.app.state.store
    return store


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[IncidentStore, Depends(get_incident_store)]


def get_incident_filter(
    status: str | None = None,
    repository: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
    settings: Settings = Depends(get_settings),
) -> IncidentFilter:
    """Build an :class:`IncidentFilter` from validated query parameters."""
    from fastapi import HTTPException

    from autoheal_api.storage import validate_status_filter

    try:
        parsed_status = validate_status_filter(status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        parsed_order = SortOrder(order.lower())
    except ValueError as exc:
        allowed = ", ".join(o.value for o in SortOrder)
        raise HTTPException(
            status_code=422, detail=f"unknown order '{order}'; allowed values: {allowed}"
        ) from exc

    if not 1 <= limit <= settings.max_page_size:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {settings.max_page_size}",
        )
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must not be negative")

    return IncidentFilter(
        status=parsed_status,
        repository=repository,
        limit=limit,
        offset=offset,
        order=parsed_order,
    )


IncidentFilterDep = Annotated[IncidentFilter, Depends(get_incident_filter)]
