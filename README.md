# AutoHeal DevOps AI

An AI-assisted, self-healing CI/CD pipeline that detects software delivery failures, diagnoses probable root causes, proposes controlled remediations, and verifies recovery through automated validation.

## Core workflow

1. Detect a CI/CD failure.
2. Normalize the failure into a shared `FailureEvent`.
3. Diagnose the failure and produce an evidence-backed `DiagnosisResult`.
4. Evaluate remediation proposals against safety policies.
5. Execute permitted repairs in an isolated environment.
6. Rerun tests and verify the result.
7. Record the incident and its outcome.

## Safety principles

- A proposed repair is not a verified repair.
- No unrestricted command execution.
- No direct production modifications.
- Failed validation must never be reported as successful recovery.
- Unknown or high-risk failures must be escalated for human review.

## Project status

Initial repository setup.