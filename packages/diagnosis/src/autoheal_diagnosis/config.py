"""Runtime configuration for the diagnosis module.

Deliberately dependency-light, exactly like ``autoheal_api.config``: a frozen
dataclass populated from environment variables. No ``pydantic-settings``, no
secrets, no external service.

The rule-based engine needs **none** of these values. Every setting below only
affects the *optional* LLM layer, which is disabled by default and which never
has to be configured for the module to work.

No secret is ever read here. Provider credentials belong to a concrete provider
adapter, which this package does not ship.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "ENV_LLM_ENABLED",
    "ENV_LLM_MAX_EVIDENCE",
    "ENV_LLM_MAX_LOG_CHARS",
    "ENV_LLM_MAX_RESPONSE_CHARS",
    "ENV_LLM_PROVIDER",
    "ENV_LLM_TIMEOUT_SECONDS",
    "ENV_STRONG_RULE_CONFIDENCE",
    "DiagnosisSettings",
]

ENV_LLM_ENABLED = "AUTOHEAL_DIAGNOSIS_LLM_ENABLED"
ENV_LLM_PROVIDER = "AUTOHEAL_DIAGNOSIS_LLM_PROVIDER"
ENV_LLM_TIMEOUT_SECONDS = "AUTOHEAL_DIAGNOSIS_LLM_TIMEOUT_SECONDS"
ENV_LLM_MAX_LOG_CHARS = "AUTOHEAL_DIAGNOSIS_LLM_MAX_LOG_CHARS"
ENV_LLM_MAX_RESPONSE_CHARS = "AUTOHEAL_DIAGNOSIS_LLM_MAX_RESPONSE_CHARS"
ENV_LLM_MAX_EVIDENCE = "AUTOHEAL_DIAGNOSIS_LLM_MAX_EVIDENCE"
ENV_STRONG_RULE_CONFIDENCE = "AUTOHEAL_DIAGNOSIS_STRONG_RULE_CONFIDENCE"

#: How much of the log is copied into a prompt. Bounded so an oversized log
#: cannot turn into an oversized request. Evidence quotes are always taken from
#: the *full* log, never from this truncated copy.
DEFAULT_LLM_MAX_LOG_CHARS = 20_000

#: A response larger than this is refused rather than parsed. Bounded so a
#: runaway provider cannot exhaust memory.
DEFAULT_LLM_MAX_RESPONSE_CHARS = 20_000

#: Hard ceiling on how long a provider may take. A hung provider must never
#: hang the diagnosis.
DEFAULT_LLM_TIMEOUT_SECONDS = 20.0

#: At or above this rule confidence the deterministic answer is trusted and the
#: optional provider is not consulted.
DEFAULT_STRONG_RULE_CONFIDENCE = 0.8


@dataclass(frozen=True, slots=True)
class DiagnosisSettings:
    """Everything the diagnosis service needs.

    Attributes
    ----------
    llm_enabled:
        ``False`` by default. When ``False`` the deterministic engine is the
        only path and no provider is ever contacted.
    llm_provider:
        Free-text label for the configured provider. Recorded in
        ``DiagnosisResult.diagnosis_producer``; never a secret.
    llm_timeout_seconds:
        Wall-clock budget for one provider call.
    llm_max_log_chars / llm_max_response_chars:
        Input and output size bounds for provider calls.
    llm_max_evidence:
        How many evidence items a provider response may carry.
    strong_rule_confidence:
        Rule confidence at or above which the deterministic result is
        considered conclusive and the provider is skipped.
    """

    llm_enabled: bool = False
    llm_provider: str = "unconfigured"
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_log_chars: int = DEFAULT_LLM_MAX_LOG_CHARS
    llm_max_response_chars: int = DEFAULT_LLM_MAX_RESPONSE_CHARS
    llm_max_evidence: int = 8
    strong_rule_confidence: float = DEFAULT_STRONG_RULE_CONFIDENCE
    producer: str = "autoheal-diagnosis-rules@0.1.0"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DiagnosisSettings:
        """Build settings from ``env`` (defaults to ``os.environ``).

        Raises ``ValueError`` on a malformed value, mirroring
        :meth:`autoheal_api.config.Settings.from_env` -- a bad setting must fail
        loudly rather than silently disabling a safety bound.
        """
        source = os.environ if env is None else env

        def text(key: str, default: str) -> str:
            value = source.get(key, "").strip()
            return value or default

        def flag(key: str, default: bool) -> bool:
            raw = source.get(key, "").strip().lower()
            if not raw:
                return default
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{key} must be a boolean, got {raw!r}")

        def number(key: str, default: float, minimum: float, maximum: float) -> float:
            raw = source.get(key, "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be a number, got {raw!r}") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}, got {value}")
            return value

        def integer(key: str, default: int, minimum: int, maximum: int) -> int:
            raw = source.get(key, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer, got {raw!r}") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}, got {value}")
            return value

        return cls(
            llm_enabled=flag(ENV_LLM_ENABLED, False),
            llm_provider=text(ENV_LLM_PROVIDER, "unconfigured"),
            llm_timeout_seconds=number(
                ENV_LLM_TIMEOUT_SECONDS, DEFAULT_LLM_TIMEOUT_SECONDS, 0.1, 600.0
            ),
            llm_max_log_chars=integer(
                ENV_LLM_MAX_LOG_CHARS, DEFAULT_LLM_MAX_LOG_CHARS, 1_000, 500_000
            ),
            llm_max_response_chars=integer(
                ENV_LLM_MAX_RESPONSE_CHARS, DEFAULT_LLM_MAX_RESPONSE_CHARS, 100, 1_000_000
            ),
            llm_max_evidence=integer(ENV_LLM_MAX_EVIDENCE, 8, 1, 32),
            strong_rule_confidence=number(
                ENV_STRONG_RULE_CONFIDENCE, DEFAULT_STRONG_RULE_CONFIDENCE, 0.0, 1.0
            ),
        )
