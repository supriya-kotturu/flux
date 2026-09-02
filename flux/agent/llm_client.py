"""The only place the discovery loop talks to Anthropic.

`loop.py` depends on the `LLMClient` protocol, not on this class or the
`anthropic` package — tests drive the loop with a scripted fake instead
(see tests/unit/test_discovery_loop.py). That's the "mock the boundary
cleanly" the brief asks for when live LLM access isn't the point of what's
being tested: the loop's control flow (stopping conditions, message
threading, dead-end detection) is independent of which model answers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_MODEL = os.environ.get("FLUX_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = 2048


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    stop_reason: str
    text: str | None
    tool_calls: list[ToolCall]
    content_blocks: list[Any]  # raw Anthropic content blocks, needed to round-trip into the next request


class LLMClient(Protocol):
    def next_step(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> LLMResponse: ...


class AnthropicClient:
    """Thin wrapper around the Messages API. Requires ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        from anthropic import Anthropic  # imported lazily so tests never need the package configured

        self._client = Anthropic(api_key=api_key)
        self._model = model

    def next_step(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str
    ) -> LLMResponse:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
        tool_calls = [
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        text = "".join(block.text for block in response.content if block.type == "text") or None
        return LLMResponse(
            stop_reason=response.stop_reason,
            text=text,
            tool_calls=tool_calls,
            content_blocks=response.content,
        )
