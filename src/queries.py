"""
Read-only queries over SessionState. Kept separate from merge.py
(which is exclusively mutation logic) for a clean split: nothing here
changes state, which makes these safe to call speculatively/repeatedly
without side effects -- e.g. from prompt-building code that runs on
every turn.
"""

from __future__ import annotations
from state import SessionState, ComponentStatus, Decision
from merge import get_active_decision


def gather_upstream_decisions(
    state: SessionState, category: str, component_id: str | None, yaml_config: dict
) -> dict[str, Decision]:
    """
    Returns {upstream_category: Decision} for every category this one
    depends on (per category_dependencies.yaml) that already has an
    active decision. Per-component upstream categories are looked up
    for the SAME component_id -- our dependency graph never has a
    per-component category depending on a different component's
    decision, only either a system-scope upstream or the same
    component's own upstream categories.
    """
    depends_on = yaml_config["dependencies"].get(category, {}).get("depends_on", [])
    result: dict[str, Decision] = {}
    for dep_category in depends_on:
        dep_scope = yaml_config["categories"][dep_category]["scope"]
        dep_component_id = component_id if dep_scope == "per_component" else None
        decision = get_active_decision(state, dep_category, dep_component_id)
        if decision is not None:
            result[dep_category] = decision
    return result


def gather_sibling_decisions(
    state: SessionState, category: str, component_id: str | None
) -> dict[str, Decision]:
    """
    Returns {sibling_component_name: Decision} for OTHER active
    components that already have a decision in this same category.
    System-scope categories (component_id is None) have no sibling
    concept -- there's only ever one decision for the whole system.
    """
    if component_id is None:
        return {}
    result: dict[str, Decision] = {}
    for cid, component in state.components.items():
        if cid == component_id or component.status != ComponentStatus.ACTIVE:
            continue
        decision = get_active_decision(state, category, cid)
        if decision is not None:
            result[component.name] = decision
    return result
