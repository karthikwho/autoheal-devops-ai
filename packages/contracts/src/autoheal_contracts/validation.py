"""The validation contract: proof, not intention.

The single most important rule in AutoHeal lives here: **a proposed fix is
not a validated fix.** This module models only the second thing.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from autoheal_contracts.base import (
    CONTRACT_SCHEMA_VERSION,
    ContractModel,
    ShortText,
    UTCDateTime,
    assert_not_in_future,
)
from autoheal_contracts.enums import ValidationScope

__all__ = ["COULD_NOT_VALIDATE", "TestFailureDetail", "ValidationResult"]

#: Sentinel note recorded when a validation ran no tests at all.
COULD_NOT_VALIDATE = "no tests were executed, so no recovery could be proven"

Summary = Annotated[str, StringConstraints(min_length=10, max_length=2000)]


class TestFailureDetail(ContractModel):
    """One failing test, as reported by the test runner."""

    test_name: ShortText
    message: Annotated[str | None, StringConstraints(min_length=1, max_length=2000)] = None
    file_path: ShortText | None = None
    line_number: Annotated[int | None, Field(ge=1)] = None


class ValidationResult(ContractModel):
    """The outcome of re-running checks after a remediation attempt.

    Required fields
    ---------------
    ``passed``
        Whether the re-run succeeded. This is a statement about the *tests*,
        never about the quality of the diagnosis.
    ``tests_executed`` / ``tests_failed``
        Counters straight from the runner. ``tests_failed <= tests_executed``.
    ``validation_summary``
        A clear, human-readable statement of what happened.
    ``validated_at``
        When the checks ran. Timezone aware, normalised to UTC, not in the future.

    Enforced invariants
    -------------------
    * ``passed=true`` requires ``tests_executed >= 1`` and ``tests_failed == 0``.
      "No tests ran but it passed" is not a validation; accepting it would let
      a skipped suite masquerade as a green one.
    * ``tests_failed <= tests_executed``.
    * ``len(failure_details) <= tests_failed`` -- details cannot invent failures.

    What this model does *not* mean
    -------------------------------
    It does not mean the remediation was correct, minimal, or safe. It means:
    "these specific checks were re-executed after the change and this is what
    happened". Repair safety is governed by risk policy and human review.
    """

    schema_version: Literal[CONTRACT_SCHEMA_VERSION] = CONTRACT_SCHEMA_VERSION
    incident_id: ShortText
    passed: bool
    tests_executed: Annotated[int, Field(ge=0)]
    tests_failed: Annotated[int, Field(ge=0)]
    failure_details: Annotated[list[TestFailureDetail], Field(default_factory=list)]
    validation_summary: Summary

    validated_at: UTCDateTime
    validation_scope: ValidationScope = ValidationScope.TEST_SUITE
    command: ShortText | None = None
    environment: ShortText | None = None
    duration_seconds: Annotated[float | None, Field(ge=0.0)] = None
    #: Opaque reference to the remediation attempt this validation covers.
    remediation_reference: ShortText | None = None

    @model_validator(mode="after")
    def _failures_cannot_exceed_executions(self) -> ValidationResult:
        if self.tests_failed > self.tests_executed:
            raise ValueError(
                f"tests_failed ({self.tests_failed}) cannot exceed "
                f"tests_executed ({self.tests_executed})"
            )
        return self

    @model_validator(mode="after")
    def _pass_requires_real_test_evidence(self) -> ValidationResult:
        """A pass with no executed tests is not a validation."""
        if self.passed and self.tests_executed == 0:
            raise ValueError(
                "passed=true requires tests_executed >= 1: a suite that never ran "
                f"cannot be reported as a pass ({COULD_NOT_VALIDATE})"
            )
        if self.passed and self.tests_failed != 0:
            raise ValueError(f"passed=true requires tests_failed == 0, got {self.tests_failed}")
        return self

    @model_validator(mode="after")
    def _failure_details_cannot_invent_failures(self) -> ValidationResult:
        if len(self.failure_details) > self.tests_failed:
            raise ValueError(
                f"failure_details has {len(self.failure_details)} entries but "
                f"tests_failed is {self.tests_failed}"
            )
        return self

    @model_validator(mode="after")
    def _validated_at_not_in_future(self) -> ValidationResult:
        assert_not_in_future(self.validated_at, "validated_at")
        return self

    @property
    def is_recovery_verified(self) -> bool:
        """True only when a green run actually executed tests.

        This is the property the operator UI should display as "verified". It
        is deliberately narrow: executing one smoke test is still a weak claim,
        which is why :attr:`tests_executed` and :attr:`validation_scope` travel
        with the result.
        """
        return self.passed and self.tests_executed > 0 and self.tests_failed == 0

    @property
    def status_note(self) -> str:
        """A one-line statement of what this result does and does not prove."""
        if self.is_recovery_verified:
            return (
                f"{self.tests_executed} checks passed ({self.validation_scope.value}); "
                "recovery is verified for this scope only"
            )
        if self.tests_executed == 0:
            return COULD_NOT_VALIDATE
        return f"{self.tests_failed} of {self.tests_executed} checks failed; recovery NOT verified"
