"""Deterministic, offline failure classification.

This is the **first and authoritative** layer of the diagnosis pipeline. It:

* needs no LLM, no API key, no network and no configuration;
* reads nothing but the :class:`~autoheal_contracts.FailureEvent` it is given;
* executes nothing, ever.

Rule precedence (documented because the patterns overlap)
--------------------------------------------------------
The rules are evaluated top to bottom and the first rule with a matching signal
wins. The order is chosen so that the *most specific, most fundamental* cause
is reported rather than the most visible symptom:

1. ``dependency-missing-module`` -- an import failed because a module was not
   installed. This is reported ahead of a test failure, because a missing module
   surfaces *through* pytest ("FAILED ... / ERROR collecting ...") while being a
   dependency problem, not a defect in the test.
2. ``dependency-resolution-failure`` -- the resolver produced no install set.
   Ahead of generic build failures for the same reason.
3. ``syntax-or-import`` -- the source could not be parsed or a name could not be
   imported. ``SyntaxError`` and ``ImportError`` outrank generic build errors
   because they name the file that did not compile.
4. ``test-failure`` -- an assertion mismatch or a runner-reported test failure.
5. ``build-failure`` -- the build tool itself failed.
6. ``configuration`` -- a configuration artefact could not be read.
7. Nothing matched -- ``unknown``.

``unknown`` is a first-class outcome. Evidence is never stretched to fit a
category, and a log that merely mentions a package name is *not* evidence of a
dependency problem: every pattern below requires an explicit failure signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoheal_contracts import (
    FailureEvent,
    FailureType,
    RecommendedAction,
    RiskLevel,
)

from autoheal_diagnosis.evidence import (
    Evidence,
    evidence_from_match,
    fallback_evidence,
)

__all__ = [
    "RULES",
    "Classification",
    "ExtraEvidence",
    "Rule",
    "Signal",
    "TYPE_MISMATCH_RULE_ID",
    "UNKNOWN_RULE_ID",
    "classify_event",
    "mismatch_classification",
    "unknown_classification",
]


@dataclass(frozen=True, slots=True)
class ExtraEvidence:
    """An additional, corroborating quote to cite when it is present."""

    pattern: re.Pattern[str]
    explanation: str


@dataclass(frozen=True, slots=True)
class Signal:
    """One concrete failure signature.

    ``probable_cause`` and ``explanation`` are templates. ``{match}`` expands to
    the matched text and ``{name}`` to the corresponding named group; any named
    group the pattern does not define renders as a neutral placeholder instead of
    raising, so adding a pattern can never break rendering.
    """

    pattern: re.Pattern[str]
    confidence: float
    probable_cause: str
    explanation: str
    extra: tuple[ExtraEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class Rule:
    """A failure category: where it maps to, and how it should be answered."""

    rule_id: str
    failure_type: FailureType
    recommended_action: RecommendedAction
    risk_level: RiskLevel
    signals: tuple[Signal, ...]
    #: Does acting on this diagnosis need a person? Defaults to ``True``: false
    #: escalation is preferable to unsafe automation.
    requires_human_review: bool = True
    #: Appended to the shared limitations text.
    limitations_note: str = ""


@dataclass(frozen=True, slots=True)
class Classification:
    """What the deterministic engine concluded, plus why."""

    failure_type: FailureType
    probable_cause: str
    confidence: float
    evidence: tuple[Evidence, ...]
    recommended_action: RecommendedAction
    risk_level: RiskLevel
    rule_id: str
    limitations: str
    requires_human_review: bool
    #: Populated for LLM-derived classifications, ``None`` for rule-derived ones.
    producer: str | None = None
    dropped_evidence: tuple[str, ...] = field(default=())


UNKNOWN_RULE_ID = "unknown-no-signature"
TYPE_MISMATCH_RULE_ID = "unknown-type-mismatch"

_DEPENDENCY_NOTE = (
    "The dependency manifest and lockfile were not inspected, so this diagnosis "
    "cannot say whether the package is declared there or which version is expected."
)
_BUILD_NOTE = (
    "The build tool's own configuration was not read, so it is not known whether "
    "the failure originates in project sources or in the build configuration."
)


def _clean(value: str) -> str:
    """Trim and unquote a captured group.

    Only a *matching* pair of surrounding quotes is removed, so an assertion
    message such as ``expected 'ok' got 'err'`` keeps its own inner quotes.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _render(template: str, match: re.Match[str]) -> str:
    """Expand ``{match}`` and ``{named-group}`` placeholders in ``template``."""
    placeholders = set(re.findall(r"\{(\w+)\}", template))
    values: dict[str, str] = {"match": _clean(match.group(0))[:200]}
    for name in placeholders:
        if name == "match":
            continue
        try:
            captured = match.group(name)
        except IndexError:  # the pattern does not define this group
            captured = None
        values[name] = _clean(captured)[:200] if captured else "the failing target"
    return template.format(**values)


# ---------------------------------------------------------------------------
# Rules, most specific first.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="dependency-missing-module",
        failure_type=FailureType.DEPENDENCY_RESOLUTION,
        recommended_action=RecommendedAction.PIN_DEPENDENCY,
        risk_level=RiskLevel.MEDIUM,
        limitations_note=_DEPENDENCY_NOTE,
        signals=(
            Signal(
                pattern=re.compile(
                    r"ModuleNotFoundError:\s*No module named\s+(?P<module>['\"]?[\w.\-]+['\"]?)"
                ),
                confidence=0.85,
                probable_cause=(
                    "The interpreter could not import {module} because the package that "
                    "provides it was not present in the environment this job ran in."
                ),
                explanation=(
                    "The log reports a missing module by name, which is a dependency "
                    "availability failure rather than a defect in the project's own code."
                ),
                extra=(
                    ExtraEvidence(
                        pattern=re.compile(r'File "[^"]+", line \d+, in <module>'),
                        explanation=(
                            "The traceback shows the entry point that triggered the failing import."
                        ),
                    ),
                ),
            ),
            Signal(
                pattern=re.compile(r"No module named\s+(?P<module>['\"]?[\w.\-]+['\"]?)"),
                confidence=0.8,
                probable_cause=(
                    "The job could not import {module}; the package providing it appears "
                    "not to be installed in the environment that ran the job."
                ),
                explanation=(
                    "The log names a module that could not be found, which points at "
                    "dependency availability rather than at project code."
                ),
                extra=(
                    ExtraEvidence(
                        pattern=re.compile(r'File "[^"]+", line \d+'),
                        explanation="The traceback locates where the failing import happened.",
                    ),
                ),
            ),
            Signal(
                pattern=re.compile(r"Cannot find module\s+(?P<module>['\"][\w./@\-]+['\"])"),
                confidence=0.75,
                probable_cause=(
                    "The runtime could not resolve {module}, so the package providing it "
                    "was not installed for this job."
                ),
                explanation=(
                    "The log reports an unresolvable module, which indicates a missing "
                    "or mis-declared package rather than a code defect."
                ),
            ),
            Signal(
                pattern=re.compile(r"(?:npm|yarn|pipenv)\s+ERR!.*?(?:code\s+)?(?:E404|ERESOLVE)"),
                confidence=0.75,
                probable_cause=(
                    "The package manager could not resolve a declared dependency from the "
                    "configured registry, so no consistent set of packages was installed."
                ),
                explanation=(
                    "The package manager reported a resolution failure, which means the "
                    "dependency declaration could not be satisfied as written."
                ),
            ),
        ),
    ),
    Rule(
        rule_id="dependency-resolution-failure",
        failure_type=FailureType.DEPENDENCY_RESOLUTION,
        recommended_action=RecommendedAction.PIN_DEPENDENCY,
        risk_level=RiskLevel.MEDIUM,
        limitations_note=_DEPENDENCY_NOTE,
        signals=(
            Signal(
                pattern=re.compile(
                    r"Could not find a version that satisfies the requirement\s+(?P<requirement>[\w.\-]+)"
                ),
                confidence=0.85,
                probable_cause=(
                    "The resolver could not satisfy the requirement {requirement} with any "
                    "version available from the configured index, so the environment was "
                    "never fully installed."
                ),
                explanation=(
                    "The log states that no available version satisfies a declared "
                    "requirement, which is a dependency resolution failure."
                ),
            ),
            Signal(
                pattern=re.compile(
                    r"No matching distribution found for\s+(?P<requirement>[\w.\-]+)"
                ),
                confidence=0.8,
                probable_cause=(
                    "No distribution for {requirement} was available from the configured "
                    "index, so the environment could not be completed."
                ),
                explanation=(
                    "The resolver found no candidate distribution for a declared "
                    "requirement, which points at the dependency declaration or index."
                ),
            ),
            Signal(
                pattern=re.compile(r"ResolutionImpossible|unable to resolve dependency"),
                confidence=0.7,
                probable_cause=(
                    "The dependency resolver reported that the declared requirements "
                    "cannot be satisfied together, so installation stopped before the "
                    "job could run."
                ),
                explanation=(
                    "The resolver explicitly reported an unsatisfiable requirement set, "
                    "which is a dependency resolution failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"(?:npm|yarn|pip)\s+ERR!"),
                confidence=0.65,
                probable_cause=(
                    "The package manager aborted before the install completed, so the job "
                    "ran against an incomplete environment."
                ),
                explanation=(
                    "The package manager reported an error, which means the environment "
                    "was not fully provisioned."
                ),
            ),
        ),
    ),
    Rule(
        rule_id="syntax-or-import",
        failure_type=FailureType.BUILD_COMPILATION,
        recommended_action=RecommendedAction.FIX_SOURCE_CODE,
        risk_level=RiskLevel.LOW,
        # Inspecting and fixing source in a branch is the lowest-risk path the
        # engine can propose, and the remediation stage still needs a human
        # approval before anything is executed.
        requires_human_review=False,
        signals=(
            Signal(
                pattern=re.compile(r"SyntaxError:\s*(?P<detail>[^\n]*)"),
                confidence=0.85,
                probable_cause=(
                    "A Python source file could not be parsed ({detail}), so the job failed "
                    "before any check could execute."
                ),
                explanation=(
                    "The log reports a syntax error, which means a source file could not be "
                    "compiled by the interpreter."
                ),
                extra=(
                    ExtraEvidence(
                        pattern=re.compile(r'File "[^"]+", line \d+'),
                        explanation="The message identifies the file and line that failed to parse.",
                    ),
                ),
            ),
            Signal(
                pattern=re.compile(r"IndentationError|TabError"),
                confidence=0.8,
                probable_cause=(
                    "A source file has an indentation problem, so the interpreter could not "
                    "compile it and the job stopped."
                ),
                explanation=(
                    "An indentation error is a source-level parse failure, reported before "
                    "the job's checks could run."
                ),
            ),
            Signal(
                pattern=re.compile(
                    r"ImportError:\s*cannot import name\s+(?P<name>['\"]?[\w.]+['\"]?)"
                ),
                confidence=0.8,
                probable_cause=(
                    "A module was found but does not export the name {name} that the code "
                    "asks for, so the import step failed."
                ),
                explanation=(
                    "The log names an import that could not be satisfied, which points at a "
                    "mismatch between the code and the module it imports."
                ),
                extra=(
                    ExtraEvidence(
                        pattern=re.compile(r'File "[^"]+", line \d+'),
                        explanation="The traceback locates the import that failed.",
                    ),
                ),
            ),
            Signal(
                pattern=re.compile(r"ImportError:\s*attempted relative import"),
                confidence=0.7,
                probable_cause=(
                    "A relative import was used where the module has no package context, so "
                    "the import could not be resolved."
                ),
                explanation=(
                    "The log reports an attempted relative import without a package, which "
                    "is a source-level import failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"failed to import\s+(?P<target>[^\n]*)"),
                confidence=0.65,
                probable_cause=(
                    "An import step reported failure for {target}, so the module could not be "
                    "loaded by the job."
                ),
                explanation=(
                    "The log explicitly reports a failed import, which blocks the job before "
                    "its checks can run."
                ),
            ),
        ),
    ),
    Rule(
        rule_id="test-failure",
        failure_type=FailureType.TEST_FAILURE,
        recommended_action=RecommendedAction.FIX_SOURCE_CODE,
        risk_level=RiskLevel.LOW,
        # Same reasoning as the syntax rule: reproducing a failing test and
        # inspecting the code under it is low risk, and the remediation stage
        # still requires explicit human approval before execution.
        requires_human_review=False,
        signals=(
            Signal(
                pattern=re.compile(r"AssertionError:\s*(?P<detail>[^\n]*)"),
                confidence=0.8,
                probable_cause=(
                    "A test assertion failed: {detail}. The observed value differed from the "
                    "value the test expected."
                ),
                explanation=(
                    "The log reports a failed assertion, which means a check ran and "
                    "disagreed with the code under test."
                ),
                extra=(
                    ExtraEvidence(
                        pattern=re.compile(r"FAILED\s+[\w./\-]+"),
                        explanation="The runner names the specific test that failed.",
                    ),
                ),
            ),
            Signal(
                pattern=re.compile(r"(?:^|\s)AssertionError\b"),
                confidence=0.75,
                probable_cause=(
                    "A check raised an assertion error, so the observed behaviour differed "
                    "from the expected behaviour."
                ),
                explanation=(
                    "An assertion error is reported, which indicates a test expectation was "
                    "not met."
                ),
            ),
            Signal(
                pattern=re.compile(r"FAILED\s+(?P<test>[\w./\-]+\.py(?:::[^\s]+)?)"),
                confidence=0.8,
                probable_cause=(
                    "The test runner reported a failure for {test}; the checks ran and at "
                    "least one of them did not pass."
                ),
                explanation=(
                    "The runner explicitly lists a failed test, so a test expectation was not met."
                ),
            ),
            Signal(
                pattern=re.compile(r"FAIL:\s+(?P<test>[\w./\-]+)"),
                confidence=0.7,
                probable_cause=(
                    "The unittest runner reported a failure for {test}, so at least one test "
                    "expectation was not met."
                ),
                explanation=(
                    "The unittest stderr format reports a failing test by name, which is a "
                    "test failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"=+ FAILURES =+"),
                confidence=0.65,
                probable_cause=(
                    "pytest reached its failure section, so at least one collected test did "
                    "not pass."
                ),
                explanation=(
                    "The pytest failure header is present, which means the suite ran and "
                    "produced failures."
                ),
            ),
            Signal(
                pattern=re.compile(r"(?:ERROR collecting|errors? during collection)"),
                confidence=0.6,
                probable_cause=(
                    "The test runner could not collect all modules, so part of the suite "
                    "never executed."
                ),
                explanation=(
                    "A collection error is reported by the test framework itself, so the "
                    "failure is in the suite's own execution."
                ),
            ),
            Signal(
                pattern=re.compile(r"\b[1-9]\d* failed\b"),
                confidence=0.55,
                probable_cause=(
                    "The run summary reports at least one failed test, so the suite did not pass."
                ),
                explanation=(
                    "The final summary line counts failures, which is direct evidence that "
                    "checks did not pass."
                ),
            ),
        ),
    ),
    Rule(
        rule_id="build-failure",
        failure_type=FailureType.BUILD_COMPILATION,
        recommended_action=RecommendedAction.FIX_SOURCE_CODE,
        risk_level=RiskLevel.MEDIUM,
        limitations_note=_BUILD_NOTE,
        signals=(
            Signal(
                pattern=re.compile(r"make(?:\[\d+\])?:\s*\*\*\* [^\n]*Error \d+"),
                confidence=0.85,
                probable_cause=(
                    "The build stopped because make reported an error while executing a "
                    "target, so no artifact was produced."
                ),
                explanation=(
                    "make reported a non-zero exit for a target, which means the build step "
                    "itself failed."
                ),
            ),
            Signal(
                pattern=re.compile(r"BUILD FAILED"),
                confidence=0.8,
                probable_cause=(
                    "The build tool reported a failed build, so the job stopped before "
                    "producing its artifact."
                ),
                explanation=(
                    "An explicit 'BUILD FAILED' marker is present, which is a build-tool failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"\berror:\s*failed to compile\b", re.IGNORECASE),
                confidence=0.75,
                probable_cause=(
                    "A compilation step failed, so the job could not produce the artifact "
                    "the next step expects."
                ),
                explanation=(
                    "The log reports a compilation failure, which stops the build before "
                    "later steps run."
                ),
            ),
            Signal(
                pattern=re.compile(r"compilation (?:terminated|failed)", re.IGNORECASE),
                confidence=0.75,
                probable_cause=(
                    "Compilation did not complete, so the build stopped before producing an "
                    "artifact."
                ),
                explanation=(
                    "The log reports that compilation terminated abnormally, which is a "
                    "build failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"FAILED to (?:build|compile)", re.IGNORECASE),
                confidence=0.7,
                probable_cause=(
                    "A build or compile step reported failure, so the job did not reach the "
                    "checks that follow it."
                ),
                explanation=("The log states that a build step failed, which is a build failure."),
            ),
            Signal(
                pattern=re.compile(r"(?:npm|yarn|pip|poetry|bundle|cargo|go)\s+ERR!"),
                confidence=0.6,
                probable_cause=(
                    "The build tool reported an error, so the job stopped before the checks "
                    "could run."
                ),
                explanation=("A build tool reported an error, which is a build-tool failure."),
            ),
        ),
    ),
    Rule(
        rule_id="configuration",
        failure_type=FailureType.CONFIGURATION,
        recommended_action=RecommendedAction.FIX_CONFIGURATION,
        risk_level=RiskLevel.MEDIUM,
        limitations_note=(
            "The configuration artefacts themselves were not read, so the diagnosis cannot "
            "point at the exact key or line that is wrong."
        ),
        signals=(
            Signal(
                pattern=re.compile(r"invalid configuration", re.IGNORECASE),
                confidence=0.85,
                probable_cause=(
                    "A configuration artefact was rejected as invalid, so the job could not "
                    "start with the settings it was given."
                ),
                explanation=(
                    "The log reports invalid configuration, which is a configuration failure "
                    "rather than a code or test failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"configuration error", re.IGNORECASE),
                confidence=0.8,
                probable_cause=(
                    "A configuration error stopped the job before it could do useful work."
                ),
                explanation=(
                    "The log reports a configuration error, which places the failure in the "
                    "job's configuration."
                ),
            ),
            Signal(
                pattern=re.compile(
                    r"YAMLError|yaml\.scanner\.ScannerError|yaml\.parser\.ParserError"
                ),
                confidence=0.75,
                probable_cause=(
                    "A YAML artefact could not be parsed, so the settings it carries were "
                    "never applied."
                ),
                explanation=(
                    "A YAML parser error is reported, which is a configuration parse failure."
                ),
            ),
            Signal(
                pattern=re.compile(
                    r"(?:could not|cannot|failed to)\s+(?:parse|load|read)\s+"
                    r"(?:the\s+)?(?:config|configuration)",
                    re.IGNORECASE,
                ),
                confidence=0.7,
                probable_cause=(
                    "The job could not load its configuration, so it stopped before executing."
                ),
                explanation=(
                    "The log states that configuration could not be loaded, which is a "
                    "configuration failure."
                ),
            ),
            Signal(
                pattern=re.compile(
                    r"(?:workflow file[^\n]*(?:invalid|error)|invalid[^\n]*workflow file)",
                    re.IGNORECASE,
                ),
                confidence=0.7,
                probable_cause=(
                    "The workflow definition was reported as invalid, so the job never "
                    "started as intended."
                ),
                explanation=(
                    "The log reports the workflow file itself as invalid, which is a "
                    "pipeline configuration failure."
                ),
            ),
            Signal(
                pattern=re.compile(r"Error:\s*Invalid\s+\S+\s+value", re.IGNORECASE),
                confidence=0.65,
                probable_cause=(
                    "A configuration value was rejected as invalid, so the job stopped "
                    "before running its steps."
                ),
                explanation=(
                    "The log reports an invalid configuration value, which is a "
                    "configuration failure."
                ),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Classification entry points
# ---------------------------------------------------------------------------


def _collect_evidence(logs: str, signal: Signal, match: re.Match[str]) -> tuple[Evidence, ...]:
    """The matching line, plus any corroborating lines the signal names."""
    items = [evidence_from_match(logs, match, _render(signal.explanation, match))]
    seen = {items[0].quote}
    for extra in signal.extra:
        found = extra.pattern.search(logs)
        if found is None:
            continue
        item = evidence_from_match(logs, found, extra.explanation)
        if item.quote not in seen:
            seen.add(item.quote)
            items.append(item)
    return tuple(items)


def classify_event(event: FailureEvent) -> Classification:
    """Classify ``event`` using the deterministic rules only.

    Returns a classification in one of three shapes:

    * a supported failure type that agrees with the event;
    * a supported failure type that disagrees with the event (use
      :func:`mismatch_classification` to reconcile it);
    * ``unknown`` when no signature matched.
    """
    for rule in RULES:
        for signal in rule.signals:
            match = signal.pattern.search(event.logs)
            if match is None:
                continue
            evidence = _collect_evidence(event.logs, signal, match)
            review = rule.requires_human_review or rule.risk_level in {
                RiskLevel.HIGH,
                RiskLevel.CRITICAL,
            }
            return Classification(
                failure_type=rule.failure_type,
                probable_cause=_render(signal.probable_cause, match),
                confidence=signal.confidence,
                evidence=evidence,
                recommended_action=rule.recommended_action,
                risk_level=rule.risk_level,
                rule_id=rule.rule_id,
                limitations=_limitations(rule.limitations_note),
                requires_human_review=review,
            )
    return unknown_classification(event, reason="no recognised failure signature")


def _limitations(note: str = "") -> str:
    """The standing limitations text, plus an optional rule-specific note."""
    base = (
        "This is a hypothesis derived only from the supplied FailureEvent, not a verified "
        "fact. The logs may be truncated or partial, confidence is a heuristic rather than "
        "a probability, and nothing here proves that a repair works -- only the validation "
        "component can establish that."
    )
    return f"{base} {note}".strip()


def _label(value: FailureType) -> str:
    """A FailureType rendered for human-readable text."""
    return value.value if hasattr(value, "value") else str(value)


def unknown_classification(event: FailureEvent, reason: str = "") -> Classification:
    """The honest 'I cannot tell' result. Never a guess dressed up as one."""
    return Classification(
        failure_type=FailureType.UNKNOWN,
        probable_cause=(
            f"The supplied logs do not contain enough information to identify a probable "
            f"cause for the failed step '{event.failed_step}'"
            + (f" ({reason})" if reason else "")
            + "."
        ),
        confidence=0.0,
        evidence=(fallback_evidence(event.logs),),
        recommended_action=RecommendedAction.ESCALATE_TO_HUMAN,
        risk_level=RiskLevel.MEDIUM,
        rule_id=UNKNOWN_RULE_ID,
        limitations=_limitations(
            "No failure signature the deterministic engine understands was found, so any "
            "category chosen here would be a guess rather than a diagnosis."
        ),
        requires_human_review=True,
    )


def mismatch_classification(event: FailureEvent, classification: Classification) -> Classification:
    """Reconcile a classification that disagrees with the reported failure type.

    The shared contract requires ``DiagnosisResult.failure_type`` to equal the
    originating ``FailureEvent.failure_type``, so this module may not relabel the
    event. Instead it keeps the reported type, states the disagreement plainly,
    drops confidence and routes the incident to a human.
    """
    return Classification(
        failure_type=event.failure_type,
        probable_cause=(
            f"The event reports this failure as '{_label(event.failure_type)}', but the "
            f"supplied logs indicate '{_label(classification.failure_type)}'. The two "
            f"disagree, so the reported classification cannot be trusted without a human "
            f"reading the full log."
        ),
        confidence=min(classification.confidence, 0.4),
        evidence=classification.evidence,
        recommended_action=RecommendedAction.ESCALATE_TO_HUMAN,
        risk_level=RiskLevel.MEDIUM,
        rule_id=TYPE_MISMATCH_RULE_ID,
        limitations=_limitations(
            "The failure type reported by ingestion and the failure type implied by the "
            "logs disagree. This module is not allowed to relabel the event, so the "
            "diagnosis is routed to a human instead."
        ),
        requires_human_review=True,
    )
