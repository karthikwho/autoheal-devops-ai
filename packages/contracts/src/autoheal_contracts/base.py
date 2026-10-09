"""Base model and shared field types for every AutoHeal contract.

Design rules that apply to all contract models:

* ``extra="forbid"`` -- an unknown key is a hard error. A typo in a JSON
  payload must never be accepted silently into a shared contract.
* Timestamps are always timezone aware, normalised to UTC and serialised as
  RFC 3339 strings ending in ``Z`` (for example
  ``2026-10-09T12:00:00Z``). Naive datetimes are rejected.
* Field constraints are declared in the type, so the JSON Schema exported to
  Members 1 and 2 documents them for free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
)

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ContractModel",
    "MAX_CLOCK_SKEW_SECONDS",
    "MAX_LOG_CHARS",
    "NonEmptyText",
    "ShortText",
    "UTCDateTime",
    "assert_not_in_future",
    "coerce_utc",
    "utcnow",
]

#: Version of the shared contract payloads. Bump on any breaking change to a
#: contract shape and keep a migration note in docs/api-contracts.md.
CONTRACT_SCHEMA_VERSION = "1.0"

#: Upper bound on the raw log payload accepted in a :class:`FailureEvent`.
#: Keeps ingestion from pushing entire build blobs through the API.
MAX_LOG_CHARS = 500_000


#: Tolerated clock skew between the reporting process's clock and ours. Any
#: timestamp beyond this in the future is rejected rather than trusted.
MAX_CLOCK_SKEW_SECONDS = 300


def assert_not_in_future(value: datetime, label: str) -> None:
    """Raise if ``value`` is more than a tolerated clock skew in the future.

    Used for every timestamp field whose "now" is produced by a machine we do
    not control (the CI reporter, the diagnosis producer, the test runner).
    """
    if value > utcnow() + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            f"{label} is in the future beyond the tolerated clock skew of {MAX_CLOCK_SKEW_SECONDS}s"
        )


def coerce_utc(value: Any) -> Any:
    """Normalise ``value`` into a timezone-aware UTC ``datetime``.

    Accepts anything Pydantic already understands (``datetime``, ISO 8601
    string, epoch seconds) and rejects naive datetimes, because "2026-10-09
    12:00:00" with no offset is not a fact -- it is an ambiguity.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp must not be empty")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - message is pass-through
            raise ValueError(f"invalid ISO 8601 timestamp: {text!r}") from exc
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value), tz=UTC)
    else:
        raise ValueError("timestamp must be an ISO 8601 string or a datetime")

    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError("timestamp must be timezone aware, e.g. '2026-10-09T12:00:00Z'")
    return parsed.astimezone(UTC)


def _serialize_utc(value: datetime) -> str:
    """Render a UTC instant as RFC 3339 with a ``Z`` suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


#: A timezone-aware UTC instant. Serialises to ``...Z`` RFC 3339 text and
#: round-trips back through :meth:`pydantic.BaseModel.model_validate`.
UTCDateTime = Annotated[
    datetime,
    BeforeValidator(coerce_utc),
    PlainSerializer(_serialize_utc, return_type=str, when_used="always"),
]

#: Short identifier-ish text: stripped, and guaranteed to be non-empty.
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]

#: Longer free text (summaries, probable cause, limitations): stripped, but
#: *not* whitespace-normalised, so log quotes remain byte-comparable.
NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=4000, strip_whitespace=True),
]


def utcnow() -> datetime:
    """Current instant as a timezone-aware UTC ``datetime``."""
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Base class shared by all AutoHeal contracts.

    Adds two behaviours on top of Pydantic defaults:

    ``extra="forbid"``
        Unknown keys are rejected so payload drift fails loudly.

    ``validate_assignment=True``
        Mutating a field re-runs validation, so an invariant can never be
        broken by ``record.status = "validated"``.

    ``evolve(**changes)``
        Returns a new, fully re-validated instance. Used to move an incident
        through its lifecycle without any pathway bypassing the validators.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )

    def evolve(self: Self, **changes: Any) -> Self:
        """Return a copy of this model with ``changes`` applied and validated.

        ``model_copy`` does not run validators, so the instance is first dumped
        and re-validated through :meth:`model_validate`. Any invariant broken
        by ``changes`` raises :class:`pydantic.ValidationError` here rather than
        silently corrupting the record.
        """
        data = self.model_dump()
        data.update(changes)
        return type(self).model_validate(data)
