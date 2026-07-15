"""
intelligence/orchestrator.py

Wave 3: `IntelligenceOrchestrator` — the single entry point that
turns an EvidenceBundle into an `IntelligenceResult` (reasoning +
clarification + observation + reply + action proposals).

The orchestrator is the ONLY place in the layer that:
  1. Calls the LLM
  2. Emits the 22 Wave 3 traces
  3. Combines parser outputs into an `IntelligenceResult`

It NEVER:
  - Executes actions (that's the Action Gateway / Wave 4)
  - Sends anything to Freshdesk (that's `FreshdeskResponseService`)
  - Mutates evidence (Context Builder produces immutable `LLMContext`)
  - Retries LLM calls beyond the client's own retry policy

Public API
----------
    IntelligenceOrchestrator(config, llm_client=None, prompt_builder=None)
    async orchestrate(context: LLMContext) -> IntelligenceResult

Dependency direction:
    orchestrator.py → stdlib
    orchestrator.py → intelligence.{config, exceptions, models, traces,
                                     prompt_builder, llm_client, reasoning_parser}
"""
from __future__ import annotations

import logging
import time
from typing import Any

from intelligence.config import IntelligenceConfig
from intelligence.exceptions import (
    IntelligenceDisabled,
    IntelligenceError,
    LLMRequestError,
    ReasoningParseError,
)
from intelligence.llm_client import LLMClient, build_llm_client
from intelligence.models import (
    ActionKind,
    ActionProposal,
    ClarificationDecision,
    ConfidenceLevel,
    CustomerReplyDraft,
    IntelligenceResult,
    LLMContext,
    ObservationDraft,
    ReasoningOutcome,
    ReasoningResult,
    RiskLevel,
)
from intelligence.prompt_builder import PromptBuilder
from intelligence.reasoning_parser import (
    parse_action_proposals,
    parse_clarification_decision,
    parse_customer_reply,
    parse_observation_draft,
    parse_reasoning_result,
)
from intelligence.traces import (
    TRACE_13_CONTEXT_BUILDER,
    TRACE_14_PROMPT_BUILDER,
    TRACE_15_HYBRID_RAG,
    TRACE_16_LLM_REQUEST,
    TRACE_17_LLM_RESPONSE,
    TRACE_18_REASONING_COMPLETE,
    TRACE_19_OBSERVATION_GENERATED,
    TRACE_20_CUSTOMER_REPLY_GENERATED,
    TRACE_21_ACTION_PROPOSAL,
    TRACE_22_PIPELINE_COMPLETE,
    emit_wave3_trace,
)

LOGGER = logging.getLogger("intelligence.orchestrator")


# ── Sprint 2.53 Wave 4A: production-safe runtime-boundary trace tags ─────────
# These are the 12 canonical inner ENTER_/EXIT_ shortname tags REQUIRED by
# the Wave 4A sprint spec. They complement (do not replace) the 22 numeric
# TRACE_XX tags — the shortname tags are grep-friendly for operators, while
# TRACE_XX are precise stage identifiers for tracing.
_ENTER_PROMPT              = "ENTER_PROMPT"
_EXIT_PROMPT               = "EXIT_PROMPT"
_ENTER_LLM                 = "ENTER_LLM"
_EXIT_LLM                  = "EXIT_LLM"
_ENTER_REASONING           = "ENTER_REASONING"
_EXIT_REASONING            = "EXIT_REASONING"
_ENTER_OBSERVATION         = "ENTER_OBSERVATION"
_EXIT_OBSERVATION          = "EXIT_OBSERVATION"
_ENTER_REPLY               = "ENTER_REPLY"
_EXIT_REPLY                = "EXIT_REPLY"
_ENTER_ACTION_PROPOSAL     = "ENTER_ACTION_PROPOSAL"
_EXIT_ACTION_PROPOSAL      = "EXIT_ACTION_PROPOSAL"


def _boundary_trace(tag: str, **fields: object) -> None:
    """Emit a runtime-boundary trace tag at WARNING level. Never raises."""
    try:
        parts = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        LOGGER.warning("%s %s", tag, parts)
    except Exception:
        pass


class IntelligenceOrchestrator:
    """
    Turn an `LLMContext` into a fully-populated `IntelligenceResult`.

    Never raises to the caller (except `IntelligenceDisabled` when the
    layer is turned off by config). All LLM / parse failures are folded
    into safe fallback drafts with `confidence=0.0` and `outcome=INSUFFICIENT_EVIDENCE`
    so downstream layers can still take a reasonable action (usually:
    escalate to a human).
    """

    def __init__(
        self,
        config: IntelligenceConfig | None = None,
        *,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._config = config or IntelligenceConfig.from_env()
        self._prompts = prompt_builder or PromptBuilder()
        # LLM client is lazy so tests can inject one AFTER construction.
        self._client: LLMClient | None = llm_client
        self._owns_client = llm_client is None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                LOGGER.debug("intelligence.orchestrator.close error=%s", exc)

    async def __aenter__(self) -> "IntelligenceOrchestrator":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ── Public API ───────────────────────────────────────────────────────

    async def orchestrate(self, context: LLMContext) -> IntelligenceResult:
        """
        Run the full intelligence pipeline over one LLMContext.

        Returns an `IntelligenceResult` — always. Never raises for
        recoverable errors.
        """
        if not self._config.enabled:
            raise IntelligenceDisabled(
                "Intelligence layer disabled via INTELLIGENCE_ENABLED=false"
            )

        started = time.monotonic()
        emit_wave3_trace(
            TRACE_13_CONTEXT_BUILDER,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="context_ready",
            duration_ms=0, status="OK",
        )
        emit_wave3_trace(
            TRACE_14_PROMPT_BUILDER,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="prompts_ready",
            duration_ms=0, status="OK",
        )
        emit_wave3_trace(
            TRACE_15_HYBRID_RAG,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="chunks_attached",
            duration_ms=0,
            status=f"{len(context.retrieved_chunks)}_chunks",
        )

        client = await self._ensure_client()

        # ── 1. Reasoning ───────────────────────────────────────────────
        reasoning = await self._reasoning_step(context, client)

        # ── 2. Clarification decision ─────────────────────────────────
        clarification = await self._clarification_step(context, client, reasoning)

        # ── 3. Observation (internal note) ────────────────────────────
        observation = await self._observation_step(context, client, reasoning)

        # ── 4. Customer reply (only when meaningful) ──────────────────
        customer_reply = await self._customer_reply_step(context, client, reasoning, clarification)

        # ── 5. Action proposals ───────────────────────────────────────
        proposals = await self._action_proposal_step(context, client, reasoning)

        duration_ms = int((time.monotonic() - started) * 1000)
        result = IntelligenceResult(
            case_id=          context.case_id,
            ticket_id=        context.ticket_id,
            reasoning=        reasoning,
            clarification=    clarification,
            observation=      observation,
            customer_reply=   customer_reply,
            action_proposals= proposals,
            llm_model=        client.model,
            duration_ms=      duration_ms,
            trace_id=         context.trace_id,
        )
        emit_wave3_trace(
            TRACE_22_PIPELINE_COMPLETE,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="intelligence",
            duration_ms=duration_ms,
            status=reasoning.outcome.value,
        )
        return result

    # ── Steps ────────────────────────────────────────────────────────────

    async def _ensure_client(self) -> LLMClient:
        if self._client is None:
            self._client = build_llm_client(self._config)
            self._owns_client = True
        return self._client

    async def _reasoning_step(
        self, context: LLMContext, client: LLMClient,
    ) -> ReasoningResult:
        _boundary_trace(_ENTER_REASONING, ticket_id=context.ticket_id, case_id=context.case_id)
        started = time.monotonic()
        _boundary_trace(_ENTER_PROMPT, stage="reasoning", ticket_id=context.ticket_id, case_id=context.case_id)
        pair = self._prompts.reasoning(context)
        _boundary_trace(_EXIT_PROMPT, stage="reasoning", ticket_id=context.ticket_id, case_id=context.case_id,
                        prompt_version=pair.prompt_version)
        raw, dur_ms = await self._call_llm(pair.system, pair.user,
                                           context=context,
                                           stage="reasoning")
        try:
            result = parse_reasoning_result(
                raw, prompt_version=pair.prompt_version, llm_model=client.model,
            )
        except ReasoningParseError as exc:
            result = _fallback_reasoning(client.model, pair.prompt_version, exc)
        emit_wave3_trace(
            TRACE_18_REASONING_COMPLETE,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="reasoning",
            duration_ms=dur_ms, status=result.outcome.value,
        )
        _boundary_trace(_EXIT_REASONING, ticket_id=context.ticket_id, case_id=context.case_id,
                        outcome=result.outcome.value,
                        confidence=f"{result.confidence:.2f}",
                        duration_ms=int((time.monotonic() - started) * 1000))
        return result

    async def _clarification_step(
        self, context: LLMContext, client: LLMClient, reasoning: ReasoningResult,
    ) -> ClarificationDecision:
        # Fast-path: if reasoning already says no clarification, don't spend a call.
        if not reasoning.clarification_required and \
           reasoning.confidence >= self._config.confidence_threshold and \
           reasoning.outcome != ReasoningOutcome.NEEDS_CLARIFICATION:
            return ClarificationDecision(
                should_clarify=False,
                questions=(),
                reason="reasoning confidence >= threshold",
            )
        # Include missing_information into a synthetic tool_result the prompt
        # can inspect (LLMContext is frozen, so we build a shallow copy).
        enriched = _with_tool_result(
            context,
            missing_information=list(reasoning.missing_information),
        )
        pair = self._prompts.clarification(enriched)
        raw, _dur = await self._call_llm(pair.system, pair.user,
                                         context=context,
                                         stage="clarification")
        try:
            return parse_clarification_decision(raw)
        except ReasoningParseError:
            return ClarificationDecision(
                should_clarify=True,
                questions=tuple(reasoning.missing_information[:3]) or
                          ("Could you please share more details about the issue?",),
                reason="clarification parse failed; fallback questions used",
            )

    async def _observation_step(
        self, context: LLMContext, client: LLMClient, reasoning: ReasoningResult,
    ) -> ObservationDraft:
        _boundary_trace(_ENTER_OBSERVATION, ticket_id=context.ticket_id, case_id=context.case_id)
        started = time.monotonic()
        _boundary_trace(_ENTER_PROMPT, stage="observation", ticket_id=context.ticket_id, case_id=context.case_id)
        pair = self._prompts.observation(context)
        _boundary_trace(_EXIT_PROMPT, stage="observation", ticket_id=context.ticket_id, case_id=context.case_id,
                        prompt_version=pair.prompt_version)
        raw, dur_ms = await self._call_llm(pair.system, pair.user,
                                           context=context,
                                           stage="observation")
        try:
            obs = parse_observation_draft(raw)
        except ReasoningParseError as exc:
            obs = _fallback_observation(reasoning, exc)
        emit_wave3_trace(
            TRACE_19_OBSERVATION_GENERATED,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="observation",
            duration_ms=dur_ms, status=obs.confidence_level.value,
        )
        _boundary_trace(_EXIT_OBSERVATION, ticket_id=context.ticket_id, case_id=context.case_id,
                        confidence_level=obs.confidence_level.value,
                        duration_ms=int((time.monotonic() - started) * 1000))
        return obs

    async def _customer_reply_step(
        self,
        context: LLMContext,
        client: LLMClient,
        reasoning: ReasoningResult,
        clarification: ClarificationDecision,
    ) -> CustomerReplyDraft | None:
        _boundary_trace(_ENTER_REPLY, ticket_id=context.ticket_id, case_id=context.case_id)
        started = time.monotonic()
        # Only draft a customer reply if we have SOMETHING useful to say.
        # Otherwise return None so the runtime posts a private draft note
        # (via the observation) instead.
        if reasoning.outcome == ReasoningOutcome.INSUFFICIENT_EVIDENCE and \
           not clarification.should_clarify:
            _boundary_trace(_EXIT_REPLY, ticket_id=context.ticket_id, case_id=context.case_id,
                            reply_kind="SKIPPED",
                            duration_ms=int((time.monotonic() - started) * 1000))
            return None

        _boundary_trace(_ENTER_PROMPT, stage="customer_reply", ticket_id=context.ticket_id, case_id=context.case_id)
        pair = self._prompts.customer_reply(context)
        _boundary_trace(_EXIT_PROMPT, stage="customer_reply", ticket_id=context.ticket_id, case_id=context.case_id,
                        prompt_version=pair.prompt_version)
        raw, dur_ms = await self._call_llm(pair.system, pair.user,
                                           context=context,
                                           stage="customer_reply")
        try:
            reply = parse_customer_reply(raw)
        except ReasoningParseError as exc:
            reply = _fallback_customer_reply(clarification, exc)
        emit_wave3_trace(
            TRACE_20_CUSTOMER_REPLY_GENERATED,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="customer_reply",
            duration_ms=dur_ms, status=reply.reply_kind,
        )
        _boundary_trace(_EXIT_REPLY, ticket_id=context.ticket_id, case_id=context.case_id,
                        reply_kind=reply.reply_kind,
                        confidence_level=reply.confidence_level.value,
                        duration_ms=int((time.monotonic() - started) * 1000))
        return reply

    async def _action_proposal_step(
        self, context: LLMContext, client: LLMClient, reasoning: ReasoningResult,
    ) -> tuple[ActionProposal, ...]:
        _boundary_trace(_ENTER_ACTION_PROPOSAL, ticket_id=context.ticket_id, case_id=context.case_id)
        started = time.monotonic()
        _boundary_trace(_ENTER_PROMPT, stage="action_proposal", ticket_id=context.ticket_id, case_id=context.case_id)
        pair = self._prompts.action_proposal(context)
        _boundary_trace(_EXIT_PROMPT, stage="action_proposal", ticket_id=context.ticket_id, case_id=context.case_id,
                        prompt_version=pair.prompt_version)
        raw, dur_ms = await self._call_llm(pair.system, pair.user,
                                           context=context,
                                           stage="action_proposal")
        try:
            proposals = parse_action_proposals(raw)
        except ReasoningParseError:
            proposals = _fallback_action_proposals(reasoning)
        emit_wave3_trace(
            TRACE_21_ACTION_PROPOSAL,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage="action_proposal",
            duration_ms=dur_ms, status=f"{len(proposals)}_proposals",
        )
        _boundary_trace(_EXIT_ACTION_PROPOSAL, ticket_id=context.ticket_id, case_id=context.case_id,
                        proposal_count=len(proposals),
                        duration_ms=int((time.monotonic() - started) * 1000))
        return proposals

    async def _call_llm(
        self, system: str, user: str, *,
        context: LLMContext, stage: str,
    ) -> tuple[str, int]:
        """Wrap `client.complete_json` with 16/17 traces and duration."""
        started = time.monotonic()
        _boundary_trace(_ENTER_LLM, stage=stage, ticket_id=context.ticket_id, case_id=context.case_id)
        emit_wave3_trace(
            TRACE_16_LLM_REQUEST,
            ticket_id=context.ticket_id, tenant=context.tenant_id,
            case_id=context.case_id, stage=stage,
            duration_ms=0, status="DISPATCHED",
        )
        try:
            raw = await self._client.complete_json(system=system, user=user)  # type: ignore[union-attr]
            dur_ms = int((time.monotonic() - started) * 1000)
            emit_wave3_trace(
                TRACE_17_LLM_RESPONSE,
                ticket_id=context.ticket_id, tenant=context.tenant_id,
                case_id=context.case_id, stage=stage,
                duration_ms=dur_ms, status="OK",
            )
            _boundary_trace(_EXIT_LLM, stage=stage, ticket_id=context.ticket_id, case_id=context.case_id,
                            status="OK", duration_ms=dur_ms)
            return raw, dur_ms
        except LLMRequestError as exc:
            dur_ms = int((time.monotonic() - started) * 1000)
            emit_wave3_trace(
                TRACE_17_LLM_RESPONSE,
                ticket_id=context.ticket_id, tenant=context.tenant_id,
                case_id=context.case_id, stage=stage,
                duration_ms=dur_ms, status=f"ERROR:{type(exc).__name__}",
            )
            _boundary_trace(_EXIT_LLM, stage=stage, ticket_id=context.ticket_id, case_id=context.case_id,
                            status=f"ERROR:{type(exc).__name__}", duration_ms=dur_ms)
            # Return a synthetic empty JSON payload — parsers will fall back.
            return "{}", dur_ms
        except IntelligenceError as exc:
            dur_ms = int((time.monotonic() - started) * 1000)
            emit_wave3_trace(
                TRACE_17_LLM_RESPONSE,
                ticket_id=context.ticket_id, tenant=context.tenant_id,
                case_id=context.case_id, stage=stage,
                duration_ms=dur_ms, status=f"ERROR:{type(exc).__name__}",
            )
            _boundary_trace(_EXIT_LLM, stage=stage, ticket_id=context.ticket_id, case_id=context.case_id,
                            status=f"ERROR:{type(exc).__name__}", duration_ms=dur_ms)
            return "{}", dur_ms


# ── Fallback helpers ────────────────────────────────────────────────────

def _fallback_reasoning(model: str, prompt_version: str,
                        exc: ReasoningParseError) -> ReasoningResult:
    return ReasoningResult(
        outcome=              ReasoningOutcome.INSUFFICIENT_EVIDENCE,
        summary=              "Automated reasoning could not be produced.",
        root_cause=           "unknown",
        confidence=           0.0,
        confidence_level=     ConfidenceLevel.LOW,
        evidence_used=        (),
        missing_information=  ("LLM response was unparseable",),
        clarification_required=False,
        reasoning_notes=      f"parse error: {str(exc)[:200]}",
        prompt_version=       prompt_version,
        llm_model=            model,
    )


def _fallback_observation(
    reasoning: ReasoningResult, exc: ReasoningParseError,
) -> ObservationDraft:
    body = (
        "<p><strong>KwikID AI — Observation (fallback)</strong></p>"
        "<p>The AI observation generator could not produce a valid response. "
        "Human review is required.</p>"
        f"<p><em>Reasoning outcome:</em> {reasoning.outcome.value}, "
        f"<em>confidence:</em> {reasoning.confidence:.2f}</p>"
    )
    return ObservationDraft(
        issue_summary=      reasoning.summary or "AI observation failed",
        evidence=           f"- reasoning outcome: {reasoning.outcome.value}",
        root_cause=         reasoning.root_cause or "unknown",
        recommended_action= "Human agent to review the ticket manually",
        escalation=         "Human Review",
        confidence_level=   ConfidenceLevel.LOW,
        body_html=          body,
    )


def _fallback_customer_reply(
    clarification: ClarificationDecision, exc: ReasoningParseError,
) -> CustomerReplyDraft:
    if clarification.should_clarify and clarification.questions:
        qs = "".join(f"<li>{q}</li>" for q in clarification.questions[:2])
        body = (
            "<p>Hi,</p>"
            "<p>Thank you for reaching out. To investigate this issue, "
            "we need a little more information:</p>"
            f"<ul>{qs}</ul>"
            "<p>Regards,<br>KwikID Support Team</p>"
        )
        return CustomerReplyDraft(
            reply_kind="clarification",
            body_html=body,
            confidence_level=ConfidenceLevel.LOW,
            confidence=0.4,
            citations=(),
        )
    body = (
        "<p>Hi,</p>"
        "<p>Thank you for reaching out. We are reviewing your request and "
        "will get back to you shortly.</p>"
        "<p>Regards,<br>KwikID Support Team</p>"
    )
    return CustomerReplyDraft(
        reply_kind="clarification",
        body_html=body,
        confidence_level=ConfidenceLevel.LOW,
        confidence=0.3,
        citations=(),
    )


def _fallback_action_proposals(reasoning: ReasoningResult) -> tuple[ActionProposal, ...]:
    if reasoning.outcome == ReasoningOutcome.ESCALATE:
        kind = ActionKind.ESCALATE_TO_ENGINEERING
        risk = RiskLevel.MEDIUM
        rationale = "reasoning outcome is ESCALATE"
    elif reasoning.clarification_required:
        kind = ActionKind.ASK_CLARIFICATION
        risk = RiskLevel.SAFE
        rationale = "reasoning requested clarification"
    else:
        kind = ActionKind.POST_INTERNAL_NOTE
        risk = RiskLevel.SAFE
        rationale = "no valid proposals parsed; default to internal note"
    return (ActionProposal(
        action_kind=kind,
        parameters={},
        confidence=max(reasoning.confidence, 0.3),
        risk=risk,
        approval_required=False,
        rationale=rationale,
    ),)


def _with_tool_result(context: LLMContext, **extras: Any) -> LLMContext:
    """Return a copy of `context` with additional entries in `tool_results`."""
    from dataclasses import replace
    merged = dict(context.tool_results)
    merged.update(extras)
    return replace(context, tool_results=merged)
