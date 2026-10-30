"""
Structured-output schemas for LLM responses.

These define the JSON shape Ollama's structured-output mode is asked to
produce for each agent. Two design choices worth noting up front:

1. FLAT schemas, not discriminated unions. Even though "ask_question"
   and "commit_decision" responses logically have different required
   fields, we use one flat model per agent with optional fields and
   enforce the conditional requirements via a Pydantic validator
   afterward. Local models via Ollama are noticeably less reliable at
   producing valid oneOf/anyOf JSON schema output than a single flat
   object -- so this trades a bit of schema strictness for much better
   generation reliability, and we get the strictness back via
   validation (which also feeds the retry loop in ollama_client.py).

2. These are the LLM's OUTPUT CONTRACT, separate from the internal
   state-tracking models in state.py (e.g. DriverResolution). The
   orchestrator merges LLM output into state -- the LLM never sees or
   writes state.py models directly, since those carry bookkeeping
   fields (attempts, timestamps, ids) the model has no business
   generating itself.
"""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

# ---------- Category Agent ----------

class DriverUpdate(BaseModel):
    """
    One decision-driver resolution the model is reporting this turn.
    A single user answer can resolve more than one driver at once
    (e.g. "we're a 3-person team building an MVP" answers both team
    size and time-to-market drivers) -- so a turn can carry several
    of these, not just the one being actively asked about.
    """
    driver_name: str
    status: Literal["answered", "inferred", "not_relevant"]
    value: str = Field(..., description="The resolved value/summary for this driver.")


class AgentTurnResponse(BaseModel):
    """
    One turn of the Category Agent's interview loop. `action` is the
    two-way choice that implements the stopping condition: the model
    must explicitly choose to keep asking or to commit, rather than
    us trying to infer intent from free text.
    """
    action: Literal["ask_question", "commit_decision"]

    # Always allowed, regardless of action -- lets the model update
    # its understanding of drivers incidentally, not just when
    # directly asking about them.
    driver_updates: list[DriverUpdate] = Field(default_factory=list)

    # Optional, both actions. Once you have a reasonable sense of which
    # top-level knowledge-base section (e.g. "Category: Relational (SQL)")
    # this decision is converging toward, report it here EXACTLY as that
    # section's heading appears in the knowledge base. Leave unset while
    # still genuinely unsure between multiple top-level sections. Once
    # set, you'll be shown full detail on only that section going
    # forward -- so only narrow when you're reasonably confident.
    narrowed_section: Optional[str] = None

    # Required when action == "ask_question"
    targeted_driver: Optional[str] = Field(
        None, description="Name of the decision driver this question targets."
    )
    question_text: Optional[str] = Field(
        None, description="The question to show the user."
    )

    # Required when action == "commit_decision"
    decision: Optional[str] = Field(
        None, description="The chosen option, e.g. 'PostgreSQL'."
    )
    rationale: Optional[str] = None
    alternatives_considered: list[str] = Field(default_factory=list)
    trade_offs_accepted: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_fields_for_action(self) -> "AgentTurnResponse":
        if self.action == "ask_question":
            missing = [
                f for f, v in [("targeted_driver", self.targeted_driver),
                                ("question_text", self.question_text)]
                if not v
            ]
            if missing:
                raise ValueError(
                    f"action='ask_question' requires non-empty fields: {missing}"
                )
        elif self.action == "commit_decision":
            missing = [
                f for f, v in [("decision", self.decision), ("rationale", self.rationale)]
                if not v
            ]
            if missing:
                raise ValueError(
                    f"action='commit_decision' requires non-empty fields: {missing}"
                )
        return self


# ---------- Component Agent ----------

class ComponentProposal(BaseModel):
    """A component the model is proposing (used on the very first turn)."""
    name: str
    description: str


class ComponentEdit(BaseModel):
    """One add/remove/edit operation against the current component list."""
    type: Literal["add", "remove", "edit"]
    component_id: Optional[str] = Field(
        None, description="Required for 'remove' and 'edit'; omitted for 'add'."
    )
    name: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_fields_for_edit_type(self) -> "ComponentEdit":
        if self.type in ("remove", "edit") and not self.component_id:
            raise ValueError(f"type='{self.type}' requires component_id.")
        if self.type in ("add", "edit") and not self.name:
            raise ValueError(f"type='{self.type}' requires name.")
        return self


class ComponentProposalResponse(BaseModel):
    """
    Used ONLY for the very first Component Agent turn (no components
    exist yet). There's no `action` field here at all -- the Orchestrator
    already knows this turn can only be a proposal (see
    component_agent_step's `bootstrap` branch), so there's no need to
    make the model choose an action.

    Critically, `components` is required with `min_length=1` here, which
    puts a real constraint into the JSON schema itself. The combined
    schema this replaced only enforced "non-empty if action=='propose'"
    via a post-hoc model_validator, which is invisible to the model at
    generation time -- `components` just looked like any other skippable,
    defaulted field. Observed in practice: local models (e.g. llama3.2)
    reliably wrote a full prose description of the proposed components
    into `message_to_user` while leaving the *structured* `components`
    list empty, failing validation every single retry since the
    corrective message didn't change that underlying signal. Making the
    field genuinely required at the schema level fixes this at the
    source instead of hoping the model self-corrects.
    """
    components: list[ComponentProposal] = Field(..., min_length=1)
    message_to_user: str = Field(
        ..., description="Conversational message shown to the user this turn."
    )


class ComponentAgentResponse(BaseModel):
    """
    One turn of the Component Agent's loop, for every turn AFTER the
    initial proposal (see ComponentProposalResponse for that one).
    `action='revise'` applies edits and keeps looping; `action='lock'`
    means the user confirmed the list, handing control back to the
    Orchestrator. 'propose' is intentionally not a valid value here --
    it's structurally impossible to need it again once components exist,
    so there's no reason to let the model pick it (or to carry the
    now-unused `components` field on this schema at all).
    """
    action: Literal["revise", "lock"]
    edits: list[ComponentEdit] = Field(default_factory=list)             # required for 'revise'
    message_to_user: str = Field(
        ..., description="Conversational message shown to the user this turn."
    )

    @model_validator(mode="after")
    def _check_required_fields_for_action(self) -> "ComponentAgentResponse":
        if self.action == "revise" and not self.edits:
            raise ValueError("action='revise' requires a non-empty edits list.")
        return self


# ---------- Review revision interpretation ----------

class ReviewRevisionResponse(BaseModel):
    """
    Interprets a free-text message from the review step that ISN'T a
    plain approval -- the user is presumably asking to revisit some
    specific decision, but may phrase it vaguely. `understood=False`
    lets the model honestly say it can't tell, rather than guessing at
    a category/component and silently reopening the wrong thing.
    category_hint/component_hint are free text (matched against real
    category keys/component names via fuzzy_match downstream) rather
    than a constrained enum, since the model may not reproduce a
    category key exactly -- same reasoning as narrowed_section in
    AgentTurnResponse.
    """
    understood: bool = Field(
        ..., description="True if the message clearly identifies a specific decision to revisit."
    )
    category_hint: Optional[str] = Field(
        None, description="Which decision category the user wants to revisit (required if understood=True)."
    )
    component_hint: Optional[str] = Field(
        None, description="Which component's decision, if the category is per-component. Omit for system-wide categories or if not specified."
    )
    revision_note: Optional[str] = Field(
        None, description="Concise summary of what the user wants changed/reconsidered (required if understood=True) -- becomes context for re-opening the decision."
    )
    clarifying_question: Optional[str] = Field(
        None, description="A question to ask the user to clarify what they'd like to change (required if understood=False)."
    )

    @model_validator(mode="after")
    def _check_required_fields(self) -> "ReviewRevisionResponse":
        if self.understood:
            missing = [f for f, v in [("category_hint", self.category_hint), ("revision_note", self.revision_note)] if not v]
            if missing:
                raise ValueError(f"understood=True requires non-empty fields: {missing}")
        else:
            if not self.clarifying_question:
                raise ValueError("understood=False requires a non-empty clarifying_question")
        return self
