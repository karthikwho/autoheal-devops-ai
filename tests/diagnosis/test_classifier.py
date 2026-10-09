"""Deterministic classifier tests: category coverage and rule precedence.

The engine is offline and reads only the ``FailureEvent`` it is handed, so every
test here is pure: log text in, classification out.
"""

from __future__ import annotations

import pytest
from autoheal_contracts import FailureType, RecommendedAction, RiskLevel

pytestmark = pytest.mark.diagnosis

# (label, logs, expected FailureType, expected rule id)
CASES: list[tuple[str, str, FailureType, str]] = [
    # -- dependency -------------------------------------------------------
    (
        "ModuleNotFoundError with a module name",
        "Traceback (most recent call last):\n"
        '  File "app.py", line 1, in <module>\n'
        "    import requests\n"
        "ModuleNotFoundError: No module named 'requests'\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-missing-module",
    ),
    (
        "no module named, full stop",
        "ImportError: No module named really_missing\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-missing-module",
    ),
    (
        "node cannot find module",
        "> build\nError: Cannot find module 'express'\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-missing-module",
    ),
    (
        "pip cannot satisfy a requirement",
        "ERROR: Could not find a version that satisfies the requirement numpy>=99\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-resolution-failure",
    ),
    (
        "no matching distribution",
        "ERROR: No matching distribution found for left-pad\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-resolution-failure",
    ),
    (
        "npm resolution failure",
        "npm ERR! code ERESOLVE\nnpm ERR! ERESOLVE unable to resolve dependency tree\n",
        FailureType.DEPENDENCY_RESOLUTION,
        "dependency-missing-module",
    ),
    # -- syntax / import --------------------------------------------------
    (
        "SyntaxError invalid syntax",
        '  File "app.py", line 3\n    return json.loads(payload))\nSyntaxError: invalid syntax\n',
        FailureType.BUILD_COMPILATION,
        "syntax-or-import",
    ),
    (
        "IndentationError",
        '  File "app.py", line 12\n    x = 1\nIndentationError: unexpected indent\n',
        FailureType.BUILD_COMPILATION,
        "syntax-or-import",
    ),
    (
        "ImportError cannot import name",
        "E   ImportError: cannot import name 'build_url' from 'app.urls'\n",
        FailureType.BUILD_COMPILATION,
        "syntax-or-import",
    ),
    (
        "attempted relative import",
        "ImportError: attempted relative import with no known parent package\n",
        FailureType.BUILD_COMPILATION,
        "syntax-or-import",
    ),
    # -- test failure -----------------------------------------------------
    (
        "AssertionError with detail",
        "FAILED tests/test_cart.py::test_total - AssertionError: expected 2, got 3\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "bare AssertionError",
        "E   AssertionError\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "pytest FAILED line",
        "FAILED tests/test_app.py::test_login_timeout\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "unittest FAIL line",
        "FAIL: test_edge_cases (test_widget.WidgetTests)\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "pytest FAILURES header",
        "=========================== FAILURES ===========================\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "collection error",
        "ERROR collecting tests/test_imports.py\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    (
        "summary line only",
        "============================== 1 failed, 41 passed in 12.31s ===================\n",
        FailureType.TEST_FAILURE,
        "test-failure",
    ),
    # -- build / configuration -------------------------------------------
    (
        "make error",
        "src/app.c:12:5: error: 'undefined_symbol' undeclared\nmake: *** [Makefile:6: all] Error 1\n",
        FailureType.BUILD_COMPILATION,
        "build-failure",
    ),
    (
        "BUILD FAILED",
        "> Task :compileJava FAILED\nBUILD FAILED in 4s\n",
        FailureType.BUILD_COMPILATION,
        "build-failure",
    ),
    (
        "failed to compile",
        "error: failed to compile src/lib.rs\n",
        FailureType.BUILD_COMPILATION,
        "build-failure",
    ),
    (
        "compilation terminated",
        "gcc: fatal error: compilation terminated\n",
        FailureType.BUILD_COMPILATION,
        "build-failure",
    ),
    (
        "invalid configuration",
        "ERROR: values.yaml: invalid configuration: expected a mapping, got a string\n",
        FailureType.CONFIGURATION,
        "configuration",
    ),
    (
        "configuration error",
        "fatal: configuration error: missing 'project' key\n",
        FailureType.CONFIGURATION,
        "configuration",
    ),
    (
        "YAMLError",
        "yaml.scanner.ScannerError: while parsing a block mapping\n",
        FailureType.CONFIGURATION,
        "configuration",
    ),
    (
        "cannot parse config",
        "Error: could not parse the configuration file at .config/app.yml\n",
        FailureType.CONFIGURATION,
        "configuration",
    ),
    (
        "invalid workflow file",
        "Error: invalid workflow file: 'runs-on' is not allowed here\n",
        FailureType.CONFIGURATION,
        "configuration",
    ),
    # -- unknown ----------------------------------------------------------
    (
        "ambiguous deployment log",
        "Run ./scripts/deploy.sh\n[info] starting deployment\n"
        "[warn] orchestrator returned a non-zero status\n",
        FailureType.UNKNOWN,
        "unknown-no-signature",
    ),
    (
        "package names only, no failure signature",
        "Run .ci/assemble.sh\n[info] bundling requests module assets\n"
        "[info] copying pytest fixtures into the artifact\n",
        FailureType.UNKNOWN,
        "unknown-no-signature",
    ),
]


@pytest.mark.parametrize(
    ("label", "logs", "expected_type", "expected_rule"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_classifies_each_supported_signature(classify, label, logs, expected_type, expected_rule):
    classification = classify(logs)
    assert classification.failure_type is expected_type, label
    assert classification.rule_id == expected_rule, label


class TestPrecedence:
    """The engine must report the root cause, not the loudest symptom."""

    def test_missing_module_inside_a_pytest_run_is_a_dependency_error(self, classify):
        """A missing module surfaces *through* pytest; it is not a test failure."""
        logs = (
            "Run pytest -q\n"
            "Importing test modules ...\n"
            "ModuleNotFoundError: No module named 'pytest_asyncio'\n"
            "!!! Interrupted: 1 error during collection !!!\n"
            "FAILED tests/test_api.py::test_health - AssertionError: expected 200 got 500\n"
        )
        classification = classify(logs, failure_type="dependency_resolution")
        assert classification.failure_type is FailureType.DEPENDENCY_RESOLUTION
        assert classification.rule_id == "dependency-missing-module"

    def test_syntax_error_outranks_a_generic_build_failure(self, classify):
        logs = 'BUILD FAILED in 4s\n  File "app.py", line 3\nSyntaxError: invalid syntax\n'
        classification = classify(logs, failure_type="build_compilation")
        assert classification.rule_id == "syntax-or-import"

    def test_import_error_outranks_a_generic_test_summary(self, classify):
        logs = (
            "FAILED tests/test_a.py::test_one\n"
            "ImportError: cannot import name 'build_url' from 'app.urls'\n"
        )
        classification = classify(logs, failure_type="build_compilation")
        assert classification.failure_type is FailureType.BUILD_COMPILATION
        assert classification.rule_id == "syntax-or-import"

    def test_assertion_outranks_a_bare_summary_count(self, classify):
        logs = (
            "============================== 1 failed, 41 passed in 12.31s ==============================\n"
            "FAILED tests/test_app.py::test_login - AssertionError: expected 200 got 500\n"
        )
        classification = classify(logs)
        assert classification.confidence == 0.8

    def test_build_failure_outranks_configuration(self, classify):
        logs = "invalid configuration in gradle.properties\nmake: *** [Makefile:6: all] Error 1\n"
        classification = classify(logs, failure_type="build_compilation")
        assert classification.rule_id == "build-failure"

    def test_a_zero_failure_count_is_not_a_test_failure(self, classify):
        """'0 failed' must not be read as evidence of a failure."""
        classification = classify(
            "============================== 0 failed, 41 passed in 12.31s =====\n"
        )
        assert classification.failure_type is FailureType.UNKNOWN


class TestUnknownIsNotForced:
    def test_insufficient_logs_return_unknown(self, classify):
        classification = classify("[info] job started\n[info] job finished\n")
        assert classification.failure_type is FailureType.UNKNOWN
        assert classification.confidence == 0.0
        assert classification.recommended_action is RecommendedAction.ESCALATE_TO_HUMAN
        assert classification.requires_human_review is True

    def test_package_name_alone_is_not_a_dependency_problem(self, classify):
        classification = classify(
            "Run pip install -r requirements.txt\nSuccessfully installed requests-2.31.0\n"
        )
        assert classification.failure_type is FailureType.UNKNOWN

    def test_conflicting_signatures_resolve_by_precedence(self, classify):
        """Several signatures at once: the root cause wins, not the loudest line.

        A failing test is reported ahead of build and configuration noise,
        because the engine's precedence puts test failures above build failures.
        The disagreement itself is a limitation, which the engine states.
        """
        logs = (
            "Run pytest -q\n"
            "FAILED tests/test_a.py::test_one\n"
            "make: *** [Makefile:6: all] Error 1\n"
            "ERROR: values.yaml: invalid configuration\n"
        )
        classification = classify(logs)
        assert classification.rule_id == "test-failure"
        assert classification.confidence == 0.8
        assert "hypothesis" in classification.limitations

    def test_equally_uninformative_lines_stay_unknown(self, classify):
        """Nothing to stand on means nothing is claimed."""
        logs = "[info] step one\n[info] step two\n[info] step three\n[info] step four\n"
        assert classify(logs).failure_type is FailureType.UNKNOWN


class TestRecommendationSafety:
    def test_no_rule_recommends_a_blind_install(self):
        from autoheal_diagnosis.classifier import RULES

        for rule in RULES:
            assert rule.recommended_action is not RecommendedAction.RETRY_JOB

    def test_dependency_actions_are_the_manifest_oriented_ones(self):
        from autoheal_diagnosis.classifier import RULES

        actions = {
            rule.rule_id: rule.recommended_action
            for rule in RULES
            if rule.failure_type is FailureType.DEPENDENCY_RESOLUTION
        }
        for rule_id, action in actions.items():
            assert action in {
                RecommendedAction.PIN_DEPENDENCY,
                RecommendedAction.UPDATE_DEPENDENCY,
            }, rule_id

    def test_risk_reflects_the_proposed_action(self):
        from autoheal_diagnosis.classifier import RULES

        for rule in RULES:
            if rule.risk_level is RiskLevel.LOW:
                # Low risk is reserved for read/inspect-only work.
                assert rule.rule_id in {"syntax-or-import", "test-failure"}, rule.rule_id

    def test_only_low_risk_rules_skip_human_review(self):
        from autoheal_diagnosis.classifier import RULES

        for rule in RULES:
            if not rule.requires_human_review:
                assert rule.risk_level is RiskLevel.LOW, rule.rule_id
