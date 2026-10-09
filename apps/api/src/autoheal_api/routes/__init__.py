"""Router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from autoheal_api.routes import health, incidents

__all__ = ["api_router"]

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(incidents.router)
