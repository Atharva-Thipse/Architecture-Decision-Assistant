"""
Shared fuzzy matching for reconciling an LLM-reported string (a
narrowed_section hint, or a driver_name in a DriverUpdate) against a
fixed list of known-good candidate strings (section headings, or the
seeded driver checklist).

This exists because two different call sites had two different bugs
stemming from the same root cause -- naive string matching:

1. knowledge_loader.find_section() used raw substring containment
   ("hint in heading.lower()"), which incorrectly matched the hint
   "SQL" against BOTH "Relational (SQL)" and "NewSQL" (the latter
   contains the raw characters "s-q-l" even though it's not the word
   "SQL"). Whichever appeared first in the list won, silently.

2. merge.apply_driver_updates() used exact-match-only, which silently
   DROPPED a driver resolution entirely if the model paraphrased the
   driver name even slightly (e.g. "Data model shape" vs the seeded
   "Data shape") -- directly undermining the stopping condition, since
   the driver would incorrectly stay "unresolved" despite having been
   answered.

Both are fixed here with the same token-based approach: exact match
first, then whole-word token containment (fixes bug 1, since "sql" as
a token is not contained in the single token "newsql"), then partial
token overlap as a last resort (handles real paraphrasing, fixes bug
2). Callers get a consistent, tested matching behavior instead of two
different ad-hoc implementations.
"""

from __future__ import annotations
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def fuzzy_match(hint: str, candidates: list[str], context: str = "") -> Optional[str]:
    """
    Returns the best-matching candidate string, or None if nothing
    reasonable matches. `context` is just for log messages (e.g.
    "driver name" or "section heading") to make warnings legible.
    """
    hint = hint.strip()
    if not hint:
        return None

    # 1. Exact match (case/whitespace-insensitive)
    hint_norm = hint.lower()
    for c in candidates:
        if c.strip().lower() == hint_norm:
            return c

    hint_tokens = _tokenize(hint)
    if not hint_tokens:
        return None

    # 2. Whole-word token containment: every token in the hint must
    #    appear as a whole token in the candidate. This is what fixes
    #    the SQL/NewSQL bug -- {"sql"} is not a subset of {"newsql"}
    #    (different tokens), even though "sql" is a raw substring of
    #    "newsql" character-by-character.
    fully_contained = [c for c in candidates if hint_tokens <= _tokenize(c)]
    if fully_contained:
        if len(fully_contained) > 1:
            # Ambiguous: multiple candidates fully contain the hint's
            # tokens. Prefer the most specific one (fewest extra
            # tokens) as a reasonable tie-break, but log it -- this
            # usually means the hint was too generic (e.g. a bare
            # "Application" when three candidates all contain that
            # word) and the caller's prompt should ideally avoid that.
            fully_contained.sort(key=lambda c: len(_tokenize(c)))
            logger.warning(
                "Ambiguous %s match for %r: candidates %s all fully contain the hint's "
                "tokens; choosing the most specific: %r",
                context, hint, fully_contained, fully_contained[0],
            )
        return fully_contained[0]

    # 3. Partial token overlap -- handles genuine paraphrasing, e.g.
    #    hint "Data model shape" vs candidate "Data shape" (tokens
    #    {"data","shape"} overlap = 2, out of 3 hint tokens).
    best, best_score = None, 0
    for c in candidates:
        score = len(hint_tokens & _tokenize(c))
        if score > best_score:
            best, best_score = c, score

    if best is not None:
        logger.info(
            "Fuzzy-matched %s %r to %r via partial token overlap (score=%d/%d).",
            context, hint, best, best_score, len(hint_tokens),
        )
        return best

    logger.warning("No %s match found for %r among candidates: %s", context, hint, candidates)
    return None
