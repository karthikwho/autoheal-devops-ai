"""FastAPI service exposing the AutoHeal incident lifecycle.

Milestone M0 endpoints:

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/health` | Liveness, service metadata and store status. |
| GET | `/api/v1/incidents` | Paged incident records. |
| GET | `/api/v1/incidents/{incident_id}` | One record, or `404`. |
| POST | `/api/v1/incidents` | Create an incident from a validated `FailureEvent`. `201`, or `409` on a duplicate id. |

Storage sits behind :class:`~autoheal_api.storage.base.IncidentStore` so a
SQLite or other backend can replace the bundled in-memory / JSON-file stores
without touching the routes.
"""

from autoheal_api.main import app, create_app

__version__ = "0.1.0"

__all__ = ["__version__", "app", "create_app"]
