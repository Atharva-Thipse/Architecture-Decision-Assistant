"""
Loads knowledge base .md files and parses them into two things the
agent logic needs:

1. Decision drivers -- extracted from the "How to use this file"
   section, used to seed InterviewState.driver_checklist. This is what
   makes the stopping condition real: driver names come directly from
   the same list a human reads in each knowledge file, so there's no
   separately maintained list that could drift out of sync.

2. Top-level sections ("## Category: X" or "## Option: X", our six
   files aren't consistent about which) -- used to trim what actually
   gets sent to the model each turn. Full detail on every option in a
   knowledge file is often 15-20k+ characters; most of that is
   irrelevant once the interview has narrowed toward one option. See
   ParsedKnowledgeFile / format_options_menu / find_section below.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path

from text_matching import fuzzy_match

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

# Matches the "## How to use this file ..." heading through the next
# "---" separator -- the section every knowledge file uses to list its
# decision drivers as bold-led bullets, e.g. "- **Data shape**: ...".
_DRIVER_SECTION_RE = re.compile(r"## How to use this file.*?(?=\n---)", re.DOTALL)
_DRIVER_BULLET_RE = re.compile(r"^- \*\*(.+?)\*\*", re.MULTILINE)

# Matches lines that are exactly a level-2 heading ("## ..."), not
# level-3 ("### ..."). Used to split a file into top-level sections
# regardless of whether they're literally named "Category:" or
# "Option:" -- some files are flat "## Option:", some are two-level
# "## Category:" -> "### Option:", and apis.md mixes both patterns.
_TOP_LEVEL_HEADING_RE = re.compile(r"^## (?!#)(.+)$", re.MULTILINE)


def load_knowledge_file(category: str) -> str:
    path = KNOWLEDGE_DIR / f"{category}.md"
    if not path.exists():
        raise FileNotFoundError(f"No knowledge file for category '{category}' at {path}")
    return path.read_text(encoding="utf-8")


def extract_decision_drivers(markdown_text: str) -> list[str]:
    """
    Pulls the bold-led bullet list of decision drivers out of the
    "How to use this file" section. Restricted to that section
    specifically (rather than searching the whole document) so bold
    text used elsewhere in the file -- e.g. "**Use when:**" labels
    inside each option -- doesn't get mistaken for a driver.
    """
    section_match = _DRIVER_SECTION_RE.search(markdown_text)
    if not section_match:
        return []
    return _DRIVER_BULLET_RE.findall(section_match.group(0))


# ---------- section parsing (for context trimming) ----------

@dataclass
class KnowledgeSection:
    heading: str          # e.g. "Category: Relational (SQL)" or "Option: Monolithic"
    full_text: str        # this section's complete text, including any nested ### subsections
    summary: str          # trimmed: heading-level Use when/Avoid when, no nested suboption detail


@dataclass
class ParsedKnowledgeFile:
    intro: str                                                       # text before the first "## " heading
    summary_guidance: str = ""                                       # the "## Decision Summary Guidance" section, if present
    options: list[KnowledgeSection] = field(default_factory=list)    # every other "## " section, in order


def parse_knowledge_file(markdown_text: str) -> ParsedKnowledgeFile:
    matches = list(_TOP_LEVEL_HEADING_RE.finditer(markdown_text))
    if not matches:
        # Degenerate case: no "## " headings at all. Treat the whole
        # file as intro text; nothing to narrow.
        return ParsedKnowledgeFile(intro=markdown_text)

    intro = markdown_text[: matches[0].start()].strip()
    parsed = ParsedKnowledgeFile(intro=intro)

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        full_text = f"## {heading}\n" + markdown_text[start:end].strip()

        if heading.startswith("How to use this file"):
            continue  # already represented via the driver checklist -- skip, don't duplicate
        if heading.startswith("Decision Summary Guidance"):
            parsed.summary_guidance = full_text
            continue

        summary = _extract_summary(full_text)
        parsed.options.append(KnowledgeSection(heading=heading, full_text=full_text, summary=summary))

    return parsed


def _extract_summary(full_text: str) -> str:
    """
    Trims a section down to its heading + Use-when/Avoid-when framing,
    dropping nested "### Option:" subsection detail (trade-offs, common
    pitfalls, scaling notes for each specific option). Sections with no
    nested subsections (a few flat entries, e.g. Voice/Headless in
    interfaces.md) have nothing to trim, so this just returns their
    (already short) body as-is.

    Some sections (e.g. apis.md's "Request/Response APIs" grouping) go
    straight from the heading into nested subsections with no
    category-level framing text of their own -- trimming naively would
    leave a bare, uninformative heading in the menu view. In that case,
    fall back to listing the nested option names instead, so the menu
    still tells the model what's inside even without a description.
    """
    subsection_start = full_text.find("\n### ")
    head_only = (full_text[:subsection_start] if subsection_start != -1 else full_text).strip()

    heading_line = full_text.splitlines()[0].strip()
    has_real_framing = len(head_only) > len(heading_line) + 10  # more than just the heading itself

    if has_real_framing:
        return head_only

    nested_headings = [h.strip() for h in re.findall(r"^### (.+)$", full_text, re.MULTILINE)]
    nested_names = [re.sub(r"^Option:\s*", "", h) for h in nested_headings]
    if nested_names:
        return f"{heading_line}\n(Includes: {', '.join(nested_names)} -- narrow in to see full detail)"
    return head_only


def format_options_menu(parsed: ParsedKnowledgeFile) -> str:
    """Compact view: every top-level section's heading + trimmed summary. No nested detail."""
    return "\n\n".join(s.summary for s in parsed.options)


def find_section(parsed: ParsedKnowledgeFile, heading_hint: str) -> KnowledgeSection | None:
    """
    Matches a model-reported narrowed_section string against actual
    section headings, using the shared fuzzy_match utility (token-based,
    not raw substring containment -- see text_matching.py for why that
    distinction matters, e.g. avoiding "SQL" incorrectly matching
    "NewSQL").
    """
    headings = [s.heading for s in parsed.options]
    matched_heading = fuzzy_match(heading_hint, headings, context="section heading")
    if matched_heading is None:
        return None
    return next(s for s in parsed.options if s.heading == matched_heading)
