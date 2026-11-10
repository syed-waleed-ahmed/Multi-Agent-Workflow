"""Structured (JSON) output with bounded, conversational self-repair.

On a parse or validation failure the model sees its own previous answer and the
exact error, then answers again. This recovers most malformed outputs from small
models, and the number of attempts is hard-capped.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..exceptions import OutputParsingError
from ..parsing import parse_json_object
from .base import ChatMessage, CompletionRequest, CompletionResponse, ModelT, StructuredResult

MAX_REPAIRS_LIMIT = 3


def repair_instruction(error: Exception) -> str:
    """The corrective message sent after a failed parse."""
    return (
        "Your previous response could not be parsed as the required JSON object "
        f"(error: {error}). Respond again with ONLY a single valid JSON object that "
        "contains exactly the required keys and nothing else."
    )


def run_structured(
    call: Callable[[CompletionRequest], CompletionResponse],
    request: CompletionRequest,
    schema: type[ModelT],
    *,
    max_repairs: int = 1,
    on_repair: Callable[[], None] | None = None,
    validation_context: dict[str, Any] | None = None,
) -> StructuredResult[ModelT]:
    """Call ``call`` until its output validates against ``schema`` or repairs run out.

    Args:
        call: Performs one completion (a provider's ``complete`` or a budgeted wrapper).
        request: The initial request; JSON mode is forced on.
        schema: The Pydantic model the output must validate against.
        max_repairs: Corrective re-asks allowed after the first attempt (0-3).
        on_repair: Invoked before each re-ask (used to charge the run budget).
        validation_context: Passed to Pydantic validators (e.g. the evidence ids a
            response may cite).

    Raises:
        OutputParsingError: if no attempt produced valid output.
    """
    if not 0 <= max_repairs <= MAX_REPAIRS_LIMIT:
        raise ValueError(f"max_repairs must be between 0 and {MAX_REPAIRS_LIMIT}.")
    agent = request.metadata.get("agent", "structured-output")
    current = request.model_copy(update={"json_mode": True})
    responses: list[CompletionResponse] = []
    last_error: Exception | None = None

    for attempt in range(max_repairs + 1):
        if attempt > 0 and on_repair is not None:
            on_repair()
        response = call(current)
        responses.append(response)
        try:
            value = schema.model_validate(
                parse_json_object(response.text), context=validation_context
            )
        except (ValueError, ValidationError) as exc:
            last_error = exc
            current = current.model_copy(
                update={
                    "messages": (
                        *current.messages,
                        ChatMessage(role="assistant", content=response.text),
                        ChatMessage(role="user", content=repair_instruction(exc)),
                    )
                }
            )
            continue
        return StructuredResult(value=value, responses=tuple(responses))

    raise OutputParsingError(
        agent,
        f"Output did not match {schema.__name__} after {len(responses)} attempt(s): {last_error}",
    ) from last_error
