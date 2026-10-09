"""AutoHeal DevOps AI -- diagnosis module (Member 2).

Turns a validated :class:`~autoheal_contracts.FailureEvent` into a validated,
evidence-backed :class:`~autoheal_contracts.DiagnosisResult`. That is the whole
job.

Public interface
----------------
::

    from autoheal_diagnosis import diagnose
    from autoheal_contracts import FailureEvent

    result = diagnose(event)                  # rule-only, offline, no credentials
    result = diagnose(event, use_llm=True)    # permit the optional LLM layer

or, when you want a long-lived object::

    from autoheal_diagnosis import DiagnosisService

    service = DiagnosisService()
    result = service.diagnose(event)

What this module never does
---------------------------
execute a command, install a package, modify a repository, touch CI
configuration, read a secret, contact a provider that was not configured for
it, or claim that a repair has been verified. It produces a hypothesis and a
recommendation; verification belongs to the validation component.

Deterministic by default
------------------------
The rule engine is the first and authoritative layer. It needs nothing: no LLM,
no API key, no network. The optional provider layer is only consulted when the
rules were inconclusive or contradicted the reported failure type.
"""

from autoheal_contracts import FailureEvent

from autoheal_diagnosis.classifier import (
    RULES,
    Classification,
    Rule,
    Signal,
    classify_event,
    mismatch_classification,
    unknown_classification,
)
from autoheal_diagnosis.config import DiagnosisSettings
from autoheal_diagnosis.evidence import Evidence, verified_evidence
from autoheal_diagnosis.fixtures import (
    EXPECTED_ACTION,
    EXPECTED_CLASSIFIER_RULE,
    EXPECTED_FAILURE_TYPE,
    EXPECTED_HUMAN_REVIEW,
    FIXTURE_NAMES,
    FIXTURES,
    fixture_event,
    fixture_payload,
)
from autoheal_diagnosis.providers import (
    LLMProvider,
    LLMRequest,
    ProviderError,
    ProviderErrorKind,
    ProviderOutcome,
    ScriptedProvider,
    UnavailableProvider,
)
from autoheal_diagnosis.service import DiagnosisService, diagnose, unknown_result

__version__ = "0.1.0"

__all__ = [
    "EXPECTED_ACTION",
    "EXPECTED_CLASSIFIER_RULE",
    "EXPECTED_FAILURE_TYPE",
    "EXPECTED_HUMAN_REVIEW",
    "FIXTURES",
    "FIXTURE_NAMES",
    "RULES",
    "Classification",
    "DiagnosisService",
    "DiagnosisSettings",
    "Evidence",
    "FailureEvent",
    "LLMProvider",
    "LLMRequest",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderOutcome",
    "Rule",
    "ScriptedProvider",
    "Signal",
    "UnavailableProvider",
    "classify_event",
    "diagnose",
    "fixture_event",
    "fixture_payload",
    "mismatch_classification",
    "unknown_classification",
    "unknown_result",
    "verified_evidence",
]


def __dir__() -> list[str]:
    return sorted(__all__)
