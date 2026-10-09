"""Application factory for the AutoHeal API.

``create_app`` is the only supported way to build the service: it takes an
explicit :class:`Settings`, wires the routers and error handlers, and stores
the chosen backend on ``app.state``. Tests call it with a temporary data
directory, so no test can ever touch the developer's real data.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from autoheal_api.config import Settings
from autoheal_api.errors import (
    ApiError,
    api_error_handler,
    contract_violation_handler,
)
from autoheal_api.routes import api_router
from autoheal_api.storage import create_store

__all__ = ["app", "create_app"]

DESCRIPTION = """
AutoHeal DevOps AI -- API foundation for the shared contracts and the incident
lifecycle.

**What this milestone does**

* ingests a validated `FailureEvent` and opens a `received` incident;
* lists and retrieves incident records;
* exposes a health probe.

**What it deliberately does not do yet**

* no diagnosis engine, no remediation execution, no production writes. A
  `FailureEvent` that arrives here is recorded, not acted upon.

**Timestamps** are RFC 3339 UTC (`2026-10-09T12:00:00Z`). **Confidence** is a
float between 0.0 and 1.0. See `docs/api-contracts.md` for the full contract
reference.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with the given settings."""
    resolved = settings or Settings.from_env()

    application = FastAPI(
        title="AutoHeal DevOps AI",
        description=DESCRIPTION,
        version=resolved.version,
    )
    application.state.settings = resolved
    application.state.store = create_store(resolved.store_backend, str(resolved.data_dir))

    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, contract_violation_handler)

    application.include_router(api_router)
    return application


#: Module-level app so ``uvicorn autoheal_api.main:app`` works.
app = create_app()
