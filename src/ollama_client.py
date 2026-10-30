"""
Ollama call wrapper with structured output + retry.

Every agent turn goes through call_ollama_structured() rather than
calling the Ollama client directly, for one reason: local models
(especially smaller ones) don't always return output that matches the
requested JSON schema on the first try -- sometimes it's malformed JSON,
sometimes it's valid JSON but violates one of our model_validators (e.g.
action='ask_question' with no question_text). Both failure modes need
the same response: show the model what went wrong and ask it to correct
itself, not crash the conversation.

NOTE: this has been written against the general shape of the Ollama
Python client's structured-output support (a `format` parameter that
accepts a JSON schema). The exact call signature should be verified
against whichever ollama-python version is actually installed when this
is wired up for real -- treat this file as a design draft, not
confirmed-working code, since nothing in this sandbox can reach a live
Ollama server to test against.
"""

from __future__ import annotations
import json
import logging
from typing import Type, TypeVar

import ollama
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    """
    Raised when the model fails to produce valid, schema-conformant
    output after all retries. The Orchestrator should catch this and
    surface a graceful message to the user (e.g. "I'm having trouble
    processing that -- could you rephrase?") rather than let it crash
    the session.
    """
    def __init__(self, message: str, last_raw_output: str, attempts: int):
        super().__init__(message)
        self.last_raw_output = last_raw_output
        self.attempts = attempts


def call_ollama_structured(
    messages: list[dict],
    schema: Type[T],
    model: str = "llama3.1",
    host: str = "http://localhost:11434",
    max_retries: int = 3,
    temperature: float = 0.1,
) -> T:
    """
    Calls Ollama with a structured-output schema, validates the result
    against the given Pydantic model, and retries with a corrective
    message on failure.

    `messages` follows the standard chat message format:
    [{"role": "system"|"user"|"assistant", "content": "..."}, ...]

    Returns a validated instance of `schema`. Raises
    StructuredOutputError if all retries are exhausted.

    Note on retry semantics: this does NOT raise on a validation
    failure that reflects a legitimate business rule the caller already
    enforces (e.g. the "reject premature commit" guard in the category
    agent loop) -- that check happens *after* this function returns a
    valid `schema` instance. This function only guards against the
    model failing to produce well-formed, schema-conformant JSON at
    all.
    """
    client = ollama.Client(host=host)
    conversation = list(messages)  # local copy; we may append corrective turns
    json_schema = schema.model_json_schema()

    last_raw_output = ""
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat(
                model=model,
                messages=conversation,
                format=json_schema,
                options={"temperature": temperature},
            )
            raw_content = response["message"]["content"]
            last_raw_output = raw_content

            parsed = json.loads(raw_content)
            validated = schema.model_validate(parsed)
            return validated

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(
                "Structured output attempt %d/%d failed for schema %s: %s",
                attempt, max_retries, schema.__name__, e,
            )
            if attempt == max_retries:
                break

            # Give the model its own broken output plus the specific
            # error, and ask it to correct itself -- more effective
            # than just re-asking the original question from scratch.
            conversation.append({"role": "assistant", "content": raw_content})
            conversation.append({
                "role": "user",
                "content": (
                    "Your last response could not be parsed: "
                    f"{e}\n\n"
                    "Respond again with ONLY valid JSON matching the required "
                    "schema. No extra commentary, no markdown code fences."
                ),
            })

    raise StructuredOutputError(
        f"Failed to get valid '{schema.__name__}' output after {max_retries} attempts.",
        last_raw_output=last_raw_output,
        attempts=max_retries,
    )
