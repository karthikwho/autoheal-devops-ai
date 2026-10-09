# Architecture

AutoHeal DevOps AI is a monorepo of small, independently owned packages joined
by a shared contract layer. This document describes the design as of
**Milestone M0** and names the boundaries the next milestones must respect.

---

## 1. Principles

**Contracts first.** Every module communicates through typed, validated
Pydantic models in `packages/contracts`. There is no untyped pipeline
"just for now", because an untyped boundary is where the safety rules quietly
stop applying.

**The contracts carry the safety rules.** Invariants are enforced by the
models, not by convention in each caller. A caller cannot construct a
`DiagnosisResult` asserting verified recovery, so no caller can report it.

**Provider neutral.** No contract names a CI vendor, an LLM vendor or a
database. `FailureEvent` normalises *away* from GitHub, GitLab or anything
else; `DiagnosisResult` has a free-text `diagnosis_producer` label and no
vendor-specific fields.

**Storage behind an interface.** The API depends on the `IncidentStore` ABC,
never on a concrete backend. Swapping JSONL for SQLite is one new class.

**Nothing acts on production.** M0 records. Diagnosis, remediation, validation
and deployment are later milestones with their own safety work.

---

## 2. Module map

```
                        ┌─────────────────────────────┐
                        │   CI/CD platform (external) │
                        └──────────────┬──────────────┘
                                       │ webhook / poll
                                       ▼
                        ┌─────────────────────────────┐
                        │  packages/ingestion         │  Member 1
                        │  → FailureEvent             │
                        └──────────────┬──────────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            │                          │                          │
            ▼                          ▼                          ▼
  ┌──────────────────┐   ┌──────────────────────────┐   ┌────────────────────┐
  │ packages/        │   │ apps/api                 │   │ packages/          │
  │ contracts        │◀──│  FastAPI endpoints       │   │ diagnosis          │
  │ (shared, M0)     │──▶│  + IncidentStore          │   │ → DiagnosisResult  │
  └──────────────────┘   └───────────┬──────────────┘   └─────────┬──────────┘
                                     │                             │
                                     │            ┌────────────────┘
                                     ▼            ▼
                          ┌────────────────────────────────┐
                          │ packages/remediation           │  later
                          │ packages/validation            │  later
                          │ → ValidationResult             │
                          └────────────────────────────────┘
```

### `packages/contracts`

The single source of truth. Depends on **Pydantic and the standard library
only** — no FastAPI, no HTTP, no database, no LLM SDK. Anything in the system
that must not drift imports from here.

| Model | Produced by | Meaning |
| --- | --- | --- |
| `FailureEvent` | ingestion (Member 1) | A normalised CI failure |
| `DiagnosisResult` | diagnosis (Member 2) | An evidence-backed hypothesis |
| `ValidationResult` | validation (later) | Proof that checks were re-run |
| `IncidentRecord` | API / store | The aggregate plus lifecycle state |

### `apps/api`

A thin FastAPI application. It holds no business logic beyond translating HTTP
to store calls: the request body of `POST /api/v1/incidents` **is** a
`FailureEvent`, so there is no second schema to keep in sync.

```
autoheal_api/
  main.py            create_app() factory + module-level app for uvicorn
  config.py          Settings, read from environment
  deps.py            FastAPI dependencies (settings, store, list filters)
  errors.py          ApiError hierarchy + JSON error envelope
  schemas.py         Response envelopes (pagination, health)
  routes/
    health.py        GET /health
    incidents.py     GET/POST /api/v1/incidents, GET /{incident_id}
  storage/
    base.py          IncidentStore ABC, IncidentFilter, exceptions
    memory.py        InMemoryIncidentStore
    json_file.py     JsonFileIncidentStore (default)
    __init__.py      create_store() backend factory
```

---

## 3. Storage

`IncidentStore` is the only persistence surface the API knows:

```python
class IncidentStore(ABC):
    def create(self, record: IncidentRecord) -> IncidentRecord: ...
    def get(self, incident_id: str) -> IncidentRecord | None: ...
    def get_or_raise(self, incident_id: str) -> IncidentRecord: ...
    def list(self, criteria: IncidentFilter | None = None) -> tuple[list[IncidentRecord], int]: ...
    def save(self, record: IncidentRecord) -> IncidentRecord: ...
    def delete(self, incident_id: str) -> bool: ...
    def clear(self) -> None: ...
    def count(self) -> int: ...
```

Two implementations are bundled:

| Backend | Persists | Use |
| --- | --- | --- |
| `memory` | Nothing | Tests, single-shot local runs |
| `json_file` | `incidents.jsonl`, atomic rewrite | Default for local runs |

`json_file` is deliberately *local development* storage: single host, single
file, scan-on-start, whole-file rewrite per mutation. It exists so that `GET`
after a restart returns what was `POST`ed, which proves the persistence
contract without a server. Every mutation writes to a temporary file and calls
`os.replace`, so a crash mid-write cannot truncate an existing store — covered
by `test_a_failed_write_leaves_the_previous_file_intact`.

**Replacing it with SQLite** is:

1. implement `IncidentStore` against `sqlite3` (or SQLAlchemy);
2. add one branch to `create_store`;
3. set `AUTOHEAL_STORE_BACKEND=sqlite`.

No route, no dependency, no test that goes through the `client` fixture needs
to change.

---

## 4. Contract design

### Timestamps

Every timestamp is timezone aware, normalised to UTC, and serialised as RFC 3339
with a `Z` suffix:

```
2026-10-09T12:00:00Z
```

* A naive datetime — `2026-10-09 12:00:00` — is **rejected**. It is not a
  moment in time, it is an ambiguity.
* An offset timestamp — `2026-10-09T14:00:00+02:00` — is accepted and
  normalised to UTC.
* Timestamps more than `MAX_CLOCK_SKEW_SECONDS` (300s) in the future are
  rejected, because the reporting machine's clock is not ours to trust.

This is implemented once, as the reusable `UTCDateTime` annotated type, so the
rule cannot be forgotten on a new field.

### Confidence

`0.0 <= confidence <= 1.0`. It means: *how strongly the diagnosis module
believes its own hypothesis*. It is not a probability of the fix working, and
it is never a statement about remediation safety.

Below `CONFIDENCE_HUMAN_REVIEW_THRESHOLD = 0.5` a diagnosis **must** set
`requires_human_review = true`, enforced by the model.

### Evidence

`DiagnosisResult.evidence` is a list of `EvidenceItem`, each with:

* `source` — an `EvidenceSource` enum: `failure_logs`, `workflow_definition`,
  `source_diff`, `repository_file`, `dependency_manifest`, `test_report`,
  `platform_metadata`, `external_reference`, `inferred_no_source`.
* `quote` — an exact substring of the artefact named by `source`.
* `explanation` — why this quote supports the probable cause (minimum length
  10, so "because" is not an explanation).
* `log_offset` — optional; only valid when `source` is `failure_logs`.

**Verification.** When `source` is `failure_logs` and the originating
`FailureEvent` is supplied, the quote is checked byte-for-byte at validation
time:

```python
diagnosis = DiagnosisResult.model_validate(payload, context={"failure_event": event})
```

A quote that is not present in the logs is a `ValidationError`. A `log_offset`
that does not match the real offset is also rejected. This is what makes
fabricated evidence structurally impossible rather than merely discouraged.

Two honesty properties follow:

* Non-log sources are *not* checked against the log and are reported as
  `checked: False` — a report never looks more thorough than it was.
* When the logs are unavailable, log-sourced quotes are reported as
  **unverified**, never as verified. Absence of evidence is not evidence.

### Lifecycle states

```
                 ┌──────────┐
                 │ RECEIVED │  FailureEvent ingested
                 └────┬─────┘
                      │ diagnosis attached
          ┌───────────┴───────────┐
          ▼                       ▼
   ┌─────────────┐        ┌─────────────────┐
   │  DIAGNOSED  │        │ AWAITING_REVIEW │  risk/confidence/unknown
   └──────┬──────┘        └────────┬────────┘
          │                         │ human approves
          └────────────┬────────────┘
                       ▼
              ┌───────────────────┐
              │ REMEDIATION_...   │  later milestones
              └─────────┬─────────┘
                        ▼
                 ┌────────────┐      fail       ┌──────────────────┐
                 │ VALIDATING │───────────────▶ │ VALIDATION_FAILED│
                 └─────┬──────┘                 └──────────────────┘
                       │ pass                        (terminal)
                       ▼
                 ┌───────────┐     ┌──────────┐
                 │ VALIDATED │────▶│ RESOLVED │──▶ CLOSED
                 └───────────┘     └──────────┘
                       └────────────▶ CLOSED
```

`ESCALATED` and `ABANDONED` are available from any non-terminal state.

### Enforced invariants

These are model validators, so violating them raises `ValidationError` at the
boundary rather than producing an inconsistent record.

**`IncidentRecord`**

* Ids must agree across the record and every embedded payload.
* `created_at >= failure_event.timestamp` — an incident cannot predate the
  failure it describes.
* `updated_at >= created_at`.
* A status that needs a diagnosis must carry one; `RECEIVED` must **not**.
* `VALIDATED` / `RESOLVED` require a `ValidationResult` with `passed = true`.
* `VALIDATION_FAILED` requires one with `passed = false`.
* `status_history` starts at `received` and ends at the current status.

**`DiagnosisResult`**

* `risk_level` of `high` / `critical` forces `requires_human_review`.
* `confidence < 0.5` forces `requires_human_review`.
* `failure_type = unknown` forces `requires_human_review`.
* `recommended_action = escalate_to_human` forces `requires_human_review`.
* `evidence` is non-empty; `limitations` is required and non-trivial.
* Log-sourced quotes must be present verbatim (see above).

**`ValidationResult`**

* `tests_failed <= tests_executed`.
* `passed = true` requires `tests_executed >= 1` and `tests_failed == 0`.
  **A suite that never ran cannot be reported as a pass.**
* `len(failure_details) <= tests_failed`.
* `validated_at` is not in the future.

---

## 5. Endpoints

| Method | Path | Success | Failure |
| --- | --- | --- | --- |
| GET | `/health` | `200` healthy + metadata | — |
| GET | `/api/v1/incidents` | `200` page + total | `422` bad filter |
| GET | `/api/v1/incidents/{incident_id}` | `200` record | `404` unknown id |
| POST | `/api/v1/incidents` | `201` + `Location` header | `422` invalid contract, `409` duplicate id |

Errors share one envelope:

```json
{"error": {"code": "incident_not_found", "message": "...", "details": {}}}
```

Full field-by-field reference with JSON examples:
[`api-contracts.md`](api-contracts.md).

---

## 6. Packaging

A [`uv` workspace](https://docs.astral.sh/uv/concepts/workspaces/) rooted at
`pyproject.toml`, with members `packages/*` and `apps/*`. The root project
declares no runtime dependencies; its `dev` group pulls in both workspace
members plus `pytest`, `httpx2` and `ruff`.

```bash
uv sync            # install the whole workspace
uv run pytest      # run the suite
uv run ruff check .
```

Python requirement is `>=3.11`. Development and CI target 3.14 (verified
FastAPI 0.143, Pydantic 2.14, Starlette 1.7, pytest 9.1 on 3.14.7).

---

## 7. Known limitations (M0)

* **JSON-file storage is local-only.** Single host, whole-file rewrite per
  mutation, no concurrent-process safety. Fine for development; replace with
  SQLite before any shared deployment.
* **No diagnosis or validation endpoints yet.** `DiagnosisResult` and
  `ValidationResult` exist and are fully validated, but `POST` cannot yet
  attach them. That is M1.
* **No authentication or authorisation.** The API binds to `127.0.0.1` by
  default. Exposing it beyond localhost requires an authn story first.
* **No idempotency key.** Retrying a `POST` with the same `incident_id` returns
  `409`. The ingestion module must tolerate that or supply a deterministic id.
* **`list` does not paginate a total efficiently** — it filters in memory.
  Fine at this scale, not fine at a million incidents.
* **No request IDs or structured access logs.**

---

## 8. Integration points for the next milestones

### Member 1 — ingestion (`packages/ingestion`)

Build a `FailureEvent` from the platform payload and `POST` it:

```python
from autoheal_contracts import FailureEvent, IncidentRecord

event = FailureEvent.model_validate(my_normalised_payload)  # fails loudly on drift
```

* Generate `incident_id` as `inc-YYYYMMDD-<6-12 lowercase alphanumerics>`.
* Map the platform's step/outcome onto `FailureType`; use `unknown` when you
  cannot classify — do **not** guess. `unknown` routes to human review, which
  is the correct outcome for an unclassifiable failure.
* Keep `logs` verbatim from the significant lines onward: diagnosis quotes are
  checked against it byte-for-byte.
* Treat `409 Conflict` as "already ingested", not as an error to retry blindly.

### Member 2 — diagnosis (`packages/diagnosis`)

Produce a `DiagnosisResult`; validate it against the originating event:

```python
diagnosis = DiagnosisResult.model_validate(payload, context={"failure_event": event})
problems = verify_diagnosis_against_failure_event(diagnosis, event)
```

* The context validator rejects fabricated quotes and misreported offsets. Use
  it — it is the cheapest way to keep an LLM honest.
* Quote only what you can see. `inferred_no_source` is available and honest.
* Always populate `limitations`. It is a required field, deliberately.
* Confidence is calibrated belief, not a marketing number, and it drives the
  human-review threshold.

### Later — remediation and validation

* Add a `POST /api/v1/incidents/{id}/diagnoses` and
  `POST /api/v1/incidents/{id}/validations`, or call `IncidentRecord.
  attach_diagnosis` / `attach_validation` inside a service layer. Either way
  the store is the boundary.
* Never write a `ValidationResult` with `passed: true` unless tests actually
  ran and passed. The contract rejects it anyway.
