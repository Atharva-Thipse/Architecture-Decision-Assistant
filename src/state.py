"""
Session state schema for the architecture-decision assistant.

This is the shared "blackboard" state that flows through the LangGraph
graph. The Orchestrator (deterministic controller) reads and writes it;
category agents and the Component Agent only see the slice of it they
need via the context the Orchestrator prepares for them.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

# ---------- Enums ----------

class ComponentStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"

class DecisionStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"

class DriverStatus(str, Enum):
    UNRESOLVED = "unresolved"
    ANSWERED = "answered"
    INFERRED = "inferred"
    NOT_RELEVANT = "not_relevant"

class Phase(str, Enum):
    GATHERING_COMPONENTS = "gathering_components"
    CATEGORY_INTERVIEWS = "category_interviews"
    REVIEWING = "reviewing"
    GENERATING_REPORTS = "generating_reports"
    DONE = "done"

# ---------- Core entities ----------

class Component(BaseModel):
    id: str
    name: str
    description: str
    status: ComponentStatus = ComponentStatus.ACTIVE
    version: int = 1                      # bumped on edit -> triggers re-validation
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class DriverResolution(BaseModel):
    driver_name: str                      # matches a decision driver from the .md file
    status: DriverStatus = DriverStatus.UNRESOLVED
    value: Optional[str] = None           # the resolved answer/inference, if any
    attempts: int = 0                     # feeds the stall detector

class Decision(BaseModel):
    id: str                               # unique per version, e.g. uuid
    category: str                         # e.g. "database"
    component_id: Optional[str] = None    # None for system-scope categories
    decision: str                         # e.g. "PostgreSQL"
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)
    trade_offs_accepted: str = ""
    driver_resolutions: list[DriverResolution] = Field(default_factory=list)
    status: DecisionStatus = DecisionStatus.ACTIVE
    version: int = 1
    superseded_by: Optional[str] = None   # id of the decision that replaced this one
    created_at: datetime = Field(default_factory=datetime.utcnow)

class InterviewState(BaseModel):
    """Transient state for whichever category interview is in progress."""
    category: str
    component_id: Optional[str] = None
    driver_checklist: list[DriverResolution] = Field(default_factory=list)
    conversation: list[dict] = Field(default_factory=list)   # role/content turns
    stalled_driver: Optional[str] = None   # set when the stall detector fires
    narrowed_section: Optional[str] = None
    # Sticky: once the model reports which top-level knowledge-file section
    # (e.g. "Category: Relational (SQL)") it's converging on, this is set
    # and stays set (only overwritten by a later, different narrowing) --
    # it's what lets prompts.py show full detail on just that section
    # instead of the entire file on every turn. See knowledge_loader.py.
    mode: str = "initial"                        # "initial" | "revalidate"
    trigger_detail: Optional[str] = None          # only set when mode == "revalidate"
    consecutive_structured_failures: int = 0
    # These three exist because, in the LangGraph-wired version, a single
    # "turn" spans TWO separate node invocations (one that calls the LLM,
    # one that just calls interrupt()) rather than one Python function's
    # local variables -- see graph_nodes.py. Anything a turn needs to
    # remember across that boundary has to live in persisted state, not
    # a local variable, since LangGraph reconstructs SessionState fresh
    # on every node call.

class PendingRevalidation(BaseModel):
    """One entry in the queue of decisions awaiting re-validation."""
    category: str
    component_id: Optional[str] = None
    trigger: str            # "upstream_category_changed" | "component_edited" | "user_requested_revision"
    trigger_detail: str     # e.g. "deployment_style changed from Monolith to Microservices"

# ---------- Top-level session state ----------

class SessionState(BaseModel):
    project_goal: str = ""
    phase: Phase = Phase.GATHERING_COMPONENTS

    components: dict[str, Component] = Field(default_factory=dict)          # keyed by component.id

    decisions: dict[str, Decision] = Field(default_factory=dict)            # keyed by decision.id — full history, nothing overwritten
    active_decision_index: dict[str, str] = Field(default_factory=dict)
    # key: "{category}" (system-scope) or "{category}:{component_id}" (per-component)
    # value: decision.id of whichever version is currently active

    current_interview: Optional[InterviewState] = None
    revalidation_queue: list[PendingRevalidation] = Field(default_factory=list)

    component_agent_conversation: list[dict] = Field(default_factory=list)
    # Persists the Component Agent's conversation across separate node
    # invocations, same reasoning as InterviewState's new fields above.
    # Empty once components are locked; not used again afterward.

    pending_review_answer: Optional[str] = None
    # Set by review_node when the user's answer isn't a plain approval,
    # so interpret_revision_node (a separate node -- it makes an LLM
    # call, so it can't share review_node's interrupt() the way we
    # learned the hard way) can read the raw text. Cleared once read.

    review_clarification_note: Optional[str] = None
    # Set by interpret_revision_node when it can't confidently resolve
    # the user's request to a specific category/component -- shown by
    # review_node prepended to the decision log on the next prompt,
    # then cleared.

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def get_checkpoint_serde():
    """
    Returns a JsonPlusSerializer with our custom Enum types (Phase,
    ComponentStatus, DriverStatus, DecisionStatus) explicitly
    allowlisted for msgpack checkpoint serialization, with NO warning
    on deserialization.

    IMPORTANT IMPLEMENTATION NOTE: this constructs JsonPlusSerializer
    with an explicit `allowed_msgpack_modules` list directly, rather
    than calling `.with_msgpack_allowlist(...)` on a plain
    `JsonPlusSerializer()`. The latter looks like the more obvious API
    but is a silent no-op here: LangGraph's default serializer starts
    in "permissive" mode (`_allowed_msgpack_modules = True`, meaning
    "allow everything, but warn"), and `with_msgpack_allowlist()`
    returns unchanged whenever the base is already `True` -- there's
    nothing to "add" to an allow-everything set. The warning only stops
    when `_allowed_msgpack_modules` is an actual set containing the
    type, which requires starting from an explicit (non-True) allowlist
    at construction time. Confirmed by testing both approaches directly
    against a running checkpoint save/load cycle -- the with_msgpack_
    allowlist() version still produced the warning.

    Without this, LangGraph's default serializer still WORKS (permissive
    fallback) but logs a warning on every checkpoint read, and would
    silently break under LANGGRAPH_STRICT_MSGPACK=true, which some
    deployments set for security reasons (the permissive fallback is
    explicitly documented as unsafe for untrusted checkpoint data).

    Use this when constructing ANY checkpointer for this project:
        MemorySaver(serde=get_checkpoint_serde())
        SqliteSaver(conn, serde=get_checkpoint_serde())

    The langgraph import is local to this function rather than at
    module level -- state.py otherwise has zero framework dependencies
    (just pydantic), and there's no reason to force a langgraph import
    on anything that only needs the data model (e.g. a future script
    that just reads/analyzes exported decision logs).
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    return JsonPlusSerializer(allowed_msgpack_modules=[
        Phase, ComponentStatus, DriverStatus, DecisionStatus,
    ])
