# autoheal-diagnosis

Evidence-backed CI/CD failure diagnosis (Member 2).

Turns a validated `FailureEvent` into a validated `DiagnosisResult`. That is the
whole job — it never executes a repair.

```python
from autoheal_diagnosis import diagnose
from autoheal_contracts import FailureEvent

event = FailureEvent.model_validate(payload)
result = diagnose(event)              # rule-only: no LLM, no key, no network
```

## Why it is safe by construction

| Property | How it is enforced |
| --- | --- |
| Evidence is real | every `failure_logs` quote is cut straight out of `event.logs`, and the result is re-validated with the event in the validation context before it is returned |
| No fabricated confidence | the deterministic engine emits a fixed heuristic per signature, bounded `0.0–1.0` |
| No unseen actions | only the `RecommendedAction` enum categories are produced, and the text in `probable_cause` / `limitations` never instructs an install, a test deletion or a security bypass |
| Unknown stays unknown | when no signature matches the engine returns `unknown` + `escalate_to_human` instead of guessing |
| No relabelling | `DiagnosisResult.failure_type` must equal the event's, so a disagreement becomes an escalation, never an override |
| Logs are data | injection text inside a log is never followed; the optional LLM layer is asked for JSON and its output is structurally validated, never executed |

## Layout

```
src/autoheal_diagnosis/
  classifier.py   deterministic rule engine (offline, first layer)
  evidence.py     quote extraction and verification
  service.py      orchestration and DiagnosisResult assembly
  providers.py    optional, provider-neutral LLM transport abstraction
  config.py       settings from AUTOHEAL_DIAGNOSIS_* environment variables
  fixtures.py     local FailureEvent fixtures (not real CI data)
  __main__.py     offline fixture runner / evaluation harness
```

## Offline evaluation

```bash
python -m autoheal_diagnosis                          # evaluation table
python -m autoheal_diagnosis dependency_missing_module
python -m autoheal_diagnosis path/to/event.json --json
```

No GitHub credentials, no LLM API key, no network and no shell execution are
required. Exit code `0` means every fixture matched its expectation.

Full documentation: [`docs/diagnosis.md`](../../docs/diagnosis.md).
