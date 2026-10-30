"""
The Orchestrator: deterministic scheduling logic (walks
category_dependencies.yaml, drains the revalidation queue) plus the
full graph assembly. This is the "deterministic controller, not an LLM
supervisor" design locked in earlier -- routing decisions here are plain
Python reading state and static YAML config, never an LLM call.

The whole multi-phase workflow (components -> category interviews ->
review -> report generation) is ONE continuous LangGraph execution from
START to END, not separate re-invocations per phase. A session may pause
at interrupt() many times along the way (once per question asked), but
it's a single graph run from the framework's perspective, which is what
lets the checkpointer persist and resume a session at any point.
"""

from __future__ import annotations
import logging
from typing import Callable, Type, TypeVar

from pydantic import BaseModel
from langgraph.graph import StateGraph, END, START
from langgraph.types import interrupt
from langgraph.checkpoint.base import BaseCheckpointSaver

from state import SessionState, Phase, ComponentStatus, DecisionStatus
from schemas import ReviewRevisionResponse
from merge import component_has_active_decision, enqueue_revalidation
from text_matching import fuzzy_match
from ollama_client import StructuredOutputError
from graph_nodes import (
    category_agent_step, category_ask_user_node, route_after_category_step,
    component_agent_step, component_ask_user_node, route_after_component_step,
    start_interview, _default_llm_call,
)
from agent_loop import _call_with_validation
from prompts import build_review_interpretation_messages
from reports import write_reports

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


# ---------- Scheduling (the deterministic part) ----------

def get_next_scheduled_interview(state: SessionState, yaml_config: dict) -> tuple[str, str | None] | None:
    """
    Walks yaml_config['recommended_order'] category-major: for each
    category in order, find the first component (if per-component
    scope) or the system-wide slot (if system scope) that doesn't yet
    have an active decision. Returns (category, component_id) for the
    first gap found, or None if everything is decided.
    """
    for category in yaml_config["recommended_order"]:
        scope = yaml_config["categories"][category]["scope"]
        if scope == "system":
            if not component_has_active_decision(state, category, None):
                return (category, None)
        else:  # per_component
            for cid, component in state.components.items():
                if component.status != ComponentStatus.ACTIVE:
                    continue
                if not component_has_active_decision(state, category, cid):
                    return (category, cid)
    return None


def schedule_next_node(state: SessionState, yaml_config: dict) -> dict:
    """
    Drains the revalidation queue first (settling downstream
    consequences of a change before starting anything new), then walks
    the dependency order for the next undecided task. If nothing is
    left either way, transitions to REVIEWING.
    """
    if state.revalidation_queue:
        item = state.revalidation_queue.pop(0)
        start_interview(state, item.category, item.component_id, mode="revalidate", trigger_detail=item.trigger_detail)
        return state.model_dump()

    task = get_next_scheduled_interview(state, yaml_config)
    if task is None:
        state.phase = Phase.REVIEWING
        return state.model_dump()

    category, component_id = task
    start_interview(state, category, component_id, mode="initial", trigger_detail=None)
    return state.model_dump()


def route_after_schedule_next(state: SessionState) -> str:
    return "review" if state.phase == Phase.REVIEWING else "category_agent_step"


# ---------- Review ----------

APPROVAL_PHRASES = {"approve", "approved", "looks good", "lgtm", "yes", "confirm", "confirmed", "ship it"}


def format_decision_log_summary(state: SessionState) -> str:
    lines = ["Here's the full decision log:\n"]
    for key, decision_id in sorted(state.active_decision_index.items()):
        d = state.decisions[decision_id]
        scope = f"component '{state.components[d.component_id].name}'" if d.component_id else "system-wide"
        lines.append(f"- [{d.category}] ({scope}): {d.decision} -- {d.rationale}")
    lines.append("\nApprove this to generate the final report, or tell me what you'd like to change.")
    return "\n".join(lines)


def review_node(state: SessionState) -> dict:
    """
    Presents the full decision log and waits for a response. Contains
    no LLM call itself -- interpreting a non-approval answer needs one,
    which is why that work lives in the separate interpret_revision_node
    below rather than here (same two-node reasoning as the category/
    component agents: the node sitting at interrupt() must stay cheap).
    """
    prompt = format_decision_log_summary(state)
    if state.review_clarification_note:
        prompt = state.review_clarification_note + "\n\n" + prompt
        state.review_clarification_note = None

    answer = interrupt(prompt)
    if answer.strip().lower() in APPROVAL_PHRASES:
        state.phase = Phase.GENERATING_REPORTS
    else:
        state.pending_review_answer = answer
    return state.model_dump()


def route_after_review(state: SessionState) -> str:
    if state.phase == Phase.GENERATING_REPORTS:
        return "generate_reports"
    if state.pending_review_answer:
        return "interpret_revision"
    return "review"  # shouldn't normally be reached; safe fallback


def interpret_revision_node(state: SessionState, yaml_config: dict, llm_call_fn) -> dict:
    """
    Interprets a non-approval review answer into a specific
    (category, component_id) to reopen, via one LLM call, then enqueues
    it through the SAME revalidation mechanism used for upstream-change
    and component-edit triggers -- a third trigger type
    ("user_requested_revision"), same queue, same downstream handling.
    Falls back to asking a clarifying question (via review_node, not
    here) rather than guessing when it can't confidently resolve the
    target -- an incorrect guess would silently reopen the wrong
    decision.
    """
    user_text = state.pending_review_answer
    state.pending_review_answer = None

    category_keys = list(yaml_config["categories"].keys())
    active_components = {cid: c for cid, c in state.components.items() if c.status == ComponentStatus.ACTIVE}
    component_names = [c.name for c in active_components.values()]

    messages = build_review_interpretation_messages(user_text, category_keys, component_names)

    def validate(response: ReviewRevisionResponse) -> str | None:
        return None  # schema's own model_validator already enforces the conditional requirements

    try:
        response: ReviewRevisionResponse = _call_with_validation(
            llm_call_fn, messages, ReviewRevisionResponse, validate
        )
    except StructuredOutputError:
        state.review_clarification_note = (
            "I had trouble understanding that -- could you name the specific decision "
            "you'd like to revisit?"
        )
        return state.model_dump()

    if not response.understood:
        state.review_clarification_note = response.clarifying_question
        return state.model_dump()

    matched_category = fuzzy_match(response.category_hint, category_keys, context="review category")
    if matched_category is None:
        state.review_clarification_note = (
            f"I couldn't match \"{response.category_hint}\" to a known decision category. "
            f"Could you name it differently? (Options: {', '.join(category_keys)})"
        )
        return state.model_dump()

    scope = yaml_config["categories"][matched_category]["scope"]
    target_component_id: str | None = None

    if scope == "per_component":
        if not response.component_hint:
            state.review_clarification_note = (
                f"Which component's {matched_category} decision would you like to revisit? "
                f"({', '.join(component_names)})"
            )
            return state.model_dump()
        matched_name = fuzzy_match(response.component_hint, component_names, context="review component")
        if matched_name is None:
            state.review_clarification_note = (
                f"I couldn't match \"{response.component_hint}\" to a known component. "
                f"({', '.join(component_names)})"
            )
            return state.model_dump()
        target_component_id = next(cid for cid, c in active_components.items() if c.name == matched_name)

    if not component_has_active_decision(state, matched_category, target_component_id):
        where = f" for component '{active_components[target_component_id].name}'" if target_component_id else ""
        state.review_clarification_note = f"There's no existing {matched_category} decision{where} to revise."
        return state.model_dump()

    enqueue_revalidation(
        state, matched_category, target_component_id,
        trigger="user_requested_revision", trigger_detail=f"User requested change: {response.revision_note}",
    )
    state.phase = Phase.CATEGORY_INTERVIEWS
    return state.model_dump()


def route_after_interpret_revision(state: SessionState) -> str:
    return "schedule_next" if state.phase == Phase.CATEGORY_INTERVIEWS else "review"


# ---------- Report generation ----------

def generate_reports_node(state: SessionState, output_dir: str) -> dict:
    """
    Renders and writes the final decision log (Architecture_Decisions.md
    + decisions.json) to output_dir, then marks the session DONE.
    """
    md_path, json_path = write_reports(state, output_dir)
    logger.info("Wrote decision log to %s and %s", md_path, json_path)
    state.phase = Phase.DONE
    return state.model_dump()


# ---------- Graph assembly ----------

def build_graph(
    yaml_config: dict,
    checkpointer: BaseCheckpointSaver,
    ollama_model: str = "llama3.1",
    ollama_host: str = "http://localhost:11434",
    temperature: float = 0.1,
    max_retries: int = 3,
    llm_call_fn: Callable[[list[dict], Type[T]], T] | None = None,
    output_dir: str = "./output",
):
    """
    Builds and compiles the full orchestrator graph. `llm_call_fn` can
    be overridden (as the tests below do) to avoid needing a live Ollama
    server; defaults to a real call_ollama_structured-backed function
    using ollama_model/ollama_host/temperature/max_retries.
    """
    if llm_call_fn is None:
        llm_call_fn = lambda msgs, schema: _default_llm_call(
            msgs, schema, ollama_model, ollama_host, temperature, max_retries
        )

    def _component_step(state: SessionState) -> dict:
        return component_agent_step(state, yaml_config, llm_call_fn)

    def _category_step(state: SessionState) -> dict:
        return category_agent_step(state, yaml_config, llm_call_fn)

    def _schedule_next(state: SessionState) -> dict:
        return schedule_next_node(state, yaml_config)

    def _interpret_revision(state: SessionState) -> dict:
        return interpret_revision_node(state, yaml_config, llm_call_fn)

    def _generate_reports(state: SessionState) -> dict:
        return generate_reports_node(state, output_dir)

    graph = StateGraph(SessionState)
    graph.add_node("component_agent_step", _component_step)
    graph.add_node("component_ask_user", component_ask_user_node)
    graph.add_node("schedule_next", _schedule_next)
    graph.add_node("category_agent_step", _category_step)
    graph.add_node("category_ask_user", category_ask_user_node)
    graph.add_node("review", review_node)
    graph.add_node("interpret_revision", _interpret_revision)
    graph.add_node("generate_reports", _generate_reports)

    graph.add_edge(START, "component_agent_step")
    graph.add_conditional_edges(
        "component_agent_step", route_after_component_step,
        {"schedule_next": "schedule_next", "component_ask_user": "component_ask_user"},
    )
    graph.add_edge("component_ask_user", "component_agent_step")

    graph.add_conditional_edges(
        "schedule_next", route_after_schedule_next,
        {"review": "review", "category_agent_step": "category_agent_step"},
    )
    graph.add_conditional_edges(
        "category_agent_step", route_after_category_step,
        {"schedule_next": "schedule_next", "category_ask_user": "category_ask_user"},
    )
    graph.add_edge("category_ask_user", "category_agent_step")

    graph.add_conditional_edges(
        "review", route_after_review,
        {"review": "review", "interpret_revision": "interpret_revision", "generate_reports": "generate_reports"},
    )
    graph.add_conditional_edges(
        "interpret_revision", route_after_interpret_revision,
        {"schedule_next": "schedule_next", "review": "review"},
    )
    graph.add_edge("generate_reports", END)

    return graph.compile(checkpointer=checkpointer)
