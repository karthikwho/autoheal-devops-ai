"""Diagnosis orchestration: ``FailureEvent`` in, ``DiagnosisResult`` out.

The pipeline is:

1. Run the **deterministic** rule engine. It is offline, needs no credentials
   and always produces an answer.
2. If that answer is conclusive, use it. The optional LLM is consulted only
   when the rules were inconclusive (``unknown``) or contradicted the reported
   failure type.
3. Reconcile whatever was produced with the originating event: the contract
   forbids relabelling the event, so a disagreement becomes an escalation
   rather than an override.
4. Build the payload and validate it **with the event in the validation
   context**, so a fabricated quote or an incoherent field raises here, in
   development, instead of reaching a downstream consumer.

This module never executes anything. It produces a recommendation; acting on it
is somebody else's job, behind a human approval gate.
"""

from __future__ import annotations

from autoheal_contracts import (
    CONFIDENCE_HUMAN_REVIEW_THRESHOLD,
    DiagnosisResult,
    FailureEvent,
    FailureType,
    RecommendedAction,
    RiskLevel,
    utcnow,
)
from autoheal_contracts.diagnosis import find_log_offset

from autoheal_diagnosis.classifier import (
    UNKNOWN_RULE_ID,
    Classification,
    classify_event,
    mismatch_classification,
    unknown_classification,
)
from autoheal_diagnosis.config import DiagnosisSettings
from autoheal_diagnosis.evidence import Evidence, verified_evidence
from autoheal_diagnosis.providers import (
    LLMProvider,
    ProviderError,
    ProviderOutcome,
    build_request,
    parse_response,
    run_with_timeout,
)

__all__ = ["DiagnosisService", "diagnose"]

_FAILURE_LOGS = "failure_logs"


class DiagnosisService:
    """The public entry point Member 3 calls.

    Parameters
    ----------
    settings:
        Overrides the environment. ``None`` reads the environment once at
        construction time.
    provider:
        An optional provider-neutral transport. ``None`` means rule-only mode,
        which is the default and needs no credentials and no network.
    """

    def __init__(
        self,
        settings: DiagnosisSettings | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self._settings = settings if settings is not None else DiagnosisSettings.from_env()
        self._provider = provider

    @property
    def settings(self) -> DiagnosisSettings:
        """The settings this service was built with."""
        return self._settings

    def diagnose(self, event: FailureEvent, *, use_llm: bool | None = None) -> DiagnosisResult:
        """Diagnose ``event`` and return a validated ``DiagnosisResult``.

        Never raises for a provider failure: the deterministic result, or an
        ``unknown`` result that routes to a human, is returned instead.
        """
        return self._build(event, self.explain(event, use_llm=use_llm))

    def explain(self, event: FailureEvent, *, use_llm: bool | None = None) -> Classification:
        """The reconciled classification this service would use for ``event``.

        Useful for tests, debugging and the fixture harness: it reports the rule
        that actually produced the answer, after reconciliation with the event's
        reported failure type.
        """
        classification = classify_event(event)
        if self._llm_allowed(use_llm) and self._rules_are_inconclusive(event, classification):
            enhanced = self._consult_provider(event)
            if enhanced is not None:
                classification = enhanced
        return self._reconcile(event, classification)

    # ------------------------------------------------------------------
    # Optional LLM layer
    # ------------------------------------------------------------------

    def _llm_allowed(self, use_llm: bool | None) -> bool:
        """Whether the optional provider may be consulted at all."""
        if use_llm is None:
            return self._settings.llm_enabled and self._provider is not None
        return bool(use_llm) and self._provider is not None

    def _rules_are_inconclusive(self, event: FailureEvent, classification: Classification) -> bool:
        """True when the deterministic answer is not strong enough to stand alone.

        A rule-derived classification that agrees with the reported failure type
        and clears ``strong_rule_confidence`` is trusted as-is. Anything else --
        no signature at all, or a signature that contradicts the event -- is a
        case where more context could genuinely help.
        """
        if classification.rule_id == UNKNOWN_RULE_ID:
            return True
        if classification.failure_type != event.failure_type:
            return True
        return classification.confidence < self._settings.strong_rule_confidence

    def _consult_provider(self, event: FailureEvent) -> Classification | None:
        """Ask the provider, validate everything, and return ``None`` on failure.

        ``None`` means "the provider did not help"; the caller keeps the
        deterministic classification it already has. A provider can never make
        the diagnosis worse than the rules alone.
        """
        provider = self._provider
        if provider is None:
            return None
        request = build_request(event, self._settings)
        try:
            raw = run_with_timeout(provider, request)
        except ProviderError:
            return None
        except Exception:  # a provider must never break the caller
            return None

        outcome = parse_response(raw, self._settings.llm_max_response_chars)
        if not outcome.ok:
            return None
        return self._classification_from_payload(event, outcome)

    def _classification_from_payload(
        self, event: FailureEvent, outcome: ProviderOutcome
    ) -> Classification | None:
        """Convert a validated model payload into a :class:`Classification`.

        Every check below is a safety gate. A payload that fails one is
        discarded and the deterministic result stands.
        """
        payload = outcome.payload
        if payload is None:
            return None

        # The contract forbids relabelling the event, so a model that changes
        # the reported failure type is not merely wrong, it is unusable.
        if payload.failure_type != event.failure_type:
            return None

        # Only log-sourced quotes can be traced back to the input we actually
        # hold, so a quote from any other artefact is dropped as unverifiable.
        candidates = tuple(
            Evidence(
                source=item.source,
                quote=item.quote,
                explanation=item.explanation,
                # The offset is recomputed here, never taken from the model: a
                # misreported offset is impossible by construction.
                log_offset=find_log_offset(event.logs, item.quote),
            )
            for item in payload.evidence[: self._settings.llm_max_evidence]
            if str(item.source) == _FAILURE_LOGS
        )
        kept, dropped = verified_evidence(candidates, event.logs)
        if not kept:
            return None

        note = (
            "A model-produced hypothesis is never allowed to proceed without a "
            "human, whatever its confidence."
        )
        if dropped:
            note += (
                f" {len(dropped)} evidence item(s) supplied by the model were dropped "
                "because their quotes are not present in the supplied failure logs."
            )
        return Classification(
            failure_type=payload.failure_type,
            probable_cause=payload.probable_cause,
            confidence=round(min(1.0, max(0.0, payload.confidence)), 2),
            evidence=kept,
            recommended_action=payload.recommended_action,
            risk_level=payload.risk_level,
            rule_id="llm-provider",
            limitations=f"{payload.limitations} {note}".strip(),
            # A model answer is a hypothesis about a hypothesis. It always
            # routes to a human.
            requires_human_review=True,
            producer=f"autoheal-diagnosis/llm:{self._settings.llm_provider}",
        )

    # ------------------------------------------------------------------
    # Reconciliation and payload construction
    # ------------------------------------------------------------------

    def _reconcile(self, event: FailureEvent, classification: Classification) -> Classification:
        """Make the classification coherent with the originating event."""
        if classification.failure_type is FailureType.UNKNOWN:
            return classification
        if classification.failure_type != event.failure_type:
            return mismatch_classification(event, classification)
        return classification

    def _build(self, event: FailureEvent, classification: Classification) -> DiagnosisResult:
        """Assemble and validate the ``DiagnosisResult``."""
        return _assemble(event, classification, self._settings.producer)


def _assemble(
    event: FailureEvent, classification: Classification, producer: str
) -> DiagnosisResult:
    """Build and validate the ``DiagnosisResult`` for a reconciled classification."""
    confidence = round(min(1.0, max(0.0, classification.confidence)), 2)
    payload = {
        "schema_version": "1.0",
        "incident_id": event.incident_id,
        "failure_type": classification.failure_type,
        "probable_cause": classification.probable_cause,
        "evidence": [
            {
                "source": item.source,
                "quote": item.quote,
                "explanation": item.explanation,
                "log_offset": item.log_offset,
            }
            for item in classification.evidence
        ],
        "confidence": confidence,
        "recommended_action": classification.recommended_action,
        "risk_level": classification.risk_level,
        "requires_human_review": _needs_human_review(classification, confidence),
        "limitations": classification.limitations,
        "diagnosed_at": utcnow(),
        "diagnosis_producer": classification.producer or producer,
    }
    # Validating with the event in the context is the last line of defence:
    # the contract rejects a quote that is not in the logs, a mismatched id
    # or failure type, and a diagnosis that predates the failure.
    return DiagnosisResult.model_validate(payload, context={"failure_event": event})


def _needs_human_review(classification: Classification, confidence: float) -> bool:
    """The same conditions the contract enforces, computed before validation.

    Computing them here means ``model_validate`` can never fail on an invariant
    we could have honoured in the first place.
    """
    return bool(
        classification.requires_human_review
        or classification.failure_type is FailureType.UNKNOWN
        or classification.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
        or classification.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        or confidence < CONFIDENCE_HUMAN_REVIEW_THRESHOLD
    )


def diagnose(
    event: FailureEvent,
    use_llm: bool = False,
    *,
    settings: DiagnosisSettings | None = None,
    provider: LLMProvider | None = None,
) -> DiagnosisResult:
    """Convenience wrapper around :meth:`DiagnosisService.diagnose`.

    ``use_llm`` defaults to ``False``: rule-only mode needs no credentials, no
    network and no configuration, and is the recommended default.
    """
    service = DiagnosisService(settings=settings, provider=provider)
    return service.diagnose(event, use_llm=use_llm)


def unknown_result(event: FailureEvent, *, reason: str = "") -> DiagnosisResult:
    """The safe fallback: an ``unknown`` diagnosis that routes to a human."""
    classification = unknown_classification(event, reason)
    return _assemble(event, classification, DiagnosisSettings().producer)
