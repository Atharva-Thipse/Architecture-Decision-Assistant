"""
Prompt templates for the Category Agent and Component Agent.

Design notes:

- Templates return `list[dict]` message lists ready to hand straight to
  call_ollama_structured() -- no further assembly needed at the call
  site.

- The system prompt is rebuilt fresh on every call rather than cached
  once per interview. It's cheap (string formatting) and avoids ever
  serving stale upstream/sibling context if something changed mid-
  interview.

- Upstream context passes the FULL Decision (decision + rationale +
  trade-offs), per the "blackboard" design -- downstream agents need
  the "why," not just the label. Sibling context is deliberately
  trimmed to one line each -- it's advisory, not authoritative, and
  full detail there would bloat context for marginal benefit (this
  matches the earlier design discussion directly).

- Revalidation prompts reuse the exact same AgentTurnResponse schema as
  the initial interview (no separate schema) -- they're just steered
  via system-prompt instructions toward preferring an immediate
  commit_decision unless something genuinely changed enough to warrant
  a question.
"""

from __future__ import annotations
from state import Decision, DriverResolution, DriverStatus
from knowledge_loader import ParsedKnowledgeFile, format_options_menu, find_section

MAX_SIBLING_CONTEXT_ITEMS = 5  # keep sibling summaries short even with many components


def build_knowledge_context(parsed: ParsedKnowledgeFile, narrowed_section: str | None) -> str:
    """
    The trimming behavior this whole module exists for: before the
    interview has narrowed to a specific section, show only the
    compact menu (heading + trimmed summary per option, no nested
    suboption detail). Once narrowed, show full detail on ONLY that
    one section, plus a one-line reminder of what else exists (so the
    model doesn't forget other options were ruled out for a reason,
    without paying the token cost of their full detail again).
    """
    parts = [parsed.intro]

    if narrowed_section:
        matched = find_section(parsed, narrowed_section)
        if matched:
            other_headings = [s.heading for s in parsed.options if s.heading != matched.heading]
            parts.append(matched.full_text)
            if other_headings:
                parts.append(
                    "(Other categories considered and not detailed here, based on the "
                    f"checklist so far: {', '.join(other_headings)})"
                )
        else:
            # Model reported a narrowed_section we couldn't match -- fail safe
            # to the full menu rather than silently showing nothing.
            parts.append(format_options_menu(parsed))
    else:
        parts.append(format_options_menu(parsed))

    if parsed.summary_guidance:
        parts.append(parsed.summary_guidance)

    return "\n\n".join(p for p in parts if p)


# ---------- formatting helpers ----------

def format_driver_checklist(checklist: list[DriverResolution]) -> str:
    lines = []
    for d in checklist:
        if d.status == DriverStatus.UNRESOLVED:
            lines.append(f"- [ ] {d.driver_name}: not yet resolved")
        else:
            lines.append(f"- [x] {d.driver_name} ({d.status.value}): {d.value}")
    return "\n".join(lines)


def format_decision_full(decision: Decision) -> str:
    alts = ", ".join(decision.alternatives_considered) or "none recorded"
    return (
        f"Decision: {decision.decision}\n"
        f"Rationale: {decision.rationale}\n"
        f"Alternatives considered: {alts}\n"
        f"Trade-offs accepted: {decision.trade_offs_accepted}"
    )


def format_upstream_context(upstream_decisions: dict[str, Decision]) -> str:
    """upstream_decisions: {category_name: Decision}, already resolved by the caller."""
    if not upstream_decisions:
        return "(no upstream decisions apply to this category)"
    blocks = []
    for category, decision in upstream_decisions.items():
        blocks.append(f"### {category}\n{format_decision_full(decision)}")
    return "\n\n".join(blocks)


def format_sibling_context(sibling_decisions: dict[str, Decision]) -> str:
    """sibling_decisions: {component_name: Decision} for the SAME category, other components."""
    if not sibling_decisions:
        return "(no other components have a decision in this category yet)"
    items = list(sibling_decisions.items())[:MAX_SIBLING_CONTEXT_ITEMS]
    lines = [
        f"- {comp_name}: {decision.decision} ({decision.rationale[:80]}...)"
        if len(decision.rationale) > 80 else f"- {comp_name}: {decision.decision} ({decision.rationale})"
        for comp_name, decision in items
    ]
    return "\n".join(lines)


# ---------- Category Agent prompts ----------

_CATEGORY_SYSTEM_TEMPLATE = """\
You are a system-architecture decision assistant helping decide the \
"{category}" choice for {scope_description}.

Your knowledge base for this decision:
---
{knowledge_text}
---

RULES YOU MUST FOLLOW:
1. Every turn, you must choose exactly one action: "ask_question" or "commit_decision".
2. You may only choose "commit_decision" once every decision driver below is \
resolved (status "answered", "inferred", or "not_relevant") -- never while any \
driver is still unresolved, unless the user has explicitly told you to decide \
with what you have.
3. When asking a question, target exactly ONE unresolved driver at a time \
(the `targeted_driver` field). Use the driver name EXACTLY as written below \
-- do not paraphrase or invent a new name.
4. If a user's answer resolves more than one driver at once (which happens \
often), report all of them in `driver_updates` in the same turn, not just \
the one you asked about.
5. Infer what you reasonably can from context already given (project goal, \
upstream decisions, prior answers) before asking the user -- mark inferred \
drivers as "inferred", not "answered". Only mark "not_relevant" when a \
driver genuinely doesn't apply to this case, not as a way to skip asking.
6. When you commit, your `decision` must be one of the specific named \
options in the knowledge base above (e.g. a specific database engine, not \
just "a database"). Ground your `rationale` in the specific decision \
drivers you resolved.

DECISION DRIVER CHECKLIST (current status):
{driver_checklist}

UPSTREAM DECISIONS (already settled, treat as fixed context -- these shape \
what's appropriate here, you cannot change them):
{upstream_context}

OTHER COMPONENTS' DECISIONS IN THIS SAME CATEGORY (informational only -- \
consider for consistency/avoiding unnecessary tool sprawl, but each \
component's needs may legitimately differ):
{sibling_context}
"""


def build_category_system_prompt(
    category: str,
    parsed_knowledge: ParsedKnowledgeFile,
    narrowed_section: str | None,
    driver_checklist: list[DriverResolution],
    upstream_decisions: dict[str, Decision],
    sibling_decisions: dict[str, Decision],
    component_name: str | None,
) -> str:
    scope_description = (
        f"the component '{component_name}'" if component_name else "the system as a whole"
    )
    knowledge_text = build_knowledge_context(parsed_knowledge, narrowed_section)
    return _CATEGORY_SYSTEM_TEMPLATE.format(
        category=category,
        scope_description=scope_description,
        knowledge_text=knowledge_text,
        driver_checklist=format_driver_checklist(driver_checklist),
        upstream_context=format_upstream_context(upstream_decisions),
        sibling_context=format_sibling_context(sibling_decisions),
    )


def build_category_messages(
    category: str,
    parsed_knowledge: ParsedKnowledgeFile,
    narrowed_section: str | None,
    driver_checklist: list[DriverResolution],
    upstream_decisions: dict[str, Decision],
    sibling_decisions: dict[str, Decision],
    component_name: str | None,
    conversation_so_far: list[dict],
) -> list[dict]:
    """
    `conversation_so_far` is the FULL transcript for this interview so
    far, including the most recent turn (whether that's the initial
    opening user message or the latest user answer) -- there is no
    separate "latest message" parameter, specifically to avoid a caller
    accidentally appending the same turn twice or dropping one, which
    was a real risk with the earlier opening/followup split once this
    gets called repeatedly inside an actual loop.
    """
    system = build_category_system_prompt(
        category, parsed_knowledge, narrowed_section, driver_checklist,
        upstream_decisions, sibling_decisions, component_name,
    )
    return [{"role": "system", "content": system}] + conversation_so_far


def build_opening_user_text(project_goal: str) -> str:
    return (
        f"Project goal: {project_goal}\n\n"
        "Begin the interview for this decision. Ask about the single most "
        "informative unresolved driver first, or commit immediately if you "
        "can already infer everything needed from the context above."
    )


def build_stall_message(driver_name: str) -> str:
    """
    Fixed, non-LLM-generated message shown to the user when the stall
    detector fires. Deliberately not an LLM call -- we don't want the
    one part of the system meant to recover from unreliable model
    behavior to itself depend on another model call.
    """
    return (
        f"I'm having trouble pinning down \"{driver_name}\" from our conversation. "
        "Could you tell me directly, or would you like me to make a reasonable "
        "assumption and move on?"
    )


_REVALIDATION_SYSTEM_SUFFIX = """

ADDITIONAL CONTEXT -- THIS IS A RE-VALIDATION, NOT A FRESH INTERVIEW:
An upstream decision changed, which may or may not affect this one.

What changed: {trigger_detail}

The previously active decision for this category was:
{previous_decision}

Prefer responding with "commit_decision" immediately -- either reaffirming \
the same decision (if it still holds) or proposing a new one (if the change \
genuinely invalidates it). Only ask a question if you genuinely cannot tell \
without more input from the user; do not re-run the full original interview.
"""

REVALIDATION_OPENING_TEXT = "Re-validate this decision given what changed."


def build_revalidation_messages(
    category: str,
    parsed_knowledge: ParsedKnowledgeFile,
    narrowed_section: str | None,
    driver_checklist: list[DriverResolution],
    upstream_decisions: dict[str, Decision],
    sibling_decisions: dict[str, Decision],
    component_name: str | None,
    previous_decision: Decision,
    trigger_detail: str,
    conversation_so_far: list[dict],
) -> list[dict]:
    system = build_category_system_prompt(
        category, parsed_knowledge, narrowed_section, driver_checklist,
        upstream_decisions, sibling_decisions, component_name,
    ) + _REVALIDATION_SYSTEM_SUFFIX.format(
        trigger_detail=trigger_detail,
        previous_decision=format_decision_full(previous_decision),
    )
    return [{"role": "system", "content": system}] + conversation_so_far


# ---------- Component Agent prompts ----------
#
# Two separate system prompts, matching the two separate schemas in
# schemas.py (ComponentProposalResponse vs ComponentAgentResponse): the
# bootstrap turn's prompt never mentions "action" or a 'propose' choice
# at all, since the schema no longer has room for the model to get that
# wrong, and it puts the component-listing requirement front and center
# since that field is now the ONLY thing this turn is for.

_COMPONENT_BOOTSTRAP_SYSTEM_PROMPT = """\
You are a requirements-elicitation assistant helping a user break down \
their project goal into system components.

Propose an initial set of components for this project based on the goal \
below. Keep each component focused on a single clear responsibility.

RULES YOU MUST FOLLOW:
1. Populate `components` with every component you're proposing (name + \
description each). This field IS your proposal -- it must be non-empty, \
and it is not optional or something you can leave for `message_to_user` \
to cover instead.
2. `message_to_user` is a short, conversational message that explicitly \
names every component in `components` with a one-line description, since \
`message_to_user` is the only text the user reads directly. Then invite \
Add/Remove/Edit feedback.

Project goal: {project_goal}
"""

_COMPONENT_ONGOING_SYSTEM_PROMPT = """\
You are a requirements-elicitation assistant helping a user refine a \
proposed breakdown of their project into system components.

RULES YOU MUST FOLLOW:
1. Interpret the user's feedback and choose action "revise" (apply \
add/remove/edit operations to the list, via `edits`) or "lock" (the user \
has confirmed the list is final -- only choose this when the user has \
clearly said so, e.g. "looks good", "lock it in", "that's all of them").
2. Never choose "lock" on your own initiative -- only in direct response \
to explicit user confirmation.
3. `message_to_user` is the ONLY place the user sees your output as text --
the structured `edits` field is not shown to them directly. So on a \
"revise" turn, state what changed AND THEN re-list the full current set of \
components (not only the changed ones) with a one-line description each, \
so the user always sees the complete, up-to-date picture in one place. If \
not locking yet, end by inviting further Add/Remove/Edit feedback.

Project goal: {project_goal}
"""

COMPONENT_OPENING_TEXT = "Please propose an initial set of components for this project."


def build_component_messages(
    project_goal: str, conversation_so_far: list[dict], bootstrap: bool
) -> list[dict]:
    """
    Same transcript-based pattern as build_category_messages -- see its
    docstring. `bootstrap=True` selects the propose-only prompt (paired
    with ComponentProposalResponse); `bootstrap=False` selects the
    revise/lock prompt (paired with ComponentAgentResponse). The caller
    (component_agent_step) already knows which phase it's in from
    `state.components`, so this is a plain flag, not something inferred
    here.
    """
    template = _COMPONENT_BOOTSTRAP_SYSTEM_PROMPT if bootstrap else _COMPONENT_ONGOING_SYSTEM_PROMPT
    system = template.format(project_goal=project_goal)
    return [{"role": "system", "content": system}] + conversation_so_far


# ---------- Review revision interpretation ----------

_REVIEW_INTERPRETATION_SYSTEM = """\
You are helping interpret a user's feedback during review of a completed \
architecture decision log. The user's message is NOT a plain approval \
(that's already been handled) -- they're presumably asking to revisit a \
specific decision, but may phrase it vaguely or casually.

Known decision categories: {category_keys}
Known components: {component_names}

RULES:
1. If the message clearly identifies (even loosely) which decision to \
revisit, set understood=True, and set category_hint to the closest \
matching category from the list above (doesn't need to be an exact \
string match -- just close enough to identify it), component_hint to \
the closest matching component name if relevant (omit if the category \
is system-wide or no component was specified), and revision_note \
summarizing what they want changed or reconsidered.
2. If the message is vague, off-topic, or you genuinely cannot tell \
which decision they mean, set understood=False and ask a specific \
clarifying_question -- e.g. naming a couple of the categories above and \
asking which one, or asking which component if that's the ambiguity.
3. Do not guess a category/component you're not reasonably confident \
about -- an incorrect guess silently reopens the wrong decision, which \
is worse than asking one clarifying question.
"""


def build_review_interpretation_messages(
    user_text: str, category_keys: list[str], component_names: list[str]
) -> list[dict]:
    system = _REVIEW_INTERPRETATION_SYSTEM.format(
        category_keys=", ".join(category_keys),
        component_names=", ".join(component_names) if component_names else "(none)",
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
