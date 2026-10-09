# autoheal-api

Minimal FastAPI service for AutoHeal DevOps AI.

Milestone M0 surface:

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/health` | Liveness, service metadata and store status. |
| GET | `/api/v1/incidents` | Paged incident records. |
| GET | `/api/v1/incidents/{incident_id}` | One record, or `404`. |
| POST | `/api/v1/incidents` | Create an incident from a validated `FailureEvent`. `201`, or `409` on a duplicate id. |

Storage sits behind an interface (`autoheal_api.storage.base.IncidentStore`) so a
SQLite or other backend can replace the bundled in-memory / JSON-file stores
without touching the routes. See `docs/architecture.md`.

```bash
uv sync
uv run uvicorn autoheal_api.main:app --reload
```
