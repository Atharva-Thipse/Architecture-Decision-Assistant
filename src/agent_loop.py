"""
The actual agent loops: run_category_interview() and
run_component_agent(). Each runs ONE interview to completion (looping
internally) and returns updated SessionState.

Design choices that resolve integration questions the earlier
pseudocode left open:

- `ask_user_fn: Callable[[str], str]` is injected rather than hardcoded
  to input()/interrupt(). In a real LangGraph node this would be
  `lambda prompt: interrupt(prompt)`; in tests below it's a scripted
  fake. This is what makes it possible to test a full multi-turn
  interview without a live model or terminal.

- `llm_call_fn: Callable[[list[dict], Type[BaseModel]], BaseModel]` is
  similarly injected, defaulting to call_ollama_structured. Tests below
  supply a scripted sequence of AgentTurnResponse/ComponentAgentResponse
  objects instead.

- A full interview (all its internal back-and-forth) runs inside ONE
  call to run_category_interview() / run_component_agent(). This maps
  directly onto the idiomatic LangGraph HITL pattern: a single node
  containing an internal loop with multiple interrupt() calls, where
  already-resumed interrupts replay their cached value and only the
  currently-pending one actually pauses the graph.

- Two DIFFERENT kinds of retry exist and are kept separate:
  call_ollama_structured's retry (malformed JSON / schema violation --
  a technical failure) vs _call_with_validation's retry here (valid,
  schema-conformant output that still breaks a business rule, e.g.
  committing before every driver is resolved). Conflating these would
  mean using the same recovery strategy for "the model didn't follow
  the JSON format" and "the model followed the format perfectly but
  isn't allowed to do this yet" -- different problems.
"""

from __future__ import annotations
import logging
from typing import Callable, Type, TypeVar

from pydantic import BaseModel

from state import (
    SessionState, InterviewState, DriverResolution, ComponentStatus, Phase,
    PendingRevalidation,
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

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_BUSINESS_RULE_RETRIES = 3
MAX_CONSECUTIVE_STRUCTURED_FAILURES = 3
STALL_SKIP_PHRASES = {"skip", "move on", "your best guess", "guess", "skip it"}

TECHNICAL_FAILURE_PROMPT = "I'm having trouble processing that internally -- could you rephrase your last answer?"


class AgentUnrecoverableError(Exception):
    """
    Raised when call_ollama_structured has failed MAX_CONSECUTIVE_STRUCTURED_FAILURES
    times in a row within a single interview, even after asking the user
    to rephrase each time. This is a DIFFERENT failure mode from a
    business-rule retry or an occasional malformed-JSON hiccup: it
    signals something is likely wrong with the model/connection itself
    (wrong model configured, server down, etc.), not with the content of
    the conversation. Without this cap, a genuinely broken connection
    would loop forever asking the user to rephrase an answer that was
    never the problem in the first place -- confirmed by testing this
    path directly (see agent_loop tests). Whatever calls
    run_category_interview / run_component_agent (the future
    Orchestrator) should catch this and surface it as an infrastructure
    problem, not a normal conversational turn.
    """
    def __init__(self, message: str, category: str | None = None, component_id: str | None = None):
        super().__init__(message)
        self.category = category
        self.component_id = component_id


def _call_with_validation(
    llm_call_fn: Callable[[list[dict], Type[T]], T],
    messages: list[dict],
    schema: Type[T],
    validate_fn: Callable[[T], str | None],
    apply_fn: Callable[[T], None] | None = None,
) -> T:
    """
    Calls llm_call_fn, applies `apply_fn` (if given) as a side effect on
    the response, THEN checks validate_fn -- in that order. This
    ordering matters: for the category agent, a response can resolve
    its last remaining driver via `driver_updates` in the SAME turn it
    commits. If validation ran before those updates were merged into
    interview state, a perfectly valid "resolve the last driver and
    commit" turn would be incorrectly rejected as premature. apply_fn
    runs on every attempt (including ones that fail validation) since
    a rejected attempt's driver_updates are still real information and
    should not be discarded.

    If validate_fn flags a business-rule violation (returns a non-None
    error string) after apply_fn runs, the error is fed back and
    retried. If retries are exhausted, proceeds with the last response
    anyway rather than crashing -- logged loudly, since this means an
    interview may commit with an incomplete checklist, which a human
    reviewing the decision log should be able to notice.
    """
    local_messages = list(messages)
    response: T | None = None
    for attempt in range(1, MAX_BUSINESS_RULE_RETRIES + 1):
        response = llm_call_fn(local_messages, schema)
        if apply_fn is not None:
            apply_fn(response)
        error = validate_fn(response)
        if error is None:
            return response
        logger.warning("Business-rule validation failed (attempt %d/%d): %s", attempt, MAX_BUSINESS_RULE_RETRIES, error)
        if attempt < MAX_BUSINESS_RULE_RETRIES:
            local_messages = local_messages + [
                {"role": "assistant", "content": response.model_dump_json()},
                {"role": "user", "content": f"That response isn't valid: {error}. Please correct it."},
            ]
    logger.warning("Exceeded business-rule retries; proceeding with last response despite unresolved validation error.")
    return response


def _call_with_recovery(
    llm_call_fn: Callable[[list[dict], Type[T]], T],
    build_messages_fn: Callable[[], list[dict]],
    schema: Type[T],
    validate_fn: Callable[[T], str | None],
    apply_fn: Callable[[T], None] | None,
    ask_user_fn: Callable[[str], str],
    conversation: list[dict],
    technical_failure_prompt: str = TECHNICAL_FAILURE_PROMPT,
    error_context: tuple[str | None, str | None] = (None, None),
) -> T:
    """
    Wraps _call_with_validation with recovery from StructuredOutputError
    (malformed/non-schema-conformant model output -- a technical
    failure, distinct from the business-rule retries _call_with_validation
    already handles). On failure, asks the user to rephrase and retries;
    but caps consecutive failures at MAX_CONSECUTIVE_STRUCTURED_FAILURES
    and raises AgentUnrecoverableError beyond that, rather than looping
    forever. `build_messages_fn` is called fresh on every attempt so it
    picks up the user's rephrased answer once appended to `conversation`.
    """
    consecutive_failures = 0
    while True:
        try:
            return _call_with_validation(llm_call_fn, build_messages_fn(), schema, validate_fn, apply_fn)
        except StructuredOutputError as e:
            consecutive_failures += 1
            logger.error(
                "Structured output failure %d/%d: %s",
                consecutive_failures, MAX_CONSECUTIVE_STRUCTURED_FAILURES, e,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_STRUCTURED_FAILURES:
                category, component_id = error_context
                raise AgentUnrecoverableError(
                    f"Failed to get valid output after {consecutive_failures} consecutive attempts "
                    "-- this looks like a problem with the model or connection, not the conversation.",
                    category=category, component_id=component_id,
                ) from e
            user_input = ask_user_fn(technical_failure_prompt)
            conversation.append({"role": "user", "content": user_input})


# ---------- Category Agent ----------

def _default_llm_call(messages: list[dict], schema: Type[T], model: str) -> T:
    return call_ollama_structured(messages, schema=schema, model=model)


def run_category_interview(
    state: SessionState,
    category: str,
    component_id: str | None,
    yaml_config: dict,
    ask_user_fn: Callable[[str], str],
    llm_call_fn: Callable[[list[dict], Type[BaseModel]], BaseModel] | None = None,
    ollama_model: str = "llama3.1",
    mode: str = "initial",              # "initial" | "revalidate"
    trigger: PendingRevalidation | None = None,
) -> SessionState:
    if llm_call_fn is None:
        llm_call_fn = lambda msgs, schema: _default_llm_call(msgs, schema, ollama_model)

    knowledge_text = load_knowledge_file(category)
    parsed = parse_knowledge_file(knowledge_text)
    driver_names = extract_decision_drivers(knowledge_text)

    component_name = state.components[component_id].name if component_id else None
    upstream_decisions = gather_upstream_decisions(state, category, component_id, yaml_config)
    sibling_decisions = gather_sibling_decisions(state, category, component_id)

    interview = InterviewState(
        category=category, component_id=component_id,
        driver_checklist=[DriverResolution(driver_name=d) for d in driver_names],
    )
    state.current_interview = interview

    previous_decision = get_active_decision(state, category, component_id) if mode == "revalidate" else None
    if mode == "revalidate":
        assert trigger is not None and previous_decision is not None
        conversation: list[dict] = [{"role": "user", "content": REVALIDATION_OPENING_TEXT}]
    else:
        conversation = [{"role": "user", "content": build_opening_user_text(state.project_goal)}]

    def build_messages() -> list[dict]:
        if mode == "revalidate":
            return build_revalidation_messages(
                category, parsed, interview.narrowed_section, interview.driver_checklist,
                upstream_decisions, sibling_decisions, component_name,
                previous_decision=previous_decision, trigger_detail=trigger.trigger_detail,
                conversation_so_far=conversation,
            )
        return build_category_messages(
            category, parsed, interview.narrowed_section, interview.driver_checklist,
            upstream_decisions, sibling_decisions, component_name,
            conversation_so_far=conversation,
        )

    def apply_response(response: AgentTurnResponse) -> None:
        apply_driver_updates(interview, response.driver_updates)
        apply_narrowed_section(interview, response.narrowed_section)

    def validate(response: AgentTurnResponse) -> str | None:
        if response.action == "commit_decision" and not all_drivers_resolved(interview):
            unresolved = [d.driver_name for d in interview.driver_checklist if d.status == "unresolved"]
            return (
                f"You cannot commit yet -- these drivers are still unresolved: {', '.join(unresolved)}. "
                "Ask about one of them, or mark it 'not_relevant' with justification if it genuinely doesn't apply."
            )
        return None

    while True:
        response: AgentTurnResponse = _call_with_recovery(
            llm_call_fn, build_messages, AgentTurnResponse, validate, apply_response,
            ask_user_fn, conversation, error_context=(category, component_id),
        )

        if response.action == "commit_decision":
            conversation.append({"role": "assistant", "content": f"[committed: {response.decision}]"})
            changed = commit_category_decision(state, response, category, component_id)
            if changed:
                trigger_detail = (
                    f"{category} changed to '{response.decision}'"
                    + (f" for component '{component_name}'" if component_name else "")
                )
                propagate_dependents(state, category, component_id, yaml_config, trigger_detail)
            return state

        # action == "ask_question"
        conversation.append({"role": "assistant", "content": response.question_text})
        record_question_attempt(interview, response.targeted_driver)

        if is_stalled(interview, response.targeted_driver):
            user_input = ask_user_fn(build_stall_message(response.targeted_driver))
            if user_input.strip().lower() in STALL_SKIP_PHRASES:
                apply_driver_updates(interview, [
                    DriverUpdate(
                        driver_name=response.targeted_driver, status="not_relevant",
                        value="User asked to skip; agent should use best reasonable judgment.",
                    )
                ])
                conversation.append({"role": "user", "content": user_input})
                continue

        else:
            user_input = ask_user_fn(response.question_text)

        conversation.append({"role": "user", "content": user_input})


# ---------- Component Agent ----------

def run_component_agent(
    state: SessionState,
    yaml_config: dict,
    ask_user_fn: Callable[[str], str],
    llm_call_fn: Callable[[list[dict], Type[BaseModel]], BaseModel] | None = None,
    ollama_model: str = "llama3.1",
) -> SessionState:
    if llm_call_fn is None:
        llm_call_fn = lambda msgs, schema: _default_llm_call(msgs, schema, ollama_model)

    conversation: list[dict] = [{"role": "user", "content": COMPONENT_OPENING_TEXT}]

    # Bootstrap turn: no components exist yet, so this can only be a
    # proposal -- decided here in Python, same reasoning (and same
    # ComponentProposalResponse schema, with `components` required+
    # non-empty at the JSON-schema level) as graph_nodes.py's
    # component_agent_step. See ComponentProposalResponse's docstring
    # for why the old combined schema was unreliable here.
    response: ComponentProposalResponse = llm_call_fn(
        build_component_messages(state.project_goal, conversation, bootstrap=True),
        ComponentProposalResponse,
    )
    conversation.append({"role": "assistant", "content": response.message_to_user})
    apply_component_proposal(state, response.components)
    user_input = ask_user_fn(response.message_to_user)
    conversation.append({"role": "user", "content": user_input})

    def validate(response: ComponentAgentResponse) -> str | None:
        return None  # schema's own model_validator already enforces the conditional requirement

    while True:
        ongoing_response: ComponentAgentResponse = _call_with_recovery(
            llm_call_fn,
            lambda: build_component_messages(state.project_goal, conversation, bootstrap=False),
            ComponentAgentResponse, validate, None,
            ask_user_fn, conversation, error_context=(None, None),
        )

        conversation.append({"role": "assistant", "content": ongoing_response.message_to_user})

        if ongoing_response.action == "revise":
            apply_component_edits(state, ongoing_response.edits, yaml_config)
        elif ongoing_response.action == "lock":
            state.phase = Phase.CATEGORY_INTERVIEWS
            return state

        user_input = ask_user_fn(ongoing_response.message_to_user)
        conversation.append({"role": "user", "content": user_input})
