"""Evidence extraction and verification.

Every quote this module emits is a **byte-for-byte substring** of the supplied
``FailureEvent.logs``. Nothing is paraphrased, summarised or invented: the shared
contract already rejects a ``failure_logs`` quote that is not present in the log,
so the cheapest way to stay honest is to only ever hand the contract a substring
that was cut out of the log on the spot.

The helpers here are deliberately small and pure. They never open a file, never
run a command and never touch the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autoheal_contracts import EvidenceSource
from autoheal_contracts.diagnosis import find_log_offset

__all__ = [
    "MAX_QUOTE_CHARS",
    "Evidence",
    "evidence_from_match",
    "fallback_evidence",
    "line_containing",
    "split_log_lines",
    "verified_evidence",
]

#: Mirrors the ``EvidenceItem.quote`` upper bound in the shared contract.
MAX_QUOTE_CHARS = 4000

#: Words that make a log line worth quoting when nothing else matched.
_HINT = re.compile(
    r"error|fail|exception|traceback|cannot|unable|denied|fatal|invalid|"
    r"not found|exit code",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One verifiable evidence item, before it becomes an ``EvidenceItem``.

    ``log_offset`` is filled in from the real position in the logs, so it can
    never disagree with the contract's offset cross-check.
    """

    source: EvidenceSource
    quote: str
    explanation: str
    log_offset: int | None = None


def split_log_lines(logs: str) -> list[str]:
    """Significant (non-blank) log lines, trimmed at the ends.

    Trimming only ever removes a prefix and a suffix, so each returned line is
    still a contiguous substring of ``logs`` and therefore quotable.
    """
    return [line.strip() for line in logs.splitlines() if line.strip()]


def line_containing(logs: str, index: int) -> str:
    """The trimmed line that occupies ``index`` in ``logs``."""
    start = logs.rfind("\n", 0, index) + 1
    end = logs.find("\n", index)
    if end == -1:
        end = len(logs)
    return logs[start:end].strip()


def _safe_quote(logs: str, candidate: str) -> str:
    """A quote that is guaranteed to satisfy the contract's length bounds."""
    text = candidate.strip()
    if len(text) >= 3:
        return text[:MAX_QUOTE_CHARS]
    # ``FailureEvent.logs`` is validated to carry >= 10 significant characters,
    # so this fallback is always long enough to be a legal quote.
    return logs.strip()[:MAX_QUOTE_CHARS]


def evidence_from_match(logs: str, match: re.Match[str], explanation: str) -> Evidence:
    """Build evidence from a regex match: the whole line it was found on."""
    quote = _safe_quote(logs, line_containing(logs, match.start()))
    return Evidence(
        source=EvidenceSource.FAILURE_LOGS,
        quote=quote,
        explanation=explanation,
        log_offset=find_log_offset(logs, quote),
    )


def verified_evidence(
    items: tuple[Evidence, ...] | list[Evidence],
    logs: str,
) -> tuple[tuple[Evidence, ...], tuple[Evidence, ...]]:
    """Split ``items`` into (present in logs) and (absent from logs).

    Used on untrusted input -- model output in particular -- so that a quote
    which cannot be traced back to the supplied logs is dropped instead of
    being reported as fact.
    """
    kept: list[Evidence] = []
    dropped: list[Evidence] = []
    for item in items:
        if find_log_offset(logs, item.quote) is not None:
            kept.append(item)
        else:
            dropped.append(item)
    return tuple(kept), tuple(dropped)


def fallback_quote(logs: str) -> str:
    """The most error-like line in ``logs``, chosen deterministically.

    Used only when no rule matched: the quote then carries no interpretive
    weight, it simply records which line the engine looked at.
    """
    lines = split_log_lines(logs)
    for line in reversed(lines):
        if len(line) >= 3 and _HINT.search(line):
            return line[:MAX_QUOTE_CHARS]
    for line in reversed(lines):
        if len(line) >= 3:
            return line[:MAX_QUOTE_CHARS]
    return logs.strip()[:MAX_QUOTE_CHARS]


def fallback_evidence(logs: str) -> Evidence:
    """The honest 'I could not classify this' evidence item."""
    quote = fallback_quote(logs)
    return Evidence(
        source=EvidenceSource.FAILURE_LOGS,
        quote=quote,
        explanation=(
            "The most error-like line in the supplied logs. It matches no failure "
            "signature the deterministic engine understands, so it supports only "
            "the statement that the failure is unclassified."
        ),
        log_offset=find_log_offset(logs, quote),
    )
