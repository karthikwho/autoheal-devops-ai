"""The AI diagnosis contract: an evidence-backed hypothesis.

Nothing in this module is LLM specific. The diagnosis module (Member 2) is
free to call any model; whatever it produces must satisfy the invariants
enforced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, ValidationInfo, model_validator

from autoheal_contracts.base import (
    CONTRACT_SCHEMA_VERSION,
    ContractModel,
    ShortText,
    UTCDateTime,
    assert_not_in_future,
)
from autoheal_contracts.enums import FailureType, RecommendedAction, RiskLevel
from autoheal_contracts.failure_event import FailureEvent

__all__ = [
    "CONFIDENCE_HUMAN_REVIEW_THRESHOLD",
    "DIAGNOSIS_CONTEXT_KEY",
    "DiagnosisResult",
    "EvidenceItem",
    "EvidenceSource",
    "EvidenceVerificationReport",
    "LOG_VERIFIABLE_SOURCES",
    "QuoteCheck",
    "find_log_offset",
    "verify_diagnosis_against_failure_event",
    "verify_evidence_quotes",
]


#: Below this confidence a diagnosis may never auto-proceed without a human.
CONFIDENCE_HUMAN_REVIEW_THRESHOLD = 0.5

#: Evidence sources that can be byte-checked against ``FailureEvent.logs``.
LOG_VERIFIABLE_SOURCES = frozenset({"failure_logs"})

#: Key used in the Pydantic validation context to supply the originating
#: :class:`FailureEvent`::
#:
#:     DiagnosisResult.model_validate(payload, context={"failure_event": event})
DIAGNOSIS_CONTEXT_KEY = "failure_event"

Quote = Annotated[str, StringConstraints(min_length=3, max_length=4000)]
Explanation = Annotated[str, StringConstraints(min_length=10, max_length=2000)]


class EvidenceSource(StrEnum):
    """Where an :class:`EvidenceItem` quote came from."""

    FAILURE_LOGS = "failure_logs"
    WORKFLOW_DEFINITION = "workflow_definition"
    SOURCE_DIFF = "source_diff"
    REPOSITORY_FILE = "repository_file"
    DEPENDENCY_MANIFEST = "dependency_manifest"
    TEST_REPORT = "test_report"
    PLATFORM_METADATA = "platform_metadata"
    EXTERNAL_REFERENCE = "external_reference"
    #: A conclusion with no artefact behind it. Honest, but never log-checkable.
    INFERRED_NO_SOURCE = "inferred_no_source"


def find_log_offset(logs: str, quote: str) -> int | None:
    """Locate ``quote`` inside ``logs``.

    Matching is exact, so any reader of the raw log blob can reproduce the
    check. Returns ``None`` when the quote is absent -- note that
    :meth:`str.find` reports absence as ``-1``, which must never be mistaken
    for "found at the last character".
    """
    if not logs or not quote:
        return None
    offset = logs.find(quote)
    return None if offset < 0 else offset


class EvidenceItem(ContractModel):
    """One piece of evidence supporting a probable cause.

    ``quote`` must be an exact substring of the artefact named by ``source``.
    When ``source`` is :attr:`EvidenceSource.FAILURE_LOGS` and the originating
    :class:`FailureEvent` is supplied, the quote is verified automatically at
    validation time -- see :func:`verify_evidence_quotes`.

    ``log_offset`` is optional and, when present, is cross-checked against the
    real offset so a fabricated offset cannot pass as evidence.
    """

    source: EvidenceSource
    quote: Quote
    explanation: Explanation
    log_offset: Annotated[int | None, Field(ge=0)] = None

    @property
    def is_log_verifiable(self) -> bool:
        """True when this quote can be byte-checked against the failure logs."""
        return str(self.source) in LOG_VERIFIABLE_SOURCES

    @model_validator(mode="after")
    def _log_offset_only_for_log_sources(self) -> EvidenceItem:
        """``log_offset`` is a position in the failure log; only a log quote has one."""
        if self.log_offset is not None and not self.is_log_verifiable:
            raise ValueError(
                "log_offset is only meaningful when source is "
                f"'{EvidenceSource.FAILURE_LOGS.value}', got '{self.source.value}'"
            )
        return self

    def find_in_logs(self, logs: str) -> int | None:
        """Return the offset of ``quote`` in ``logs``, or ``None`` if absent."""
        return find_log_offset(logs, self.quote)


@dataclass(frozen=True)
class QuoteCheck:
    """Outcome of checking one evidence quote.

    ``verifiable`` records that the quote's source can be byte-checked against
    the failure log. A quote from a workflow file or a manifest has nothing to
    check against the log, so it is not a failure -- but ``checked`` states
    that explicitly, so a report never looks more thorough than it was.
    """

    source: str
    quote: str
    verifiable: bool
    checked: bool
    found_in_logs: bool
    log_offset: int | None
    expected_log_offset: int | None = None
    note: str | None = None

    @property
    def ok(self) -> bool:
        if not self.verifiable:
            return True
        if not self.checked or not self.found_in_logs:
            return False
        if self.expected_log_offset is not None:
            return self.log_offset == self.expected_log_offset
        return True


@dataclass(frozen=True)
class EvidenceVerificationReport:
    """Aggregate outcome of quote verification against a failure event.

    ``logs_available=False`` means the logs were not supplied. Quotes that
    *could* have been checked are then reported as unverified -- absence of
    evidence is never counted as evidence.
    """

    incident_id: str
    logs_available: bool
    checks: tuple[QuoteCheck, ...] = ()

    @property
    def verified(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def unverified_quotes(self) -> tuple[str, ...]:
        return tuple(check.quote for check in self.unverified_items)

    @property
    def unverified_items(self) -> tuple[QuoteCheck, ...]:
        """Only the checks that should have passed and did not."""
        return tuple(check for check in self.checks if check.verifiable and not check.ok)

    @property
    def unchecked_items(self) -> tuple[QuoteCheck, ...]:
        """Sources with nothing to check against the log."""
        return tuple(check for check in self.checks if not check.verifiable)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe rendering, used by tests and future API endpoints."""
        return {
            "incident_id": self.incident_id,
            "logs_available": self.logs_available,
            "verified": self.verified,
            "unverified_quotes": list(self.unverified_quotes),
            "checks": [
                {
                    "source": c.source,
                    "quote": c.quote,
                    "verifiable": c.verifiable,
                    "checked": c.checked,
                    "found_in_logs": c.found_in_logs,
                    "log_offset": c.log_offset,
                    "expected_log_offset": c.expected_log_offset,
                    "ok": c.ok,
                    "note": c.note,
                }
                for c in self.checks
            ],
        }


def verify_evidence_quotes(
    diagnosis: DiagnosisResult,
    failure_event: FailureEvent | None,
) -> EvidenceVerificationReport:
    """Check every log-sourced quote in ``diagnosis`` against the raw logs."""
    logs: str | None = failure_event.logs if failure_event is not None else None
    checks: list[QuoteCheck] = []

    for item in diagnosis.evidence:
        verifiable = item.is_log_verifiable
        offset = find_log_offset(logs, item.quote) if verifiable and logs else None

        if not verifiable:
            note = "source is not the raw failure logs; nothing to verify against"
        elif not logs:
            note = "failure logs unavailable; quote could not be verified"
        else:
            note = None

        checks.append(
            QuoteCheck(
                source=str(item.source),
                quote=item.quote,
                verifiable=verifiable,
                checked=verifiable and bool(logs),
                found_in_logs=offset is not None,
                log_offset=offset,
                expected_log_offset=item.log_offset,
                note=note if offset is None else None,
            )
        )

    return EvidenceVerificationReport(
        incident_id=diagnosis.incident_id,
        logs_available=bool(logs),
        checks=tuple(checks),
    )


class DiagnosisResult(ContractModel):
    """A diagnosis: probable cause, evidence, confidence and a proposed next step.

    Required fields
    ---------------
    ``evidence``
        At least one item, each carrying ``source``, ``quote`` and
        ``explanation``. A diagnosis with no evidence is not accepted.
    ``limitations``
        Always required. Every diagnosis must state what it does *not* know.
    ``confidence``
        ``0.0 <= confidence <= 1.0``.

    Enforced invariants
    -------------------
    * ``risk_level`` of ``high``/``critical`` forces ``requires_human_review``.
    * ``confidence`` below :data:`CONFIDENCE_HUMAN_REVIEW_THRESHOLD` forces
      ``requires_human_review``.
    * ``failure_type`` of ``unknown`` forces ``requires_human_review``.
    * ``recommended_action`` of ``escalate_to_human`` forces
      ``requires_human_review``.
    * ``diagnosed_at`` must not be in the future.
    * With a :class:`FailureEvent` in the validation context, the ids and
      failure types must agree, ``diagnosed_at`` must not predate the failure,
      and every ``failure_logs`` quote must be present verbatim.

    A ``DiagnosisResult`` is a *hypothesis*. It never means a repair is safe or
    verified; that is the job of :class:`ValidationResult`.
    """

    schema_version: Literal[CONTRACT_SCHEMA_VERSION] = CONTRACT_SCHEMA_VERSION
    incident_id: ShortText
    failure_type: FailureType
    probable_cause: Annotated[str, StringConstraints(min_length=10, max_length=1000)]
    evidence: Annotated[list[EvidenceItem], Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    recommended_action: RecommendedAction
    risk_level: RiskLevel
    requires_human_review: bool
    limitations: Annotated[str, StringConstraints(min_length=10, max_length=2000)]

    diagnosed_at: UTCDateTime
    #: Free-form label of the model/tooling used, e.g. ``"anthropic/claude"``.
    #: Provider neutral by design: the contract never names a vendor.
    diagnosis_producer: ShortText | None = None

    @model_validator(mode="after")
    def _risk_forces_human_review(self) -> DiagnosisResult:
        if (
            self.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            and not self.requires_human_review
        ):
            raise ValueError(
                f"risk_level '{self.risk_level.value}' requires requires_human_review=true"
            )
        return self

    @model_validator(mode="after")
    def _low_confidence_forces_human_review(self) -> DiagnosisResult:
        if self.confidence < CONFIDENCE_HUMAN_REVIEW_THRESHOLD and not self.requires_human_review:
            raise ValueError(
                f"confidence {self.confidence} is below "
                f"{CONFIDENCE_HUMAN_REVIEW_THRESHOLD}; requires_human_review must be true"
            )
        return self

    @model_validator(mode="after")
    def _unknown_failure_type_forces_human_review(self) -> DiagnosisResult:
        if self.failure_type is FailureType.UNKNOWN and not self.requires_human_review:
            raise ValueError("failure_type 'unknown' requires requires_human_review=true")
        return self

    @model_validator(mode="after")
    def _escalation_forces_human_review(self) -> DiagnosisResult:
        if (
            self.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
            and not self.requires_human_review
        ):
            raise ValueError(
                "recommended_action 'escalate_to_human' requires requires_human_review=true"
            )
        return self

    @model_validator(mode="after")
    def _diagnosed_at_not_in_future(self) -> DiagnosisResult:
        assert_not_in_future(self.diagnosed_at, "diagnosed_at")
        return self

    @model_validator(mode="after")
    def _coherent_with_originating_event(self, info: ValidationInfo) -> DiagnosisResult:
        """Verify against the originating event when it is in the context."""
        event = (info.context or {}).get(DIAGNOSIS_CONTEXT_KEY)
        if event is None:
            return self
        problems = verify_diagnosis_against_failure_event(self, event)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def verify_evidence(self, failure_event: FailureEvent | None) -> EvidenceVerificationReport:
        """Verify this diagnosis's evidence quotes against ``failure_event``."""
        return verify_evidence_quotes(self, failure_event)


def verify_diagnosis_against_failure_event(
    diagnosis: DiagnosisResult,
    failure_event: FailureEvent,
) -> list[str]:
    """Cross-check a diagnosis against the failure event it came from.

    Returns human-readable problems; an empty list means the diagnosis is
    coherent with the event. These are contract-consistency problems, not
    quality judgements about the diagnosis itself.
    """
    problems: list[str] = []

    if diagnosis.incident_id != failure_event.incident_id:
        problems.append(
            f"incident_id mismatch: diagnosis '{diagnosis.incident_id}' "
            f"vs failure event '{failure_event.incident_id}'"
        )

    if diagnosis.failure_type != failure_event.failure_type:
        problems.append(
            f"failure_type mismatch: diagnosis '{diagnosis.failure_type.value}' "
            f"vs failure event '{failure_event.failure_type.value}'"
        )

    if diagnosis.diagnosed_at < failure_event.timestamp:
        problems.append(
            "diagnosed_at predates the failure timestamp "
            f"({diagnosis.diagnosed_at.isoformat()} < {failure_event.timestamp.isoformat()})"
        )

    for check in verify_evidence_quotes(diagnosis, failure_event).unverified_items:
        if check.found_in_logs:
            problems.append(f"evidence quote offset mismatch: {check.quote!r}")
        else:
            problems.append(f"evidence quote not found in failure logs: {check.quote!r}")

    return problems
