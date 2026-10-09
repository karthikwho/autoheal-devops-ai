# autoheal-contracts

Shared, provider-neutral Pydantic contracts for AutoHeal DevOps AI.

| Model | Purpose |
| --- | --- |
| `FailureEvent` | Normalised CI/CD failure as reported by the ingestion module. |
| `DiagnosisResult` | Evidence-backed hypothesis produced by the diagnosis module. |
| `ValidationResult` | Proof that checks were re-run after a remediation. |
| `IncidentRecord` | Aggregate tying the three above together with lifecycle state. |

These models hold no framework, transport or LLM dependencies — only Pydantic
and the standard library. See `docs/api-contracts.md` for field-by-field
documentation, JSON examples and the invariants each model enforces.

```python
from autoheal_contracts import FailureEvent, IncidentRecord

event = FailureEvent.model_validate(payload)
record = IncidentRecord.from_failure_event(event)
```
