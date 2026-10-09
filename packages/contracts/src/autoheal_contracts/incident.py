"""The aggregate contract: one incident, its whole story."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from autoheal_contracts.base import (
    CONTRACT_SCHEMA_VERSION,
    ContractModel,
    ShortText,
    UTCDateTime,
    coerce_utc,
    utcnow,
)
from autoheal_contracts.diagnosis import DiagnosisResult
from autoheal_contracts.enums import FailureType, IncidentStatus, RiskLevel
from autoheal_contracts.failure_event import INCIDENT_ID_PATTERN, FailureEvent
from autoheal_contracts.validation import ValidationResult

__all__ = ["IncidentRecord", "StatusTransition", "STATUS_PRECONDITIONS"]


def _coerce_now(value: datetime | str) -> datetime:
    """Normalise a caller-supplied instant used for transition timestamps."""
    return coerce_utc(value)  # type: ignore[return-value]


#: Statuses that may only be reached once a diagnosis is attached.
_STATUSES_REQUIRING_DIAGNOSIS = frozenset(
    {
        IncidentStatus.DIAGNOSED,
        IncidentStatus.AWAITING_REVIEW,
        IncidentStatus.REMEDIATION_PROPOSED,
        IncidentStatus.REMEDIATION_APPROVED,
        IncidentStatus.VALIDATING,
        IncidentStatus.VALIDATED,
        IncidentStatus.VALIDATION_FAILED,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    }
)

#: Statuses that may only be reached once a validation has actually run.
_STATUSES_REQUIRING_VALIDATION = frozenset(
    {IncidentStatus.VALIDATED, IncidentStatus.VALIDATION_FAILED, IncidentStatus.RESOLVED}
)

#: Statuses that require the validation to have passed.
_STATUSES_REQUIRING_PASSING_VALIDATION = frozenset(
    {IncidentStatus.VALIDATED, IncidentStatus.RESOLVED}
)

#: Terminal statuses.
_TERMINAL_STATUSES = frozenset(
    {
        IncidentStatus.VALIDATED,
        IncidentStatus.VALIDATION_FAILED,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
        IncidentStatus.ABANDONED,
    }
)


class StatusTransition(ContractModel):
    """One audited lifecycle transition."""

    status: IncidentStatus
    at: UTCDateTime
    note: ShortText | None = None


class IncidentRecord(ContractModel):
    """The aggregate root for a single AutoHeal incident.

    Required fields
    ---------------
    ``incident_id``
        Must equal the ids of the carried ``failure_event``, ``diagnosis`` and
        ``validation``.
    ``failure_event``
        Always present; an incident exists because a failure was reported.
    ``status``
        See :class:`~autoheal_contracts.enums.IncidentStatus`.
    ``created_at`` / ``updated_at``
        Timezone aware, normalised to UTC. ``updated_at >= created_at``.

    Enforced invariants
    -------------------
    * Ids must agree across every embedded payload.
    * ``created_at`` may not predate the reported failure.
    * Every embedded payload must belong to this incident.
    * A status that needs a diagnosis must carry one, and vice versa: a
      ``RECEIVED`` incident must not carry a diagnosis.
    * ``VALIDATED`` / ``RESOLVED`` require a ``ValidationResult`` with
      ``passed=true``; ``VALIDATION_FAILED`` requires one with ``passed=false``.
      No status can assert an outcome the validation does not support.
    * ``status_history`` starts at ``RECEIVED`` and its last entry is the
      current status.
    """

    schema_version: Literal[CONTRACT_SCHEMA_VERSION] = CONTRACT_SCHEMA_VERSION
    incident_id: Annotated[str, StringConstraints(pattern=INCIDENT_ID_PATTERN)]
    failure_event: FailureEvent
    diagnosis: DiagnosisResult | None = None
    validation: ValidationResult | None = None
    status: IncidentStatus = IncidentStatus.RECEIVED
    created_at: UTCDateTime
    updated_at: UTCDateTime
    status_history: Annotated[list[StatusTransition], Field(default_factory=list)]

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _ids_must_agree(self) -> IncidentRecord:
        payloads: list[tuple[str, str]] = [
            ("failure_event", self.failure_event.incident_id),
        ]
        if self.diagnosis is not None:
            payloads.append(("diagnosis", self.diagnosis.incident_id))
        if self.validation is not None:
            payloads.append(("validation", self.validation.incident_id))

        for label, value in payloads:
            if value != self.incident_id:
                raise ValueError(
                    f"incident_id mismatch: record '{self.incident_id}' vs {label} '{value}'"
                )
        return self

    @model_validator(mode="after")
    def _record_not_backdated(self) -> IncidentRecord:
        if self.created_at < self.failure_event.timestamp:
            raise ValueError(
                "created_at predates the failure timestamp "
                f"({self.created_at.isoformat()} < {self.failure_event.timestamp.isoformat()})"
            )
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at predates created_at "
                f"({self.updated_at.isoformat()} < {self.created_at.isoformat()})"
            )
        return self

    @model_validator(mode="after")
    def _status_requires_diagnosis(self) -> IncidentRecord:
        if self.status in _STATUSES_REQUIRING_DIAGNOSIS and self.diagnosis is None:
            raise ValueError(f"status '{self.status.value}' requires a diagnosis")
        if self.status is IncidentStatus.RECEIVED and self.diagnosis is not None:
            raise ValueError(
                "status 'received' must not carry a diagnosis; move to 'diagnosed' first"
            )
        return self

    @model_validator(mode="after")
    def _status_requires_matching_validation(self) -> IncidentRecord:
        if self.status in _STATUSES_REQUIRING_VALIDATION and self.validation is None:
            raise ValueError(f"status '{self.status.value}' requires a validation result")
        if self.status in _STATUSES_REQUIRING_PASSING_VALIDATION:
            if self.validation is None or not self.validation.passed:
                raise ValueError(
                    f"status '{self.status.value}' requires a passing validation result"
                )
        if self.status is IncidentStatus.VALIDATION_FAILED:
            if self.validation is None or self.validation.passed:
                raise ValueError("status 'validation_failed' requires a failing validation result")
        return self

    @model_validator(mode="after")
    def _history_is_consistent(self) -> IncidentRecord:
        if not self.status_history:
            raise ValueError("status_history must contain at least one transition")
        if self.status_history[0].status is not IncidentStatus.RECEIVED:
            raise ValueError("status_history must start with 'received'")
        if self.status_history[-1].status is not self.status:
            raise ValueError(
                "status_history must end with the current status "
                f"('{self.status_history[-1].status.value}' vs '{self.status.value}')"
            )
        return self

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        """True when no further transition is expected."""
        return self.status in _TERMINAL_STATUSES

    @property
    def is_recovery_verified(self) -> bool:
        """True only when a real validation actually passed.

        An incident that merely carries a diagnosis, or whose validation ran
        no tests, is *not* verified. This is the flag operators should read as
        "AutoHeal proved the fix".
        """
        return (
            self.status in _STATUSES_REQUIRING_PASSING_VALIDATION
            and self.validation is not None
            and self.validation.is_recovery_verified
        )

    @property
    def needs_human_review(self) -> bool:
        """True when this incident must not progress without a human."""
        if self.status in {IncidentStatus.AWAITING_REVIEW, IncidentStatus.ESCALATED}:
            return True
        if self.diagnosis is not None and self.diagnosis.requires_human_review:
            return True
        if self.diagnosis is not None and self.diagnosis.risk_level in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }:
            return True
        return self.failure_event.failure_type is FailureType.UNKNOWN

    @classmethod
    def from_failure_event(
        cls,
        failure_event: FailureEvent,
        *,
        now: datetime | str | None = None,
    ) -> IncidentRecord:
        """Create a fresh ``RECEIVED`` record from an ingested failure event."""
        created = _coerce_now(now) if now is not None else utcnow()
        if created < failure_event.timestamp:
            raise ValueError("cannot create an incident dated before the failure it describes")
        return cls.model_validate(
            {
                "incident_id": failure_event.incident_id,
                "failure_event": failure_event,
                "status": IncidentStatus.RECEIVED,
                "created_at": created,
                "updated_at": created,
                "status_history": [{"status": IncidentStatus.RECEIVED, "at": created}],
            }
        )

    # ------------------------------------------------------------------
    # Lifecycle transitions (all re-validated)
    # ------------------------------------------------------------------
    def transition(
        self,
        status: IncidentStatus,
        *,
        note: str | None = None,
        now: datetime | str | None = None,
    ) -> IncidentRecord:
        """Move the incident to ``status``, appending an audited transition.

        The returned record is re-validated in full, so an illegal transition
        raises rather than producing an inconsistent aggregate.
        """
        at = coerce_utc(now) if now is not None else utcnow()
        history = [*self.status_history, {"status": status, "at": at, "note": note}]
        return self.evolve(status=status, updated_at=at, status_history=history)

    def attach_diagnosis(self, diagnosis: DiagnosisResult) -> IncidentRecord:
        """Record a diagnosis and move to ``DIAGNOSED`` or ``AWAITING_REVIEW``.

        The payload and the new status are applied in a single re-validation,
        so the record never exists in an intermediate state that asserts a
        status its payload does not support.
        """
        target = (
            IncidentStatus.AWAITING_REVIEW
            if diagnosis.requires_human_review
            else IncidentStatus.DIAGNOSED
        )
        at = utcnow()
        history = [*self.status_history, {"status": target, "at": at, "note": "diagnosis recorded"}]
        return self.evolve(
            diagnosis=diagnosis,
            status=target,
            updated_at=at,
            status_history=history,
        )

    def attach_validation(self, validation: ValidationResult) -> IncidentRecord:
        """Record a validation outcome and move to the matching terminal state."""
        target = IncidentStatus.VALIDATED if validation.passed else IncidentStatus.VALIDATION_FAILED
        at = utcnow()
        history = [
            *self.status_history,
            {"status": target, "at": at, "note": validation.status_note},
        ]
        return self.evolve(
            validation=validation,
            status=target,
            updated_at=at,
            status_history=history,
        )


#: Plain-text description of the payload each status requires. Used by the
#: docs and available to callers that need to explain a 422.
STATUS_PRECONDITIONS: dict[str, str] = {
    IncidentStatus.RECEIVED.value: "a failure_event, and no diagnosis",
    IncidentStatus.DIAGNOSED.value: "a diagnosis",
    IncidentStatus.AWAITING_REVIEW.value: "a diagnosis requiring human review",
    IncidentStatus.REMEDIATION_PROPOSED.value: "a diagnosis",
    IncidentStatus.REMEDIATION_APPROVED.value: "a diagnosis approved by a human",
    IncidentStatus.VALIDATING.value: "a diagnosis and an approved remediation",
    IncidentStatus.VALIDATED.value: "a passing validation result",
    IncidentStatus.VALIDATION_FAILED.value: "a failing validation result",
    IncidentStatus.ESCALATED.value: "a diagnosis, escalated to a human",
    IncidentStatus.RESOLVED.value: "a passing validation result",
    IncidentStatus.CLOSED.value: "a passing validation result",
    IncidentStatus.ABANDONED.value: "no further requirements",
}
