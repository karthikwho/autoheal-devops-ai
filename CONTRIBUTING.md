# Contributing

This guide covers how to work in the AutoHeal DevOps AI repository: branches,
pull requests, tests, and the one rule that outranks everything else.

---

## The rule

> **`main` must always be stable.**

`main` is the branch every other module integrates against. It must, at every
commit:

* have a green test suite;
* keep the shared contracts backwards compatible (or ship a version bump with
  a migration note, per [`api-contracts.md` §9](docs/api-contracts.md));
* keep the API starting and every documented endpoint behaving as documented.

If a change cannot meet that bar, it does not go to `main`. Nobody "fixes it
later" on `main`.

---

## Workflow

### 1. Branch off `main`

Feature branches are short-lived and named for what they do:

```bash
git switch main && git pull
git switch -c feature/attach-diagnosis-endpoint
```

Convention: `<type>/<short-kebab-summary>`, where type is one of
`feature`, `fix`, `contracts`, `docs`, `chore`, `test`.

Branch prefixes by owner area are welcome and encouraged, for example
`feature/ingestion-github-webhook`.

### 2. Commit in small pieces

Conventional commits. One logical change per commit.

```bash
git commit -m "contracts: add DiagnosisResult evidence verification"
git commit -m "api: return 409 on duplicate incident_id"
git commit -m "docs: document confidence semantics"
```

Types: `feat`, `fix`, `contracts`, `api`, `docs`, `test`, `refactor`, `chore`.

### 3. Keep it green locally

Before you open a pull request:

```bash
uv sync
uv run pytest          # the whole suite
uv run ruff check .
uv run ruff format .
```

CI runs the same three commands. A PR that fails any of them does not get
reviewed.

### 4. Open a pull request

Target `main`. The PR description must state:

* **what** changed, and why;
* **contracts affected**, if any — say explicitly whether the change is
  backwards compatible;
* **test results** — paste the actual summary line, not a claim;
* **known limitations** and follow-up work.

Reviewers should be able to answer "is `main` still stable after this?" from
the description alone.

### 5. Merge

Squash or rebase merge, then delete the branch. Never force-push to `main`.

---

## Test expectations

### Running

```bash
uv run pytest                       # everything
uv run pytest tests/contracts -q    # contracts only
uv run pytest tests/api -q          # endpoints and storage only
uv run pytest -k confidence -v      # one topic
```

Markers: `contracts` and `api` (registered in `pyproject.toml`).

### What must be tested

A change is not complete until the tests that would have caught its absence
exist.

| Change | Required tests |
| --- | --- |
| A new or changed contract field | Valid payload, missing field, invalid type, invalid enum, and each cross-field invariant it touches |
| A new invariant | A test that the invariant rejects the violating payload |
| A new endpoint | Success, each documented failure status, and the empty case |
| A storage change | Create/read/update/delete plus the reopen-after-restore path |

### Rules for tests

* **Isolated storage.** Every test that touches storage must use `tmp_path` or
  the in-memory backend. Never write into a real data directory.
* **No network, no credentials.** No GitHub tokens, no LLM API keys, no live
  HTTP. The only client used (`httpx2`) talks to the in-process ASGI app.
* **No invented results.** Run the suite and report what it printed. Never
  hand-write an expected summary line.
* **Snapshot the safety rules.** The tests covering "a proposed fix is not a
  validated fix", evidence-quote verification, and forced human review are
  safety-critical. Changing them requires a reviewer who understands why they
  exist.
* **Deterministic.** No sleeps waiting for clocks, no ordering assumptions
  beyond what the contract guarantees.

---

## Architecture boundaries

Keep these, even when it would be faster not to:

* **Contracts have no framework deps.** `packages/contracts` imports Pydantic
  and the standard library. Not FastAPI, not HTTP, not a database, not an LLM
  SDK. If you need HTTP, that belongs in `apps/api`.
* **Storage stays behind the interface.** The API depends on `IncidentStore`,
  never on a concrete backend. New backends implement the ABC and get one
  branch in `create_store`.
* **No second schema to drift.** `POST /api/v1/incidents` accepts a
  `FailureEvent` directly. Do not introduce a parallel API-level DTO.
* **Safety rules live in the contracts, not in each caller.** If a rule can be
  a model validator, make it one.
* **Don't add infrastructure casually.** No PostgreSQL, Redis, Celery or
  Kubernetes without an ADR in `docs/`. The current baseline is deliberately
  dependency-light.

Reserved directories (`packages/ingestion`, `packages/diagnosis`,
`packages/validation`, `packages/remediation`, `packages/incident_store`,
`apps/dashboard`, `tests/evaluation`, `tests/fixtures`, `tests/integration`)
are claimed by other members. Coordinate before writing into one.

---

## What is out of scope for now

Deliberately not built in M0, and not to be added opportunistically:

* the AI diagnosis engine;
* arbitrary command execution;
* automatic patch application;
* production deployment or infrastructure;
* the dashboard.

If your PR adds one of these, it is a different milestone. Start a discussion
first.

---

## Setup reference

```bash
# Python 3.11+ (developed and verified on 3.14)
uv sync

uv run uvicorn autoheal_api.main:app --reload          # API on :8000
AUTOHEAL_STORE_BACKEND=memory \
  uv run uvicorn autoheal_api.main:app                 # no filesystem at all

uv run ruff check . && uv run ruff format .
```
