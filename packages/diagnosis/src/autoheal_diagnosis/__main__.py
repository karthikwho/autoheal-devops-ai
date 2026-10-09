"""Offline fixture runner and evaluation harness.

Usage
-----
::

    python -m autoheal_diagnosis                          # evaluation table
    python -m autoheal_diagnosis dependency_missing_module
    python -m autoheal_diagnosis some-event.json --json
    python -m autoheal_diagnosis --all --json

Needs no GitHub credentials, no LLM API key, no network and no shell. Each
fixture is validated against the shared ``FailureEvent`` contract, diagnosed,
and the result is re-validated against the same contract before it is printed.

Exit code ``0`` means every evaluated fixture matched its expectation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from autoheal_contracts import DiagnosisResult, FailureEvent

from autoheal_diagnosis.fixtures import (
    EXPECTED_ACTION,
    EXPECTED_CLASSIFIER_RULE,
    EXPECTED_FAILURE_TYPE,
    EXPECTED_HUMAN_REVIEW,
    FIXTURE_NAMES,
    FIXTURES,
    fixture_payload,
)
from autoheal_diagnosis.service import DiagnosisService

_COLUMNS = (
    "fixture",
    "expected type",
    "predicted type",
    "classifier rule",
    "action",
    "human review",
    "result",
)


def _load(target: str) -> tuple[str, FailureEvent]:
    """Resolve ``target`` to a named fixture or a JSON file path."""
    if target in FIXTURES:
        return target, FailureEvent.model_validate(fixture_payload(target))
    path = Path(target)
    if path.is_file():
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return path.stem, FailureEvent.model_validate(payload)
    raise SystemExit(f"unknown fixture or file: {target!r}")


def _run_one(name: str, event: FailureEvent, use_llm: bool) -> dict[str, Any]:
    """Diagnose one event and re-validate the result against the contract."""
    service = DiagnosisService()
    classification = service.explain(event, use_llm=use_llm)
    result = service.diagnose(event, use_llm=use_llm)
    # The contract's own context validator is the final check: it rejects a
    # fabricated quote, a misreported offset, a mismatched id or failure type.
    DiagnosisResult.model_validate(result.model_dump(), context={"failure_event": event})
    return {
        "name": name,
        "event": event,
        "classification": classification,
        "diagnosis": result,
    }


def _as_json(payload: dict[str, Any]) -> str:
    """Render one run as structured JSON -- no terminal parsing required."""
    classification = payload["classification"]
    diagnosis: DiagnosisResult = payload["diagnosis"]
    return json.dumps(
        {
            "fixture": payload["name"],
            "failure_event": payload["event"].model_dump(mode="json"),
            "classifier_rule_id": classification.rule_id,
            "diagnosis": diagnosis.model_dump(mode="json"),
            "evidence_verification": diagnosis.verify_evidence(payload["event"]).as_dict(),
        },
        indent=2,
        sort_keys=False,
    )


def _check(payload: dict[str, Any]) -> tuple[bool, str]:
    """Compare a run against the local expectation for its fixture."""
    diagnosis: DiagnosisResult = payload["diagnosis"]
    expected_type = EXPECTED_FAILURE_TYPE.get(payload["name"])
    expected_rule = EXPECTED_CLASSIFIER_RULE.get(payload["name"])
    expected_action = EXPECTED_ACTION.get(payload["name"])
    expected_review = EXPECTED_HUMAN_REVIEW.get(payload["name"])

    problems: list[str] = []
    if expected_type is not None and diagnosis.failure_type.value != expected_type:
        problems.append(f"type {diagnosis.failure_type.value} != {expected_type}")
    if expected_rule is not None and payload["classification"].rule_id != expected_rule:
        problems.append(f"rule {payload['classification'].rule_id} != {expected_rule}")
    if expected_action is not None and diagnosis.recommended_action.value != expected_action:
        problems.append(f"action {diagnosis.recommended_action.value} != {expected_action}")
    if expected_review is not None and diagnosis.requires_human_review is not expected_review:
        problems.append(f"review {diagnosis.requires_human_review} != {expected_review}")
    return (not problems), "; ".join(problems)


def _row(payload: dict[str, Any]) -> tuple[str, ...]:
    diagnosis: DiagnosisResult = payload["diagnosis"]
    ok, _ = _check(payload)
    return (
        payload["name"],
        EXPECTED_FAILURE_TYPE.get(payload["name"], "-"),
        diagnosis.failure_type.value,
        payload["classification"].rule_id,
        diagnosis.recommended_action.value,
        "yes" if diagnosis.requires_human_review else "no",
        "PASS" if ok else "FAIL",
    )


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(column), len(_COLUMNS[index])) for index, column in enumerate(_COLUMNS)]
    header = "  ".join(name.ljust(widths[i]) for i, name in enumerate(_COLUMNS))
    print(header)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def main(argv: list[str] | None = None) -> int:
    """Run the fixture set. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="autoheal_diagnosis",
        description=(
            "Diagnose local FailureEvent fixtures with the deterministic engine "
            "(offline, no credentials required)."
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="fixture names or paths to FailureEvent JSON files. Default: all fixtures.",
    )
    parser.add_argument(
        "--all", action="store_true", help="evaluate every bundled fixture (default)"
    )
    parser.add_argument(
        "--json", action="store_true", help="print full structured JSON instead of a table"
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="permit the optional LLM layer (requires a provider to be configured)",
    )
    parser.add_argument("--provider-label", default="cli", help="producer label for the run")
    args = parser.parse_args(argv)

    targets = list(args.targets) or list(FIXTURE_NAMES)
    runs: list[dict[str, Any]] = []
    for target in targets:
        try:
            name, event = _load(target)
        except Exception as exc:  # a bad fixture must fail loudly
            print(f"error: could not load {target!r}: {exc}", file=sys.stderr)
            return 2
        runs.append(_run_one(name, event, args.use_llm))

    if args.json:
        print("[" + ",\n".join(_as_json(run) for run in runs) + "]")
    elif len(runs) == 1 and not args.all:
        print(_as_json(runs[0]))
    else:
        _print_table([_row(run) for run in runs])
        failures = sum(0 if _check(run)[0] else 1 for run in runs)
        print(f"\n{len(runs) - failures}/{len(runs)} fixtures matched expectations")

    return 0 if all(_check(run)[0] for run in runs) else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
