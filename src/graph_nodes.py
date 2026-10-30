"""
LangGraph node functions.

CRITICAL DESIGN CONSTRAINT (discovered by testing, not assumed): LangGraph
resumes a node by re-executing its ENTIRE function body from the top --
only interrupt() calls that were already resumed replay their cached
value; everything else (including real LLM calls) genuinely re-runs. A
single node containing an internal while-loop with multiple interrupt()
calls -- which agent_loop.py uses, and which works fine there because
it's driven by injected fake functions in tests -- causes every prior
LLM call in that loop to be REPEATED on every resume. Measured directly:
a 3-turn interview made 9 real LLM calls instead of 3, and this scales
roughly quadratically with interview length.

The fix: split each agent into TWO nodes.
  - "*_step" nodes do the expensive, non-deterministic work (the LLM
    call) and prepare a question if one is needed. They are never the
    node sitting at a currently-blocked interrupt(), so they only run
    once per real turn.
  - "*_ask_user" nodes do nothing but call interrupt() (and append the
    answer to persisted conversation state). Re-executing them on
    resume is nearly free -- there's no expensive work to repeat.

Anything a turn needs to remember across this two-node boundary lives in
persisted SessionState/InterviewState (added in state.py), not in local
Python variables, since LangGraph reconstructs the state object fresh on
every node call.

agent_loop.py's whole-loop functions are NOT used here and are not
deprecated either -- they remain valid, directly useful for unit-testing
turn-by-turn logic with injected fake functions (as already proven), just
not suitable as literal LangGraph nodes once real, expensive LLM calls
are involved.
"""

from __future__ import annotations
import logging
from typing import Callable, Type, TypeVar

from pydantic import BaseModel
from langgraph.types import interrupt

from state import (
    SessionState, InterviewState, DriverResolution, Phase, ComponentStatus,
)
from schemas import AgentTurnResponse, ComponentAgentResponse, ComponentProposalResponse, DriverUpdate
from ollama_client import call_ollama_structured, StructuredOutputError
from knowledge_loader import load_knowledge_file, parse_knowledge_file, extract_decision_drivers
from prompts import (
    build_category_messages, build_opening_user_text, build_stall_message,
    build_revalidation_messages, REVALIDATION_OPENING_TEXT,
    build_component_messages, COMPONENT_OPENING_TEXT,
)
from merge import (
    apply_driver_updates, record_question_attempt, is_stalled, all_drivers_resolved,
    apply_narrowed_section, commit_category_decision, propagate_dependents,
    get_active_decision, apply_component_proposal, apply_component_edits,
)
from queries import gather_upstream_decisions, gather_sibling_decisions
from agent_loop import (
    _call_with_validation, AgentUnrecoverableError,
    MAX_CONSECUTIVE_STRUCTURED_FAILURES, STALL_SKIP_PHRASES, TECHNICAL_FAILURE_PROMPT,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _default_llm_call(
    messages: list[dict], schema: Type[T], model: str,
    host: str = "http://localhost:11434", temperature: float = 0.1, max_retries: int = 3,
) -> T:
    return call_ollama_structured(messages, schema=schema, model=model, host=host,
                                   temperature=temperature, max_retries=max_retries)


# ---------- Category Agent: two nodes ----------

def start_interview(state: SessionState, category: str, component_id: str | None,
                     mode: str = "initial", trigger_detail: str | None = None) -> None:
    """Initializes state.current_interview for a new task. Called by schedule_next_node."""
    driver_names = extract_decision_drivers(load_knowledge_file(category))
    interview = InterviewState(
        category=category, component_id=component_id, mode=mode, trigger_detail=trigger_detail,
        driver_checklist=[DriverResolution(driver_name=d) for d in driver_names],
    )
    interview.conversation = [{
        "role": "user",
        "content": REVALIDATION_OPENING_TEXT if mode == "revalidate" else build_opening_user_text(state.project_goal),
    }]
    state.current_interview = interview


def category_agent_step(state: SessionState, yaml_config: dict, llm_call_fn) -> dict:
    """
    Does exactly ONE LLM call for the in-progress interview at
    state.current_interview, then either commits (clearing
    current_interview) or appends a question to the conversation for
    category_ask_user_node to pose next. Never calls interrupt() itself.
    """
    interview = state.current_interview
    assert interview is not None, "category_agent_step called with no active interview"
    category, component_id, mode = interview.category, interview.component_id, interview.mode

    # A stall-skip resolution from the user's last answer, if applicable,
    # is handled BEFORE the (single) LLM call this turn -- not as a
    # separate loop iteration, keeping this node's expensive work to
    # exactly one call.
    if interview.stalled_driver and interview.conversation and interview.conversation[-1]["role"] == "user":
        last_answer = interview.conversation[-1]["content"].strip().lower()
        if last_answer in STALL_SKIP_PHRASES:
            apply_driver_updates(interview, [DriverUpdate(
                driver_name=interview.stalled_driver, status="not_relevant",
                value="User asked to skip; agent should use best reasonable judgment.",
            )])
        interview.stalled_driver = None

    knowledge_text = load_knowledge_file(category)
    parsed = parse_knowledge_file(knowledge_text)
    component_name = state.components[component_id].name if component_id else None
    upstream_decisions = gather_upstream_decisions(state, category, component_id, yaml_config)
    sibling_decisions = gather_sibling_decisions(state, category, component_id)

    if mode == "revalidate":
        previous_decision = get_active_decision(state, category, component_id)
        messages = build_revalidation_messages(
            category, parsed, interview.narrowed_section, interview.driver_checklist,
            upstream_decisions, sibling_decisions, component_name,
            previous_decision=previous_decision, trigger_detail=interview.trigger_detail,
            conversation_so_far=interview.conversation,
        )
    else:
        messages = build_category_messages(
            category, parsed, interview.narrowed_section, interview.driver_checklist,
            upstream_decisions, sibling_decisions, component_name,
            conversation_so_far=interview.conversation,
        )

    def validate(response: AgentTurnResponse) -> str | None:
        if response.action == "commit_decision" and not all_drivers_resolved(interview):
            unresolved = [d.driver_name for d in interview.driver_checklist if d.status == "unresolved"]
            return (
                f"You cannot commit yet -- these drivers are still unresolved: {', '.join(unresolved)}. "
                "Ask about one of them, or mark it 'not_relevant' with justification if it genuinely doesn't apply."
            )
        return None

    def apply_response(response: AgentTurnResponse) -> None:
        apply_driver_updates(interview, response.driver_updates)
        apply_narrowed_section(interview, response.narrowed_section)

    try:
        response = _call_with_validation(llm_call_fn, messages, AgentTurnResponse, validate, apply_fn=apply_response)
        interview.consecutive_structured_failures = 0
    except StructuredOutputError as e:
        interview.consecutive_structured_failures += 1
        logger.error(
            "Structured output failure %d/%d for %s/%s: %s",
            interview.consecutive_structured_failures, MAX_CONSECUTIVE_STRUCTURED_FAILURES,
            category, component_id, e,
        )
        if interview.consecutive_structured_failures >= MAX_CONSECUTIVE_STRUCTURED_FAILURES:
            raise AgentUnrecoverableError(
                f"Failed to get valid output after {interview.consecutive_structured_failures} "
                "consecutive attempts for this decision.",
                category=category, component_id=component_id,
            ) from e
        interview.conversation.append({"role": "assistant", "content": TECHNICAL_FAILURE_PROMPT})
        return state.model_dump()

    if response.action == "commit_decision":
        changed = commit_category_decision(state, response, category, component_id)  # clears current_interview
        if changed:
            trigger_detail = (
                f"{category} changed to '{response.decision}'"
                + (f" for component '{component_name}'" if component_name else "")
            )
            propagate_dependents(state, category, component_id, yaml_config, trigger_detail)
        return state.model_dump()

    # action == "ask_question"
    interview.conversation.append({"role": "assistant", "content": response.question_text})
    record_question_attempt(interview, response.targeted_driver)
    if is_stalled(interview, response.targeted_driver):
        interview.stalled_driver = response.targeted_driver
        interview.conversation[-1] = {"role": "assistant", "content": build_stall_message(response.targeted_driver)}
    return state.model_dump()


def category_ask_user_node(state: SessionState) -> dict:
    """
    Does NOTHING except call interrupt() and record the answer. No
    expensive/non-deterministic work happens here, so re-executing this
    node on resume (which does happen) costs nothing real.
    """
    interview = state.current_interview
    assert interview is not None and interview.conversation and interview.conversation[-1]["role"] == "assistant"
    question = interview.conversation[-1]["content"]
    answer = interrupt(question)
    interview.conversation.append({"role": "user", "content": answer})
    return state.model_dump()


# ---------- Component Agent: two nodes ----------

def component_agent_step(state: SessionState, yaml_config: dict, llm_call_fn) -> dict:
    if not state.component_agent_conversation:
        state.component_agent_conversation = [{"role": "user", "content": COMPONENT_OPENING_TEXT}]

    bootstrap = not state.components

    if bootstrap:
        # No components exist yet, so this turn can only ever be a
        # proposal -- that's already decided here in Python, not left
        # for the model to choose. Using a dedicated schema
        # (ComponentProposalResponse, with `components` required+non-empty
        # at the JSON-schema level) instead of the combined
        # ComponentAgentResponse fixes a real failure mode observed with
        # smaller local models: an optional `components` field (default
        # empty list, only enforced non-empty by a post-hoc validator)
        # gave the model no strong signal it had to be filled, so the
        # model wrote its whole proposal into message_to_user prose and
        # left components empty -- failing validation identically on
        # every retry, since the corrective message didn't change that
        # underlying signal.
        messages = build_component_messages(state.project_goal, state.component_agent_conversation, bootstrap=True)
        try:
            response = llm_call_fn(messages, ComponentProposalResponse)
        except StructuredOutputError as e:
            logger.error("Component agent structured output failure: %s", e)
            state.component_agent_conversation.append({"role": "assistant", "content": TECHNICAL_FAILURE_PROMPT})
            return state.model_dump()

        state.component_agent_conversation.append({"role": "assistant", "content": response.message_to_user})
        apply_component_proposal(state, response.components)
        return state.model_dump()

    def validate(response: ComponentAgentResponse) -> str | None:
        return None  # schema's own model_validator already enforces the conditional requirement

    messages = build_component_messages(state.project_goal, state.component_agent_conversation, bootstrap=False)

    try:
        response = _call_with_validation(llm_call_fn, messages, ComponentAgentResponse, validate)
    except StructuredOutputError as e:
        logger.error("Component agent structured output failure: %s", e)
        state.component_agent_conversation.append({"role": "assistant", "content": TECHNICAL_FAILURE_PROMPT})
        return state.model_dump()

    state.component_agent_conversation.append({"role": "assistant", "content": response.message_to_user})

    if response.action == "revise":
        apply_component_edits(state, response.edits, yaml_config)
    elif response.action == "lock":
        state.phase = Phase.CATEGORY_INTERVIEWS
        state.component_agent_conversation = []

    return state.model_dump()


def component_ask_user_node(state: SessionState) -> dict:
    assert state.component_agent_conversation and state.component_agent_conversation[-1]["role"] == "assistant"
    question = state.component_agent_conversation[-1]["content"]
    answer = interrupt(question)
    state.component_agent_conversation.append({"role": "user", "content": answer})
    return state.model_dump()


# ---------- Routing predicates ----------

def route_after_component_step(state: SessionState) -> str:
    return "schedule_next" if state.phase == Phase.CATEGORY_INTERVIEWS else "component_ask_user"


def route_after_category_step(state: SessionState) -> str:
    return "schedule_next" if state.current_interview is None else "category_ask_user"
