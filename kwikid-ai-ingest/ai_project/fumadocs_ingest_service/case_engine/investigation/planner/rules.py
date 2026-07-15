"""
case_engine/investigation/planner/rules.py

Sprint 2.39: Deterministic planning rules for the Investigation Evidence Planner.

Each PlanningRule maps a topic to an ordered set of PlanningSteps.
Rules declare WHAT evidence to collect — never which tool to invoke.

Rules are consulted by InvestigationPlanner (engine.py) in order:
  1. Topic-specific rule (if one matches)
  2. DefaultWorkflowPlanningRule (if a workflow definition is present)
  3. MinimalFallbackRule (absolute last resort)

Concrete rules:
  VKYCSessionFailurePlanningRule   — VKYC_SESSION_FAILURE
  OTPDeliveryFailurePlanningRule   — OTP_DELIVERY_FAILURE
  DocumentOCRPlanningRule          — DOCUMENT_OCR_FAILURE
  APICallbackPlanningRule          — API_CALLBACK_FAILURE
  AgentPortalPlanningRule          — AGENT_PORTAL_ISSUE
  DefaultWorkflowPlanningRule      — any topic with a workflow definition
  MinimalFallbackRule              — absolute fallback, produces 1-step plan

Dependency direction:
  rules.py → planner/models.py
  rules.py → investigation/evidence/models.py (EvidencePriority)
  rules.py → tools/tool_models.py (ToolCapability)
  rules.py → workflows/models.py (WorkflowDefinition) via TYPE_CHECKING
  rules.py → knowledge/sop/models.py (SOPDocument) via TYPE_CHECKING
  rules.py does NOT import from investigation/context.py or investigation/planner/engine.py
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.planner.models import (
    CompletionCondition,
    DependencyType,
    EvidenceKind,
    EvidenceRequirement,
    FailureStrategy,
    FallbackStrategy,
    InvestigationGraph,
    InvestigationPlan,
    InvestigationPriority,
    EstimatedComplexity,
    PlanningStep,
    PreconditionKind,
    RetryPolicy,
    StepDependency,
    StepOutput,
    StepPrecondition,
)
from case_engine.tools.tool_models import ToolCapability

if TYPE_CHECKING:
    from case_engine.knowledge.sop.models import SOPDocument
    from case_engine.workflows.models import WorkflowDefinition

LOGGER = logging.getLogger(__name__)

_DEFAULT_RETRY   = RetryPolicy(max_attempts=2, backoff_seconds=0.0, retry_on_timeout=True)
_STRICT_RETRY    = RetryPolicy(max_attempts=3, backoff_seconds=1.0, retry_on_timeout=True)
_NO_RETRY        = RetryPolicy(max_attempts=1, backoff_seconds=0.0, retry_on_timeout=False)
_CONFIDENCE_THRESHOLD = 0.75


# ── Helpers ────────────────────────────────────────────────────────────────────

def _req(
    req_id: str,
    kind: EvidenceKind,
    title: str,
    description: str,
    priority: EvidencePriority,
    required: bool,
    expected_fields: tuple[str, ...],
    capability: ToolCapability | None = None,
    hints: tuple[str, ...] = (),
) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id=req_id,
        kind=kind,
        title=title,
        description=description,
        priority=priority,
        required=required,
        expected_fields=expected_fields,
        validation_hints=hints,
        capability_hint=capability,
    )


def _pre(pre_id: str, description: str, kind: PreconditionKind, key: str) -> StepPrecondition:
    return StepPrecondition(precondition_id=pre_id, description=description, kind=kind, key=key)


def _out(out_id: str, description: str, kind: EvidenceKind, key: str) -> StepOutput:
    return StepOutput(output_id=out_id, description=description, kind=kind, key=key)


def _seq(from_id: str, to_id: str) -> StepDependency:
    return StepDependency(from_step_id=from_id, to_step_id=to_id, dependency_type=DependencyType.SEQUENTIAL)


def _build_graph(steps: tuple[PlanningStep, ...]) -> InvestigationGraph:
    """Build an InvestigationGraph from steps' depends_on fields."""
    edges: list[StepDependency] = []
    step_ids = {s.step_id for s in steps}
    for step in steps:
        for dep_id in step.depends_on:
            if dep_id in step_ids:
                edges.append(_seq(dep_id, step.step_id))

    parallel_groups = _find_parallel_groups(steps, tuple(edges))

    return InvestigationGraph(
        nodes=steps,
        edges=tuple(edges),
        parallel_groups=tuple(parallel_groups),
    )


def _find_parallel_groups(
    steps: tuple[PlanningStep, ...],
    edges: tuple[StepDependency, ...],
) -> list[frozenset[str]]:
    """Find sets of steps that share the same single predecessor and are all parallelizable."""
    from collections import defaultdict
    # Group steps by their predecessor set (for SEQUENTIAL deps)
    predecessor_sets: dict[frozenset[str], list[str]] = defaultdict(list)
    for step in steps:
        if step.parallelizable:
            seq_deps = frozenset(
                e.from_step_id for e in edges
                if e.to_step_id == step.step_id and e.dependency_type == DependencyType.SEQUENTIAL
            )
            predecessor_sets[seq_deps].append(step.step_id)

    return [
        frozenset(step_ids)
        for step_ids in predecessor_sets.values()
        if len(step_ids) > 1
    ]


def _collect_all_requirements(steps: tuple[PlanningStep, ...]) -> tuple[EvidenceRequirement, ...]:
    seen: set[str] = set()
    result: list[EvidenceRequirement] = []
    for step in steps:
        for req in step.evidence_required:
            if req.requirement_id not in seen:
                seen.add(req.requirement_id)
                result.append(req)
    return tuple(result)


def _build_completion_conditions(steps: tuple[PlanningStep, ...]) -> tuple[CompletionCondition, ...]:
    conditions: list[CompletionCondition] = []
    for step in steps:
        if not step.optional:
            for req in step.evidence_required:
                if req.required:
                    conditions.append(CompletionCondition(
                        condition_id=f"cond_{step.step_id}_{req.requirement_id}",
                        description=f"{req.title} collected in {step.title}",
                        required=True,
                    ))
    return tuple(conditions)


def _estimate_complexity(steps: tuple[PlanningStep, ...]) -> EstimatedComplexity:
    n = len(steps)
    has_vision = any(
        req.kind == EvidenceKind.VISION
        for s in steps for req in s.evidence_required
    )
    if n <= 1:
        return EstimatedComplexity.TRIVIAL
    if n == 2:
        return EstimatedComplexity.LOW
    if n <= 4:
        return EstimatedComplexity.HIGH if has_vision else EstimatedComplexity.MEDIUM
    if n <= 6:
        return EstimatedComplexity.HIGH
    return EstimatedComplexity.VERY_HIGH


# ── Abstract rule ──────────────────────────────────────────────────────────────

class PlanningRule(ABC):
    """
    Abstract base for all topic-specific planning rules.

    A PlanningRule produces an InvestigationPlan for a specific topic.
    It has NO side effects — it only constructs typed domain objects.
    """

    @abstractmethod
    def topic_key(self) -> str:
        """Uppercase topic key this rule handles (e.g., 'VKYC_SESSION_FAILURE')."""
        ...

    @abstractmethod
    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        """
        Build and return an InvestigationPlan.

        Never raises. Never executes tools. Never modifies any external state.
        """
        ...

    def applies_to(self, topic: str) -> bool:
        """Return True if this rule handles the given topic (case-insensitive)."""
        return topic.upper().replace("-", "_") == self.topic_key()


# ── VKYC Session Failure ───────────────────────────────────────────────────────

class VKYCSessionFailurePlanningRule(PlanningRule):
    """
    Evidence plan for VKYC_SESSION_FAILURE.

    Investigation objective: Determine why the VKYC session failed and whether
    a session reset can resolve the issue without manual escalation.

    Evidence required (in dependency order):
      Step 1 (CRITICAL): Session state — failure code, attempt count, reset eligibility
      Step 2 (HIGH, parallel): User identity — KYC status, risk tier, account state
      Step 3 (HIGH, parallel): Failure log — category, transience, recommended action
      Step 4 (LOW, optional, parallel): Visual evidence — camera/liveness analysis
    """

    def topic_key(self) -> str:
        return "VKYC_SESSION_FAILURE"

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_session_evidence",
            order=0,
            title="Collect VKYC Session State",
            description=(
                "Retrieve session status, attempt count, failure code, and reset eligibility "
                "from the Unity VKYC platform. This is the primary evidence for the investigation."
            ),
            evidence_required=(_req(
                "req_vkyc_session_state",
                EvidenceKind.SESSION,
                "VKYC Session Details",
                "Session status, attempt count, failure code, and reset eligibility",
                EvidencePriority.CRITICAL,
                required=True,
                expected_fields=("session_id", "session_status", "attempt_count", "failure_code", "reset_eligible"),
                capability=ToolCapability.READ,
                hints=("session_id must be present in slots",),
            ),),
            evidence_priority=EvidencePriority.CRITICAL,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_session_id", "session_id slot must be filled", PreconditionKind.SLOT_PRESENT, "session_id"),),
            outputs=(_out("out_session_state", "VKYC session state record", EvidenceKind.SESSION, "session_state"),),
            retry_policy=_STRICT_RETRY,
            failure_strategy=FailureStrategy.ESCALATE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        step_02 = PlanningStep(
            step_id="step_02_user_evidence",
            order=1,
            title="Collect User Identity and KYC State",
            description=(
                "Retrieve KYC status, account state, and risk tier from the Unity platform. "
                "Used to determine if the user is in a state where reset is safe."
            ),
            evidence_required=(_req(
                "req_vkyc_user_identity",
                EvidenceKind.API,
                "User Identity Details",
                "KYC status, account state, and risk tier from Unity platform",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("kyc_status", "account_state", "risk_tier"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_phone", "phone_number slot must be filled", PreconditionKind.SLOT_PRESENT, "phone_number"),),
            outputs=(_out("out_user_identity", "User identity record", EvidenceKind.API, "user_identity"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_session_evidence",),
        )

        step_03 = PlanningStep(
            step_id="step_03_log_evidence",
            order=2,
            title="Collect Failure Log Analysis",
            description=(
                "Retrieve failure category, transience indicator, and recommended action "
                "from log analysis. Determines if the failure is transient or structural."
            ),
            evidence_required=(_req(
                "req_vkyc_failure_log",
                EvidenceKind.LOG,
                "Failure Log Analysis",
                "Failure category, is_transient flag, and recommended_action from log analysis",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("failure_category", "is_transient", "recommended_action"),
                capability=ToolCapability.QUERY,
                hints=("session_id from step_01 is the primary input",),
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_session_ev", "Session evidence must be collected", PreconditionKind.EVIDENCE_PRESENT, "session_state"),),
            outputs=(_out("out_failure_log", "Failure log analysis record", EvidenceKind.LOG, "failure_log"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_session_evidence",),
        )

        step_04 = PlanningStep(
            step_id="step_04_vision_evidence",
            order=3,
            title="Collect Visual Evidence (Optional)",
            description=(
                "Analyse VKYC session screenshots or recordings for camera/liveness issues. "
                "Optional — only if visual artefacts are available. Cannot block resolution."
            ),
            evidence_required=(_req(
                "req_vkyc_vision_analysis",
                EvidenceKind.VISION,
                "Visual Session Analysis",
                "Computer vision analysis of VKYC session visual input for camera/liveness issues",
                EvidencePriority.LOW,
                required=False,
                expected_fields=("findings", "confidence"),
                capability=ToolCapability.ANALYZE,
                hints=("Only run if visual artefact URI is available",),
            ),),
            evidence_priority=EvidencePriority.LOW,
            candidate_capabilities=(ToolCapability.ANALYZE,),
            preconditions=(_pre("pre_vision_always", "Unconditional — skip if no artefact", PreconditionKind.ALWAYS, ""),),
            outputs=(_out("out_vision_analysis", "Visual evidence from VKYC session", EvidenceKind.VISION, "vision_analysis"),),
            retry_policy=_NO_RETRY,
            failure_strategy=FailureStrategy.SKIP,
            parallelizable=True,
            optional=True,
            depends_on=("step_01_session_evidence",),
        )

        steps = (step_01, step_02, step_03, step_04)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective=(
                "Determine why the VKYC session failed and whether a session reset "
                "can resolve the issue without manual escalation."
            ),
            investigation_priority=InvestigationPriority.HIGH,
            estimated_complexity=_estimate_complexity(steps),
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.SKIP_OPTIONAL_STEPS,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── OTP Delivery Failure ───────────────────────────────────────────────────────

class OTPDeliveryFailurePlanningRule(PlanningRule):
    """
    Evidence plan for OTP_DELIVERY_FAILURE.

    Investigation objective: Determine why the OTP was not received and
    whether an OTP resend can resolve the issue.

    Evidence required:
      Step 1 (CRITICAL): User evidence — phone number, KYC status
      Step 2 (CRITICAL, parallel): Log evidence — OTP generation logs, SMS provider response
      Step 3 (HIGH, parallel): API evidence — delivery callback status
      Step 4 (NORMAL, optional, parallel): Summary evidence — case history for repeat failures
    """

    def topic_key(self) -> str:
        return "OTP_DELIVERY_FAILURE"

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_user_evidence",
            order=0,
            title="Collect User Phone and KYC State",
            description=(
                "Retrieve the user's phone number, KYC status, and account state. "
                "Verifies the phone number is valid and account is active before investigating OTP delivery."
            ),
            evidence_required=(_req(
                "req_otp_user_state",
                EvidenceKind.API,
                "User Phone and KYC State",
                "Phone number, KYC status, account state from Unity platform",
                EvidencePriority.CRITICAL,
                required=True,
                expected_fields=("phone_number", "kyc_status", "account_state"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.CRITICAL,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_phone", "phone_number slot must be filled", PreconditionKind.SLOT_PRESENT, "phone_number"),),
            outputs=(_out("out_user_state", "User phone and KYC state", EvidenceKind.API, "user_state"),),
            retry_policy=_STRICT_RETRY,
            failure_strategy=FailureStrategy.ESCALATE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        step_02 = PlanningStep(
            step_id="step_02_otp_log_evidence",
            order=1,
            title="Collect OTP Generation and SMS Delivery Logs",
            description=(
                "Retrieve OTP generation logs, SMS provider response, and delivery status. "
                "Identifies if OTP was generated but SMS delivery failed, or if generation itself failed."
            ),
            evidence_required=(_req(
                "req_otp_delivery_log",
                EvidenceKind.LOG,
                "OTP Delivery Log Analysis",
                "OTP generation status, SMS provider response, and failure category",
                EvidencePriority.CRITICAL,
                required=True,
                expected_fields=("otp_generated", "sms_provider", "failure_category", "is_transient"),
                capability=ToolCapability.QUERY,
                hints=("phone_number from step_01 is the primary input",),
            ),),
            evidence_priority=EvidencePriority.CRITICAL,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_user_ev", "User evidence must be collected", PreconditionKind.EVIDENCE_PRESENT, "user_state"),),
            outputs=(_out("out_otp_log", "OTP delivery log analysis", EvidenceKind.LOG, "otp_log"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_user_evidence",),
        )

        step_03 = PlanningStep(
            step_id="step_03_callback_evidence",
            order=2,
            title="Collect Delivery Callback Status",
            description=(
                "Retrieve the SMS delivery callback status from the provider. "
                "Determines if delivery was attempted and what the carrier reported."
            ),
            evidence_required=(_req(
                "req_otp_callback",
                EvidenceKind.API,
                "SMS Delivery Callback",
                "Delivery callback status, timestamp, and carrier response code",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("callback_status", "delivery_timestamp", "carrier_code"),
                capability=ToolCapability.QUERY,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_user_ev2", "User evidence must be collected", PreconditionKind.EVIDENCE_PRESENT, "user_state"),),
            outputs=(_out("out_callback", "SMS delivery callback record", EvidenceKind.API, "delivery_callback"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_user_evidence",),
        )

        step_04 = PlanningStep(
            step_id="step_04_case_history",
            order=3,
            title="Collect Case History (Optional)",
            description=(
                "Retrieve prior OTP delivery failures for this user. "
                "Optional — helps identify repeat patterns but is not required to resolve."
            ),
            evidence_required=(_req(
                "req_otp_case_history",
                EvidenceKind.SUMMARY,
                "OTP Case History",
                "Prior OTP delivery failures, escalation rate, and repeat patterns",
                EvidencePriority.NORMAL,
                required=False,
                expected_fields=("prior_cases", "escalation_rate", "repeat_pattern"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.NORMAL,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_history_always", "Unconditional — optional step", PreconditionKind.ALWAYS, ""),),
            outputs=(_out("out_case_history", "Case history summary", EvidenceKind.SUMMARY, "case_history"),),
            retry_policy=_NO_RETRY,
            failure_strategy=FailureStrategy.SKIP,
            parallelizable=True,
            optional=True,
            depends_on=("step_01_user_evidence",),
        )

        steps = (step_01, step_02, step_03, step_04)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective=(
                "Determine why the OTP was not delivered and whether "
                "an OTP resend can resolve the issue."
            ),
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=_estimate_complexity(steps),
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── Document OCR Failure ───────────────────────────────────────────────────────

class DocumentOCRPlanningRule(PlanningRule):
    """
    Evidence plan for DOCUMENT_OCR_FAILURE.

    Investigation objective: Determine why the document OCR failed and whether
    the document can be re-submitted after remediation.

    Evidence required:
      Step 1 (HIGH): User evidence — KYC status and onboarding stage
      Step 2 (CRITICAL, optional parallel): Visual evidence — document image analysis
      Step 3 (HIGH, parallel): Summary evidence — onboarding status
    """

    def topic_key(self) -> str:
        return "DOCUMENT_OCR_FAILURE"

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_user_evidence",
            order=0,
            title="Collect User KYC and Onboarding State",
            description="Retrieve KYC status and current onboarding stage for the user.",
            evidence_required=(_req(
                "req_doc_user_state",
                EvidenceKind.API,
                "User KYC and Onboarding State",
                "KYC status, account state, and onboarding stage",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("kyc_status", "account_state", "onboarding_stage"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_phone", "phone_number slot must be filled", PreconditionKind.SLOT_PRESENT, "phone_number"),),
            outputs=(_out("out_user_state", "User KYC state", EvidenceKind.API, "user_state"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.ESCALATE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        step_02 = PlanningStep(
            step_id="step_02_document_vision",
            order=1,
            title="Analyse Document Image",
            description="Computer vision analysis of the submitted document image for legibility and OCR issues.",
            evidence_required=(_req(
                "req_doc_vision",
                EvidenceKind.VISION,
                "Document Image Analysis",
                "Legibility, OCR quality, and document classification from computer vision",
                EvidencePriority.CRITICAL,
                required=True,
                expected_fields=("is_legible", "ocr_quality", "document_type", "findings"),
                capability=ToolCapability.ANALYZE,
                hints=("document_uri must be available", "Use DOCUMENT modality"),
            ),),
            evidence_priority=EvidencePriority.CRITICAL,
            candidate_capabilities=(ToolCapability.ANALYZE,),
            preconditions=(_pre("pre_user_ev", "User state must be collected", PreconditionKind.EVIDENCE_PRESENT, "user_state"),),
            outputs=(_out("out_doc_vision", "Document visual analysis", EvidenceKind.VISION, "document_vision"),),
            retry_policy=_NO_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_user_evidence",),
        )

        step_03 = PlanningStep(
            step_id="step_03_onboarding_status",
            order=2,
            title="Collect Onboarding Status",
            description="Retrieve current onboarding completion percentage and blocking step.",
            evidence_required=(_req(
                "req_doc_onboarding",
                EvidenceKind.SUMMARY,
                "Onboarding Status",
                "Onboarding completion percentage, blocking step, and application ID",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("completion_pct", "blocking_step", "application_id"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_appid", "application_id slot must be filled", PreconditionKind.SLOT_PRESENT, "application_id"),),
            outputs=(_out("out_onboarding", "Onboarding status record", EvidenceKind.SUMMARY, "onboarding_status"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=True,
            optional=False,
            depends_on=("step_01_user_evidence",),
        )

        steps = (step_01, step_02, step_03)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective="Determine why document OCR failed and whether resubmission can resolve it.",
            investigation_priority=InvestigationPriority.HIGH,
            estimated_complexity=_estimate_complexity(steps),
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.ESCALATE_IMMEDIATELY,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── API Callback Failure ───────────────────────────────────────────────────────

class APICallbackPlanningRule(PlanningRule):
    """
    Evidence plan for API_CALLBACK_FAILURE.

    Investigation objective: Determine why the callback was not received and
    whether a callback retry can resolve the issue.
    """

    def topic_key(self) -> str:
        return "API_CALLBACK_FAILURE"

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_failure_log",
            order=0,
            title="Collect Callback Failure Analysis",
            description="Retrieve the failure category, error code, and transience from log analysis.",
            evidence_required=(_req(
                "req_callback_log",
                EvidenceKind.LOG,
                "Callback Failure Log",
                "Failure category, error code, retry count, and transience indicator",
                EvidencePriority.CRITICAL,
                required=True,
                expected_fields=("failure_category", "error_code", "retry_count", "is_transient"),
                capability=ToolCapability.QUERY,
            ),),
            evidence_priority=EvidencePriority.CRITICAL,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_always", "Unconditional", PreconditionKind.ALWAYS, ""),),
            outputs=(_out("out_failure_log", "Callback failure log", EvidenceKind.LOG, "callback_failure_log"),),
            retry_policy=_STRICT_RETRY,
            failure_strategy=FailureStrategy.ESCALATE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        step_02 = PlanningStep(
            step_id="step_02_callback_api",
            order=1,
            title="Query Callback Delivery Status",
            description="Retrieve callback delivery status from the API provider.",
            evidence_required=(_req(
                "req_callback_api",
                EvidenceKind.API,
                "Callback Delivery Status",
                "Callback delivery status, timestamp, and provider response code",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("delivery_status", "delivery_timestamp", "response_code"),
                capability=ToolCapability.QUERY,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_log_ev", "Log evidence must be collected", PreconditionKind.EVIDENCE_PRESENT, "callback_failure_log"),),
            outputs=(_out("out_callback_api", "Callback delivery status", EvidenceKind.API, "callback_delivery"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=False,
            depends_on=("step_01_failure_log",),
        )

        steps = (step_01, step_02)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective="Determine why the API callback was not received and whether a retry resolves it.",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=_estimate_complexity(steps),
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── Agent Portal Issue ─────────────────────────────────────────────────────────

class AgentPortalPlanningRule(PlanningRule):
    """
    Evidence plan for AGENT_PORTAL_ISSUE.

    Investigation objective: Determine why the agent portal is not working
    and whether the issue is user-side, session-side, or infrastructure-side.
    """

    def topic_key(self) -> str:
        return "AGENT_PORTAL_ISSUE"

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_user_evidence",
            order=0,
            title="Collect User State",
            description="Retrieve user KYC status and account state to determine if the issue is user-specific.",
            evidence_required=(_req(
                "req_portal_user_state",
                EvidenceKind.API,
                "User Account State",
                "KYC status, account state, and risk tier",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("kyc_status", "account_state", "risk_tier"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_phone", "phone_number slot must be filled", PreconditionKind.SLOT_PRESENT, "phone_number"),),
            outputs=(_out("out_user_state", "User account state", EvidenceKind.API, "user_state"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        step_02 = PlanningStep(
            step_id="step_02_failure_log",
            order=1,
            title="Collect Portal Failure Log",
            description="Retrieve portal failure category and error details.",
            evidence_required=(_req(
                "req_portal_failure_log",
                EvidenceKind.LOG,
                "Portal Failure Log",
                "Failure category, error code, and transience indicator",
                EvidencePriority.HIGH,
                required=True,
                expected_fields=("failure_category", "error_code", "is_transient"),
                capability=ToolCapability.QUERY,
            ),),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(ToolCapability.QUERY,),
            preconditions=(_pre("pre_user_ev", "User evidence must be collected", PreconditionKind.EVIDENCE_PRESENT, "user_state"),),
            outputs=(_out("out_portal_log", "Portal failure log", EvidenceKind.LOG, "portal_failure_log"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=False,
            depends_on=("step_01_user_evidence",),
        )

        steps = (step_01, step_02)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective="Determine why the agent portal is not accessible and whether a portal refresh resolves it.",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=_estimate_complexity(steps),
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── Default Workflow Rule ──────────────────────────────────────────────────────

class DefaultWorkflowPlanningRule(PlanningRule):
    """
    Fallback rule for topics with a workflow definition but no specific rule.

    Generates a plan from the workflow's tool_candidates, converting each
    to evidence requirements by their ToolCapability.
    """

    def topic_key(self) -> str:
        return "__DEFAULT_WORKFLOW__"

    def applies_to(self, topic: str) -> bool:
        return False  # Never auto-selected — used explicitly by the planner

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()
        steps: list[PlanningStep] = []

        if workflow and workflow.required_slots:
            for idx, slot_name in enumerate(workflow.required_slots):
                steps.append(PlanningStep(
                    step_id=f"step_{idx:02d}_slot_{slot_name}",
                    order=idx,
                    title=f"Collect {slot_name.replace('_', ' ').title()} Evidence",
                    description=f"Collect evidence required for slot: {slot_name}",
                    evidence_required=(_req(
                        f"req_slot_{slot_name}",
                        EvidenceKind.API,
                        f"{slot_name.replace('_', ' ').title()} Data",
                        f"Evidence required for workflow slot: {slot_name}",
                        EvidencePriority.HIGH,
                        required=True,
                        expected_fields=(slot_name,),
                        capability=ToolCapability.READ,
                    ),),
                    evidence_priority=EvidencePriority.HIGH,
                    candidate_capabilities=(ToolCapability.READ,),
                    preconditions=(_pre(f"pre_{slot_name}", f"{slot_name} must be available", PreconditionKind.ALWAYS, slot_name),),
                    outputs=(_out(f"out_{slot_name}", f"{slot_name} data", EvidenceKind.API, slot_name),),
                    retry_policy=_DEFAULT_RETRY,
                    failure_strategy=FailureStrategy.CONTINUE,
                    parallelizable=idx > 0,
                    optional=False,
                    depends_on=() if idx == 0 else (f"step_00_slot_{workflow.required_slots[0]}",),
                ))

        if not steps:
            return MinimalFallbackRule().generate_plan(
                plan_id, case_id, client_id, topic, workflow_id, slots, workflow, sop,
            )

        step_tuple = tuple(steps)
        graph = _build_graph(step_tuple)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective=f"Collect evidence required to resolve {topic.replace('_', ' ').title()} issue.",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=_estimate_complexity(step_tuple),
            expected_evidence=_collect_all_requirements(step_tuple),
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            completion_conditions=_build_completion_conditions(step_tuple),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at=now,
            steps=step_tuple,
            graph=graph,
        )


# ── Minimal Fallback Rule ──────────────────────────────────────────────────────

class MinimalFallbackRule(PlanningRule):
    """
    Absolute last-resort rule. Produces a single-step plan to collect user identity.

    Used when no topic-specific rule matches and no workflow is available.
    Ensures the planner ALWAYS returns a valid InvestigationPlan (never None, never raises).
    """

    def topic_key(self) -> str:
        return "__MINIMAL_FALLBACK__"

    def applies_to(self, topic: str) -> bool:
        return False  # Never auto-selected — used explicitly by the planner

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: WorkflowDefinition | None,
        sop: SOPDocument | None,
    ) -> InvestigationPlan:
        now = _now_iso()

        step_01 = PlanningStep(
            step_id="step_01_minimal_user",
            order=0,
            title="Collect Basic User Identity",
            description="Minimal evidence collection: user identity for root cause analysis.",
            evidence_required=(_req(
                "req_minimal_user",
                EvidenceKind.API,
                "Basic User Identity",
                "Minimal user identity for root cause analysis",
                EvidencePriority.NORMAL,
                required=True,
                expected_fields=("kyc_status", "account_state"),
                capability=ToolCapability.READ,
            ),),
            evidence_priority=EvidencePriority.NORMAL,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(_pre("pre_always", "Unconditional", PreconditionKind.ALWAYS, ""),),
            outputs=(_out("out_minimal_user", "Minimal user identity", EvidenceKind.API, "user_identity"),),
            retry_policy=_DEFAULT_RETRY,
            failure_strategy=FailureStrategy.ESCALATE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )

        steps = (step_01,)
        graph = _build_graph(steps)

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective=f"Minimal evidence collection for unrecognised topic: {topic}",
            investigation_priority=InvestigationPriority.LOW,
            estimated_complexity=EstimatedComplexity.TRIVIAL,
            expected_evidence=_collect_all_requirements(steps),
            confidence_threshold=0.5,
            completion_conditions=_build_completion_conditions(steps),
            fallback_strategy=FallbackStrategy.ESCALATE_IMMEDIATELY,
            created_at=now,
            steps=steps,
            graph=graph,
        )


# ── Registry ───────────────────────────────────────────────────────────────────

_DEFAULT_RULES: tuple[PlanningRule, ...] = (
    VKYCSessionFailurePlanningRule(),
    OTPDeliveryFailurePlanningRule(),
    DocumentOCRPlanningRule(),
    APICallbackPlanningRule(),
    AgentPortalPlanningRule(),
)


def get_default_rules() -> tuple[PlanningRule, ...]:
    """Return the default set of topic-specific planning rules."""
    return _DEFAULT_RULES


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()
