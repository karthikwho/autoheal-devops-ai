"""Shared vocabulary for every AutoHeal contract.

All enums derive from :class:`enum.StrEnum` so that members serialise to their
lower-case wire value, keep a stable ``.value`` for JSON Schema generation and
compare equal to plain strings -- which keeps them provider neutral.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "FailureType",
    "IncidentStatus",
    "RecommendedAction",
    "RiskLevel",
    "ValidationScope",
]


class FailureType(StrEnum):
    """Normalised classification of *why* a pipeline run failed.

    The ingestion module maps a provider-specific failure onto one of these
    values. ``UNKNOWN`` is a first-class outcome: a failure that cannot be
    classified must never be silently forced into a more specific bucket.
    """

    TEST_FAILURE = "test_failure"
    LINT_FORMAT = "lint_format"
    BUILD_COMPILATION = "build_compilation"
    DEPENDENCY_RESOLUTION = "dependency_resolution"
    DEPENDENCY_VULNERABILITY = "dependency_vulnerability"
    CONFIGURATION = "configuration"
    TIMEOUT = "timeout"
    FLAKY_TEST = "flaky_test"
    INFRASTRUCTURE = "infrastructure"
    AUTHENTICATION_PERMISSIONS = "authentication_permissions"
    SECURITY_SCAN = "security_scan"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    """How dangerous acting on a diagnosis would be.

    ``HIGH`` and ``CRITICAL`` always force human review -- see
    :data:`~autoheal_contracts.diagnosis.CONFIDENCE_HUMAN_REVIEW_THRESHOLD`
    and the invariants enforced by
    :class:`~autoheal_contracts.diagnosis.DiagnosisResult`.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendedAction(StrEnum):
    """The class of remediation a diagnosis proposes.

    These describe *categories* of response, never concrete commands. AutoHeal
    never executes arbitrary shell input.
    """

    RETRY_JOB = "retry_job"
    RERUN_TESTS = "rerun_tests"
    PIN_DEPENDENCY = "pin_dependency"
    UPDATE_DEPENDENCY = "update_dependency"
    FIX_SOURCE_CODE = "fix_source_code"
    FIX_CONFIGURATION = "fix_configuration"
    REVERT_COMMIT = "revert_commit"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    NO_ACTION = "no_action"


class ValidationScope(StrEnum):
    """What was actually re-run to produce a validation result."""

    TEST_SUITE = "test_suite"
    SINGLE_TEST = "single_test"
    LINT = "lint"
    BUILD = "build"
    SMOKE = "smoke"
    FULL_PIPELINE = "full_pipeline"


class IncidentStatus(StrEnum):
    """Lifecycle state of an incident.

    The happy path is::

        RECEIVED -> DIAGNOSED -> REMEDIATION_PROPOSED
                 -> REMEDIATION_APPROVED -> VALIDATING
                 -> VALIDATED -> RESOLVED -> CLOSED

    Every branch that needs a person diverges into ``AWAITING_REVIEW`` or
    ``ESCALATED``. ``ABANDONED`` is the only terminal state that may carry no
    validation result (for example a duplicate or a withdrawn report).

    The invariants between status and carried payloads are enforced by
    :class:`~autoheal_contracts.incident.IncidentRecord`.
    """

    RECEIVED = "received"
    DIAGNOSED = "diagnosed"
    AWAITING_REVIEW = "awaiting_review"
    REMEDIATION_PROPOSED = "remediation_proposed"
    REMEDIATION_APPROVED = "remediation_approved"
    VALIDATING = "validating"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ABANDONED = "abandoned"
