"""
Renders the final decision log once all decisions are approved. Pure
string-building, no I/O in the render functions themselves (consistent
with how merge.py stays pure and ollama_client.py isolates the actual
network call) -- write_reports() is the only function that touches disk.

Two outputs:
- Architecture_Decisions.md: human-readable, the actual deliverable
  this whole project exists to produce -- every decision documented
  with its rationale, alternatives considered, trade-offs accepted,
  and the full decision-driver checklist that led to it (the "why,"
  not just the label).
- decisions.json: a machine-readable export of just the decision log
  (not the full session state, which also carries transient interview/
  conversation data that isn't meaningful once the session is done).
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from state import SessionState, Decision, DecisionStatus, ComponentStatus


def _category_display_name(category: str) -> str:
    return category.replace("_", " ").title()


def _format_driver_resolutions(decision: Decision) -> str:
    lines = []
    for d in decision.driver_resolutions:
        if d.status.value == "unresolved":
            continue  # shouldn't occur for a committed decision, but skip defensively rather than mislead
        lines.append(f"- **{d.driver_name}** _{d.status.value}_: {d.value}")
    return "\n".join(lines) if lines else "_(no decision drivers recorded)_"


def _format_single_decision(decision: Decision) -> str:
    parts = [f"**Decision:** {decision.decision}  "]
    parts.append(f"**Rationale:** {decision.rationale}  ")
    if decision.alternatives_considered:
        parts.append(f"**Alternatives considered:** {', '.join(decision.alternatives_considered)}  ")
    if decision.trade_offs_accepted:
        parts.append(f"**Trade-offs accepted:** {decision.trade_offs_accepted}  ")
    parts.append("")
    parts.append("**Why (decision drivers considered):**")
    parts.append(_format_driver_resolutions(decision))
    return "\n".join(parts)


def render_decision_log_markdown(state: SessionState) -> str:
    lines: list[str] = []
    lines.append("# Architecture Decision Log")
    lines.append("")
    lines.append(f"**Project goal:** {state.project_goal}")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")

    active_components = [c for c in state.components.values() if c.status == ComponentStatus.ACTIVE]

    lines.append("## Components")
    lines.append("")
    for c in active_components:
        lines.append(f"- **{c.name}** -- {c.description}")
    lines.append("")

    def sorted_active_decisions():
        return [
            state.decisions[did]
            for key, did in sorted(state.active_decision_index.items())
        ]

    system_decisions = [d for d in sorted_active_decisions() if d.component_id is None]
    if system_decisions:
        lines.append("## System-Wide Decisions")
        lines.append("")
        for d in system_decisions:
            lines.append(f"### {_category_display_name(d.category)}")
            lines.append("")
            lines.append(_format_single_decision(d))
            lines.append("")

    lines.append("## Component Decisions")
    lines.append("")
    for c in active_components:
        component_decisions = [d for d in sorted_active_decisions() if d.component_id == c.id]
        if not component_decisions:
            continue
        lines.append(f"### {c.name}")
        lines.append("")
        if c.description:
            lines.append(f"_{c.description}_")
            lines.append("")
        for d in component_decisions:
            lines.append(f"#### {_category_display_name(d.category)}")
            lines.append("")
            lines.append(_format_single_decision(d))
            lines.append("")

    superseded = [d for d in state.decisions.values() if d.status == DecisionStatus.SUPERSEDED]
    if superseded:
        lines.append("## Revision History")
        lines.append("")
        lines.append("Decisions that were revisited and changed during the process:")
        lines.append("")
        for d in sorted(superseded, key=lambda x: (x.category, x.component_id or "", x.version)):
            scope = f"component '{state.components[d.component_id].name}'" if d.component_id else "system-wide"
            replacement = state.decisions.get(d.superseded_by)
            replacement_text = replacement.decision if replacement else "(unknown)"
            lines.append(
                f"- **{_category_display_name(d.category)}** ({scope}), v{d.version}: "
                f"{d.decision} -> superseded by **{replacement_text}**. Original rationale: {d.rationale}"
            )
        lines.append("")

    return "\n".join(lines)


def export_decision_log_json(state: SessionState) -> str:
    active_components = [c for c in state.components.values() if c.status == ComponentStatus.ACTIVE]
    payload = {
        "project_goal": state.project_goal,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "components": [{"id": c.id, "name": c.name, "description": c.description} for c in active_components],
        "decisions": [
            json.loads(state.decisions[did].model_dump_json())
            for _, did in sorted(state.active_decision_index.items())
        ],
    }
    return json.dumps(payload, indent=2)


def write_reports(state: SessionState, output_dir: str) -> tuple[str, str]:
    """Writes both output files to output_dir, creating it if needed. Returns their paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "Architecture_Decisions.md"
    json_path = out / "decisions.json"
    md_path.write_text(render_decision_log_markdown(state), encoding="utf-8")
    json_path.write_text(export_decision_log_json(state), encoding="utf-8")
    return str(md_path), str(json_path)
