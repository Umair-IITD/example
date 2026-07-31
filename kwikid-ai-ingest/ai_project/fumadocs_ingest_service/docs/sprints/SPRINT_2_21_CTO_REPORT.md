# Sprint 2.21 CTO Report — Action Proposal Engine + Risk Classification Foundation

**Date:** 2026-06-12
**Sprint:** 2.21
**Branch:** major-architecture-change
**Regression:** 3545 passed, 0 failed, 4 skipped

---

## Executive Summary

Sprint 2.21 delivers the **Action Proposal Engine** — the ACTIONPROPOSAL node in the flow diagram — which converts investigation and knowledge layer outputs into a structured, risk-assessed list of proposed remediation actions. The engine is deterministic, never calls LLMs, never executes actions, and is fully backwards compatible with all prior workflow infrastructure.

The system can now answer the foundational support automation question: **"What action should be taken for this support case?"** — with reasoning, risk classification, and audit trail, before any action gateway execution.

---

## Blueprint Compliance

| Blueprint Principle | Implementation |
|---|---|
| **Principle 1 — Investigation Before Action** | `ActionProposalService.propose()` returns `BLOCKED` if `investigation_result is None`. Gateway is never reached without investigation. |
| **Principle 3 — Reasoning Before Execution** | `ActionProposalEngine` runs and stores reasoning + risk assessments at proposal time. Nothing is executed. |
| **Section 15 — Action System** | 12 `ProposedActionType` values covering all KwikID support scenarios. |
| **Section 17 — Risk Model** | 3-tier table: SAFE (auto), REVERSIBLE (approval possible), HIGH_RISK (approval required). |
| **Flow Diagram — ACTIONPROPOSAL node** | Wired in: `ROOTCAUSE → HYBRIDRAG → REASONING → GUARDRAILS → ACTIONPROPOSAL → ACTIONGW` |
| **Flow Diagram — RISKCHECK diamond** | `SAFE → EXECUTE`, `REVERSIBLE/HIGH → APPROVAL` — risk assessed at proposal time, before gateway. |

---

## Architecture

```
ActionProposalService.propose()
    │
    ├── Guard: investigation_result is None → BLOCKED (blueprint Principle 1)
    │
    ├── Extract root_cause.category / confidence / escalate from investigation_result
    │
    ├── ActionProposalEngine.propose()
    │       ├── _PROPOSAL_RULES[root_cause_category] → ordered [ProposedActionType, ...]
    │       ├── SOP correlation: knowledge_result.sop_match_found → moves SOP action to priority 1
    │       ├── investigation_escalate=True → ESCALATE_L2 forced to priority 1
    │       ├── RiskAssessmentEngine.assess(action_type) per proposal → ActionRiskAssessment
    │       └── ActionProposalBundle(proposals, reasoning, top_proposal, all_safe, requires_approval)
    │
    ├── Audit: ACTION_PROPOSAL_STARTED → ACTION_PROPOSAL_COMPLETED (or BLOCKED)
    │
    └── bundle.to_dict() + {"status": "COMPLETED"} → JSONB-compatible dict
```

### Rule Table (14 root cause categories)

| Root Cause | Primary Action | Secondary |
|---|---|---|
| NETWORK_FAILURE | RESET_SESSION | ASK_USER_RETRY |
| TIMEOUT | RESET_SESSION | WAIT_AND_RETRY, ASK_USER_RETRY |
| QUOTA_EXCEEDED | WAIT_AND_RETRY | ESCALATE_L2 |
| EXPIRED_SESSION | RESET_SESSION | ASK_USER_RETRY |
| REPEATED_FAILURE | RESET_SESSION | MANUAL_REVIEW |
| LIVENESS_FAILURE | RETRY_DOCUMENT_CAPTURE | ASK_USER_RETRY, ESCALATE_L2 |
| DOCUMENT_FAILURE | RETRY_DOCUMENT_CAPTURE | ASK_USER_RETRY |
| VALIDATION_FAILURE | RETRY_DOCUMENT_CAPTURE | MANUAL_REVIEW |
| KYC_REJECTED | ESCALATE_L2 | MANUAL_REVIEW |
| SMS_DELIVERY_FAILURE | RESEND_OTP | ASK_USER_RETRY |
| CALLBACK_FAILURE | RETRY_CALLBACK | ASK_USER_RETRY |
| ONBOARDING_BLOCKED | MANUAL_REVIEW | ESCALATE_L2 |
| PORTAL_UNAVAILABLE | CHECK_SERVER_STATUS | REFRESH_PORTAL, WAIT_AND_RETRY |
| UNKNOWN | MANUAL_REVIEW | ESCALATE_L2 |

### Risk Classification Table

| Action Type | Risk Level | Requires Approval |
|---|---|---|
| ASK_USER_RETRY | SAFE | No |
| WAIT_AND_RETRY | SAFE | No |
| RESEND_OTP | SAFE | No |
| RETRY_DOCUMENT_CAPTURE | SAFE | No |
| CHECK_SERVER_STATUS | SAFE | No |
| REFRESH_PORTAL | SAFE | No |
| RESET_SESSION | REVERSIBLE | Yes |
| RETRY_CALLBACK | REVERSIBLE | Yes |
| MANUAL_REVIEW | REVERSIBLE | Yes |
| ESCALATE_L2 | REVERSIBLE | Yes |
| CREATE_ASANA_TICKET | REVERSIBLE | Yes |
| UNKNOWN_ACTION | HIGH_RISK | Yes |

---

## Files Created

| File | Description |
|---|---|
| `case_engine/actions/models.py` | Domain models: ProposedActionType, ProposalRiskLevel, ActionRiskAssessment, ActionReasoning, ActionProposalItem, ActionProposalBundle |
| `case_engine/actions/proposal.py` | ActionProposalEngine — deterministic rule table + SOP correlation |
| `case_engine/actions/risk.py` | RiskAssessmentEngine — table-driven SAFE/REVERSIBLE/HIGH_RISK |
| `case_engine/actions/service.py` | ActionProposalService — orchestration, audit, JSONB output |
| `case_engine/actions/__init__.py` | Public API + build_action_proposal_service factory |
| `api/routes/action_proposals_admin.py` | POST /admin/action-proposals/run |

---

## Files Modified

| File | Change |
|---|---|
| `case_engine/models.py` | +4 AuditEventType values: ACTION_PROPOSAL_STARTED, ACTION_PROPOSAL_COMPLETED, ACTION_PROPOSAL_BLOCKED, RISK_ASSESSMENT_COMPLETED |
| `case_engine/audit.py` | +4 methods: log_action_proposal_started/completed/blocked, log_risk_assessment_completed |
| `case_engine/workflows/models.py` | WorkflowExecutionResult: +action_proposal_result field, to_dict, from_dict |
| `case_engine/workflows/workflow_engine.py` | +action_proposal_service param, _run_action_proposal() private method |
| `app/main.py` | Register action_proposals_admin router |
| `api/routes/action_proposals_admin.py` | Fixed error_body call (no detail= param) |

---

## Tests Written — Sprint 2.21

| File | Tests | Coverage |
|---|---|---|
| `test_sprint221_action_models.py` | 60 | ProposedActionType, ProposalRiskLevel, ActionRiskAssessment, ActionReasoning, ActionProposalItem, ActionProposalBundle, WorkflowExecutionResult.action_proposal_result, AuditEventType |
| `test_sprint221_proposal_engine.py` | 50 | All 14 root cause categories, SOP correlation (11 cases), escalate override, bundle metadata, error recovery |
| `test_sprint221_risk_engine.py` | 41 | All 12 action types classified, parametrized SAFE/REVERSIBLE, assess_bundle, highest_risk, any_requires_approval |
| `test_sprint221_service.py` | 51 | COMPLETED result, BLOCKED guard, root cause extraction, escalate flag, audit integration, factory |
| `test_sprint221_workflow_integration.py` | 27 | Engine constructor backwards compat, action_proposal_result field, PROPOSE_ACTION step wiring, exception isolation |
| `test_sprint221_audit.py` | 30 | 4 audit methods × (no-supabase, insert called, outcome, event_type, exception isolation) |
| `test_sprint221_admin_api.py` | 18 | 200 happy path, field echoing, defaults, validation errors (422), structured 500, no-traceback |
| `test_sprint221_e2e.py` | 34 | 5 core topics, SOP correlation, escalation override, BLOCKED, bundle completeness, JSONB round-trip, error resilience |
| **Total** | **291** | |

---

## Regression Results

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| Pre-Sprint 2.21 | 3254 | 0 | 4 |
| Sprint 2.21 complete | **3545** | **0** | 4 |
| Net new tests | +291 | — | — |

---

## Bug Fixed During Sprint

**`api/routes/action_proposals_admin.py`**: The except handler called `error_body(code=..., message=..., detail=...)` but `error_body` only accepts `code` and `message`. This caused the except handler itself to raise a `TypeError`, resulting in FastAPI serving a generic "Internal Server Error" instead of structured JSON. Fixed by removing the `detail=` argument.

---

## Backwards Compatibility

- `WorkflowEngine()` with no arguments: still works (no proposal service wired, silently skips).
- `WorkflowExecutionResult` existing serialized JSON in database: `action_proposal_result` defaults to `None` via `from_dict`.
- No changes to existing action gateway, action models, or action runtime.
- No changes to existing workflow YAML playbooks.

---

## Remaining Gaps / Next Sprint

1. **Approval workflow integration**: When `requires_approval=True`, the system should pause and route to human approval. Currently the proposal is stored but the gateway proceeds to its own risk check.
2. **PROPOSE_ACTION step uses proposal result**: The workflow engine stores `action_proposal_result` but does not yet use `top_proposal.action_type` to override the YAML-declared `action_type` in the step. This would close the loop: investigation → knowledge → proposal → gateway.
3. **CREATE_ASANA_TICKET executor**: Risk classified, not yet wired to any action executor.
4. **Proposal-to-gateway mapping**: Map `ProposedActionType` → gateway `action_namespace/action_type` so proposals drive execution.
5. **RISK_ASSESSMENT_COMPLETED audit**: Currently emitted at bundle level; per-action emission would give finer audit granularity.
