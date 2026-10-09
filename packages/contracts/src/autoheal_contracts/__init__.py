"""AutoHeal DevOps AI.

This is the shared contracts package. It contains the typed, validated
Pydantic models that every AutoHeal module speaks:

* :class:`~autoheal_contracts.failure_event.FailureEvent` -- what the CI/CD
  platform reported (produced by the ingestion module).
* :class:`~autoheal_contracts.diagnosis.DiagnosisResult` -- what the AI
  diagnosis module believes went wrong, plus its evidence.
* :class:`~autoheal_contracts.validation.ValidationResult` -- what the
  validation module proved by re-running tests.
* :class:`~autoheal_contracts.incident.IncidentRecord` -- the aggregate that
  ties the previous three together with lifecycle state.

The package is intentionally free of framework, transport and LLM
dependencies. It depends only on Pydantic and the standard library.
"""

from autoheal_contracts.base import (
    CONTRACT_SCHEMA_VERSION,
    ContractModel,
    UTCDateTime,
    utcnow,
)
from autoheal_contracts.diagnosis import (
    CONFIDENCE_HUMAN_REVIEW_THRESHOLD,
    DiagnosisResult,
    EvidenceItem,
    EvidenceSource,
    EvidenceVerificationReport,
    verify_diagnosis_against_failure_event,
    verify_evidence_quotes,
)
from autoheal_contracts.enums import (
    FailureType,
    IncidentStatus,
    RecommendedAction,
    RiskLevel,
    ValidationScope,
)
from autoheal_contracts.failure_event import FailureEvent
from autoheal_contracts.incident import IncidentRecord, StatusTransition
from autoheal_contracts.validation import (
    TestFailureDetail,
    ValidationResult,
)

__version__ = "0.1.0"

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "CONFIDENCE_HUMAN_REVIEW_THRESHOLD",
    "ContractModel",
    "DiagnosisResult",
    "EvidenceItem",
    "EvidenceSource",
    "EvidenceVerificationReport",
    "FailureEvent",
    "FailureType",
    "IncidentRecord",
    "IncidentStatus",
    "RecommendedAction",
    "RiskLevel",
    "StatusTransition",
    "TestFailureDetail",
    "UTCDateTime",
    "ValidationResult",
    "ValidationScope",
    "__version__",
    "utcnow",
    "verify_diagnosis_against_failure_event",
    "verify_evidence_quotes",
]
