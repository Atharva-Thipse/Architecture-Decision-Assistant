"""
Merge logic: turns validated LLM output (schemas.py) into SessionState
(state.py) mutations. This is the layer referenced as
update_driver_status / build_decision / record_decision /
propagate_dependents_to_queue / apply_component_edits in the earlier
Orchestrator design sketch -- now made concrete.

Nothing in this file calls Ollama or does I/O. It's pure state
transformation, which is what makes it possible to test the cascade
logic (below) without a live model.
"""

from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone

from schemas import AgentTurnResponse, ComponentAgentResponse, ComponentProposal, ComponentEdit
from state import (
    SessionState, Component, ComponentStatus,
    Decision, DecisionStatus,
    DriverResolution, DriverStatus,
    InterviewState, PendingRevalidation,
)
from text_matching import fuzzy_match

STALL_THRESHOLD = 2  # attempts beyond the first before we surface a stall to the user


# ---------- key helpers ----------

def build_decision_key(category: str, component_id: str | None) -> str:
    return category if component_id is None else f"{category}:{component_id}"


def get_active_decision(state: SessionState, category: str, component_id: str | None) -> Decision | None:
    key = build_decision_key(category, component_id)
    decision_id = state.active_decision_index.get(key)
    return state.decisions.get(decision_id) if decision_id else None


def component_has_active_decision(state: SessionState, category: str, component_id: str | None) -> bool:
    d = get_active_decision(state, category, component_id)
    return d is not None and d.status == DecisionStatus.ACTIVE


# ---------- revalidation queue ----------

def enqueue_revalidation(
    state: SessionState, category: str, component_id: str | None, trigger: str, trigger_detail: str
) -> None:
    """Adds a PendingRevalidation entry, skipping if an equivalent one is already queued."""
    for existing in state.revalidation_queue:
        if existing.category == category and existing.component_id == component_id:
            return  # already queued; don't duplicate
    state.revalidation_queue.append(
        PendingRevalidation(
            category=category, component_id=component_id,
            trigger=trigger, trigger_detail=trigger_detail,
        )
    )


# ---------- Category Agent merge ----------

def apply_driver_updates(interview: InterviewState, updates: list) -> None:
    """
    Merges DriverUpdate entries (from AgentTurnResponse) into the
    interview's checklist. Uses fuzzy_match rather than exact matching
    -- exact-only matching previously caused silent data loss whenever
    the model paraphrased a driver name even slightly (e.g. "Data model
    shape" vs the seeded "Data shape"), which directly undermined the
    stopping condition: a driver could be genuinely answered but stay
    "unresolved" forever because the label didn't match character-for-
    character. See text_matching.py for the matching logic.
    """
    known_names = [d.driver_name for d in interview.driver_checklist]
    by_name = {d.driver_name: d for d in interview.driver_checklist}
    for update in updates:
        matched_name = fuzzy_match(update.driver_name, known_names, context="driver name")
        if matched_name is None:
            continue  # logged inside fuzzy_match; genuinely no reasonable match
        target = by_name[matched_name]
        target.status = DriverStatus(update.status)
        target.value = update.value
        target.attempts = 0  # resolved -> stall counter no longer relevant


def record_question_attempt(interview: InterviewState, targeted_driver: str) -> None:
    known_names = [d.driver_name for d in interview.driver_checklist]
    matched_name = fuzzy_match(targeted_driver, known_names, context="driver name")
    if matched_name is not None:
        for d in interview.driver_checklist:
            if d.driver_name == matched_name:
                d.attempts += 1
                return
    # Genuinely no match, even fuzzy -- track it anyway so it's not
    # silently lost, but this should be rare if fuzzy_match is working
    # (it logs a warning already).
    interview.driver_checklist.append(
        DriverResolution(driver_name=targeted_driver, status=DriverStatus.UNRESOLVED, attempts=1)
    )


def is_stalled(interview: InterviewState, targeted_driver: str) -> bool:
    known_names = [d.driver_name for d in interview.driver_checklist]
    matched_name = fuzzy_match(targeted_driver, known_names, context="driver name")
    if matched_name is None:
        return False
    for d in interview.driver_checklist:
        if d.driver_name == matched_name:
            return d.attempts > STALL_THRESHOLD
    return False


def apply_narrowed_section(interview: InterviewState, narrowed_section: str | None) -> None:
    """
    Sticky update: only overwrites interview.narrowed_section when the
    model actually reports a (non-empty) value this turn. A turn that
    leaves narrowed_section unset doesn't erase prior narrowing -- the
    model isn't re-stating it every turn, only when it changes.
    """
    if narrowed_section:
        interview.narrowed_section = narrowed_section


def all_drivers_resolved(interview: InterviewState) -> bool:
    return all(d.status != DriverStatus.UNRESOLVED for d in interview.driver_checklist)


def commit_category_decision(
    state: SessionState,
    response: AgentTurnResponse,
    category: str,
    component_id: str | None,
) -> bool:
    """
    Builds a Decision from a validated commit_decision response, records
    it (superseding any prior active decision for this slot), and
    returns whether the decision actually CHANGED vs what was active
    before. That boolean is what the caller uses to decide whether to
    propagate revalidation further downstream -- a reaffirmed decision
    (same option chosen again during a revalidation) should NOT
    re-trigger its own dependents.

    IMPORTANT: when nothing changed, no new Decision is created at all
    -- the existing active decision is left completely untouched (same
    id, same version, still ACTIVE). An earlier version of this function
    computed `changed` correctly but never actually gated the mutation
    on it, silently version-bumping and superseding even on a pure
    reaffirmation -- caught by testing the revalidation path end-to-end,
    not by inspection.
    """
    assert state.current_interview is not None
    assert response.action == "commit_decision"

    previous = get_active_decision(state, category, component_id)
    changed = previous is None or previous.decision != response.decision

    if not changed:
        state.current_interview = None
        state.updated_at = datetime.now(timezone.utc)
        return False

    new_decision = Decision(
        id=str(uuid.uuid4()),
        category=category,
        component_id=component_id,
        decision=response.decision,
        rationale=response.rationale,
        alternatives_considered=response.alternatives_considered,
        trade_offs_accepted=response.trade_offs_accepted or "",
        driver_resolutions=[d.model_copy() for d in state.current_interview.driver_checklist],
        status=DecisionStatus.ACTIVE,
        version=(previous.version + 1) if previous else 1,
    )

    if previous is not None:
        previous.status = DecisionStatus.SUPERSEDED
        previous.superseded_by = new_decision.id

    state.decisions[new_decision.id] = new_decision
    state.active_decision_index[build_decision_key(category, component_id)] = new_decision.id
    state.current_interview = None
    state.updated_at = datetime.now(timezone.utc)

    return True


def propagate_dependents(
    state: SessionState,
    changed_category: str,
    changed_component_id: str | None,
    yaml_config: dict,
    trigger_detail: str,
) -> None:
    """
    Walks category_dependencies.yaml for categories that depend on
    `changed_category`, and enqueues revalidation for any of THEIR
    already-active decisions. Does not recurse -- if a revalidation
    itself results in a further change, that call site is responsible
    for calling this function again for the newly-changed category.
    This keeps propagation naturally following "one hop per commit"
    rather than needing to precompute a transitive closure up front.

    Scope handling: if the changed category is per-component, only
    that same component's dependents are affected (e.g. a structure_pattern
    change for component X only revalidates component X's database/apis
    decisions, not every component's). If the changed category is
    system-scoped, every component's relevant decisions are affected.
    """
    dependents = [
        cat for cat, info in yaml_config["dependencies"].items()
        if changed_category in info.get("depends_on", [])
    ]

    for dep_category in dependents:
        dep_scope = yaml_config["categories"][dep_category]["scope"]

        if dep_scope == "per_component":
            if changed_component_id is not None:
                targets = [changed_component_id]
            else:
                # system-wide change -> every component with an active decision here
                targets = [
                    cid for cid, c in state.components.items()
                    if c.status == ComponentStatus.ACTIVE
                    and component_has_active_decision(state, dep_category, cid)
                ]
            for cid in targets:
                if component_has_active_decision(state, dep_category, cid):
                    enqueue_revalidation(
                        state, dep_category, cid,
                        trigger="upstream_category_changed", trigger_detail=trigger_detail,
                    )
        else:  # system scope
            if component_has_active_decision(state, dep_category, None):
                enqueue_revalidation(
                    state, dep_category, None,
                    trigger="upstream_category_changed", trigger_detail=trigger_detail,
                )


# ---------- Component Agent merge ----------

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "component"


def _unique_component_id(state: SessionState, name: str) -> str:
    base = slugify(name)
    candidate = base
    n = 2
    while candidate in state.components:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def apply_component_proposal(state: SessionState, proposals: list[ComponentProposal]) -> None:
    for p in proposals:
        cid = _unique_component_id(state, p.name)
        state.components[cid] = Component(id=cid, name=p.name, description=p.description)
    state.updated_at = datetime.now(timezone.utc)


def apply_component_edits(state: SessionState, edits: list[ComponentEdit], yaml_config: dict) -> None:
    """
    Applies add/remove/edit operations. For 'edit' on a component that
    already has active decisions, enqueues revalidation for each of
    those decisions -- this is the "preserve and re-validate" behavior
    agreed on for component redefinition, reusing the same
    PendingRevalidation queue as category-driven revisions rather than
    a separate mechanism.
    """
    for edit in edits:
        if edit.type == "add":
            cid = _unique_component_id(state, edit.name)
            state.components[cid] = Component(id=cid, name=edit.name, description=edit.description or "")

        elif edit.type == "remove":
            comp = state.components.get(edit.component_id)
            if comp is not None:
                comp.status = ComponentStatus.REMOVED
                comp.updated_at = datetime.now(timezone.utc)
                # Existing decisions for a removed component are left as-is
                # (not deleted, not superseded) -- they simply won't be
                # rendered in the final report once the component is inactive.

        elif edit.type == "edit":
            comp = state.components.get(edit.component_id)
            if comp is None:
                continue
            if edit.name:
                comp.name = edit.name
            if edit.description:
                comp.description = edit.description
            comp.version += 1
            comp.updated_at = datetime.now(timezone.utc)

            for category in yaml_config["categories"]:
                if component_has_active_decision(state, category, comp.id):
                    enqueue_revalidation(
                        state, category, comp.id,
                        trigger="component_edited",
                        trigger_detail=f"Component '{comp.name}' was edited (v{comp.version}).",
                    )

    state.updated_at = datetime.now(timezone.utc)
