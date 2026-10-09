# AutoHeal DevOps AI

An AI-assisted, self-healing CI/CD pipeline that detects software delivery failures, diagnoses probable root causes, proposes controlled remediations, and verifies recovery through automated validation.

## Core workflow

1. Detect a CI/CD failure.
2. Normalize the failure into a shared `FailureEvent`.
3. Diagnose the failure and produce an evidence-backed `DiagnosisResult`.
4. Evaluate remediation proposals against safety policies.
5. Execute permitted repairs in an isolated environment.
6. Rerun tests and verify the result.
7. Record the incident and its outcome.

## Safety principles

- A proposed repair is not a verified repair.
- No unrestricted command execution.
- No direct production modifications.
- Failed validation must never be reported as successful recovery.
- Unknown or high-risk failures must be escalated for human review.

These are not aspirations. They are enforced in code — see
[`docs/architecture.md`](docs/architecture.md) and
[`docs/api-contracts.md`](docs/api-contracts.md) for the specific invariants.

## Project status

**Milestone M0 — shared contracts and API foundation: complete.**

| Capability | Status |
| --- | --- |
| Typed, validated shared contracts | Done — `packages/contracts` |
| FastAPI service with incident endpoints | Done — `apps/api` |
| Local storage behind a swappable interface | Done — in-memory and JSON-file stores |
| Automated tests | Done — 214 tests, all passing |
| Documentation | Done — this README, `docs/architecture.md`, `docs/api-contracts.md`, `CONTRIBUTING.md` |
| AI diagnosis engine | Not started — owned by Member 2 |
| CI/CD failure ingestion | Not started — owned by Member 1 |
| Remediation execution and validation | Not started |
| Dashboard | Not started |

## Repository layout

```
packages/
  contracts/          Shared Pydantic contracts (the source of truth)
apps/
  api/                FastAPI service
tests/
  contracts/          Contract validation tests
  api/                Endpoint and persistence tests
docs/
```

`packages/diagnosis`, `packages/ingestion`, `packages/validation`,
`packages/remediation`, `packages/incident_store`, `apps/dashboard`,
`tests/evaluation`, `tests/fixtures` and `tests/integration` are reserved for
the other team members and are intentionally left empty.

## Quick start

Requires Python 3.11 or newer (developed and verified on 3.14). Dependencies are
managed with [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Install everything (root project + workspace members + dev tools)
uv sync

# 2. Run the API on http://127.0.0.1:8000
uv run uvicorn autoheal_api.main:app --reload

# 3. In another terminal, exercise it
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/api/v1/incidents
curl -sS -X POST http://127.0.0.1:8000/api/v1/incidents \
  -H 'Content-Type: application/json' \
  -d @docs/examples/failure-event.json
```

Interactive API docs are served at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### Configuration

All settings come from environment variables; none are required.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTOHEAL_DATA_DIR` | `./.autoheal-data` | Directory for the JSON-file store |
| `AUTOHEAL_STORE_BACKEND` | `json_file` | `json_file` or `memory` |
| `AUTOHEAL_HOST` / `AUTOHEAL_PORT` | `127.0.0.1` / `8000` | Bind address |
| `AUTOHEAL_DEFAULT_PAGE_SIZE` / `AUTOHEAL_MAX_PAGE_SIZE` | `50` / `200` | Pagination bounds |

```bash
# Run without touching the filesystem at all
AUTOHEAL_STORE_BACKEND=memory uv run uvicorn autoheal_api.main:app
```

## Running the tests

```bash
uv run pytest                       # full suite
uv run pytest tests/contracts -q    # contract tests only
uv run pytest tests/api -q          # endpoint and persistence tests only
uv run pytest -k confidence -v      # a single topic
```

Lint and format gates:

```bash
uv run ruff check .
uv run ruff format .
```

Tests use isolated temporary storage and never require GitHub credentials, LLM
API keys or network access.

## Test suite at a glance

| Area | Tests | What is protected |
| --- | --- | --- |
| `FailureEvent` | 38 | Required fields, enum values, identifier formats, timestamp rules |
| `DiagnosisResult` | 45 | Structured evidence, confidence bounds, human-review routing, quote verification |
| `ValidationResult` | 26 | Pass requires executed tests; failures never exceed executions |
| `IncidentRecord` | 25 | Status/payload invariants, cross-incident consistency |
| Health + incidents endpoints | 43 | All four endpoints, 404, 409, 422, filtering, paging |
| Storage and persistence | 37 | Reopen-after-restore, atomic writes, swap the backend |

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — system design, module
  boundaries, storage interface, invariants.
- [`docs/api-contracts.md`](docs/api-contracts.md) — field-by-field contract
  reference with exact JSON examples.
- [`docs/examples/`](docs/examples/) — copy-paste payloads.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branches, pull requests, test
  expectations, and the rule that `main` must stay stable.

## What this milestone deliberately does not do

No PostgreSQL, Redis, Celery or Kubernetes. No arbitrary command execution, no
automatic patch application, no production writes, no dashboard. The API records
what happened; it never acts on it.
