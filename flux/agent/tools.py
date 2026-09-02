"""Tool schemas the discovery agent chooses from, and their translation to `Action`.

Deliberately narrower than `flux.surface.base.Action`: the model picks one
locator strategy per step (role+name, label, or visible text — never
coordinates, never a multi-candidate fallback list). Discovery only needs
*a* reliable way to act each step; turning that single choice into a
ranked, self-healing candidate list is the artifact recorder's job
(Phase 4), which can re-derive redundant strategies for the same resolved
element after the fact. Keeping the model's decision surface small also
keeps it from reaching for brittle coordinates when a semantic locator
would do.
"""

from __future__ import annotations

from typing import Any

from flux.agent.llm_client import ToolCall
from flux.surface.base import Action, DialogResponse
from flux.surface.locator import label, make, role_name, text as text_candidate

_LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "by": {
            "type": "string",
            "enum": ["role", "label", "text"],
            "description": (
                "How to find the target. Prefer 'role' (e.g. role=button, value='Search') "
                "when the control has a clear semantic role and accessible name. Use 'label' "
                "for form fields identified by their <label> text. Use 'text' only when "
                "neither gives a unique match."
            ),
        },
        "role": {"type": "string", "description": "ARIA role, required when by='role' (button, link, textbox, ...)"},
        "value": {
            "type": "string",
            "description": "Accessible name (by=role), label text (by=label), or visible text (by=text).",
        },
        "exact": {"type": "boolean", "default": False},
    },
    "required": ["by", "value"],
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "click",
        "description": "Click a button, link, or other clickable control.",
        "input_schema": {
            "type": "object",
            "properties": {
                "locator": _LOCATOR_SCHEMA,
                "expect_dialog": {
                    "type": "string",
                    "enum": ["accept", "dismiss"],
                    "description": (
                        "If this click is expected to trigger a native browser confirm/alert "
                        "dialog, say whether to accept or dismiss it. Omit if you don't expect one — "
                        "the default is to dismiss, so an unexpected confirmation never sails through."
                    ),
                },
            },
            "required": ["locator"],
        },
    },
    {
        "name": "type",
        "description": "Type text into a field, replacing its current contents.",
        "input_schema": {
            "type": "object",
            "properties": {"locator": _LOCATOR_SCHEMA, "text": {"type": "string"}},
            "required": ["locator", "text"],
        },
    },
    {
        "name": "select",
        "description": "Choose an option in a <select> dropdown.",
        "input_schema": {
            "type": "object",
            "properties": {"locator": _LOCATOR_SCHEMA, "option": {"type": "string"}},
            "required": ["locator", "option"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait for a control to become visible (e.g. after a slow-loading page).",
        "input_schema": {
            "type": "object",
            "properties": {
                "locator": _LOCATOR_SCHEMA,
                "timeout_ms": {"type": "integer", "default": 8000},
            },
            "required": ["locator"],
        },
    },
    {
        "name": "extract",
        "description": "Read the text/value of a control and record it under a named output.",
        "input_schema": {
            "type": "object",
            "properties": {"locator": _LOCATOR_SCHEMA, "output_name": {"type": "string"}},
            "required": ["locator", "output_name"],
        },
    },
    {
        "name": "navigate",
        "description": "Go directly to a URL. Prefer clicking links; use this only to recover.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "goal_complete",
        "description": "Declare the goal achieved. Include every extracted output and a checkpoint description of the final state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outputs": {"type": "object", "description": "output_name -> value, for every extract call made"},
                "checkpoint": {"type": "string", "description": "How to recognize this success state on replay."},
            },
            "required": ["outputs", "checkpoint"],
        },
    },
    {
        "name": "give_up",
        "description": "Declare that the goal cannot be safely completed. Use this rather than guessing.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

_TERMINAL_TOOLS = {"goal_complete", "give_up"}


def is_terminal(call: ToolCall) -> bool:
    return call.name in _TERMINAL_TOOLS


def _build_locator(spec: dict[str, Any]):
    by = spec["by"]
    value = spec["value"]
    exact = bool(spec.get("exact", False))
    if by == "role":
        role = spec.get("role")
        if not role:
            raise ValueError("locator with by='role' requires 'role'")
        return make(role_name(role, value, exact=exact))
    if by == "label":
        return make(label(value, exact=exact))
    if by == "text":
        return make(text_candidate(value, exact=exact))
    raise ValueError(f"unknown locator strategy: {by}")


def tool_call_to_action(call: ToolCall) -> Action:
    """Translate one model tool call into a Surface Action. Raises ValueError on a malformed call."""
    if call.name == "navigate":
        url = call.input.get("url")
        if not url:
            raise ValueError("navigate requires 'url'")
        return Action(kind="navigate", value=url)

    if call.name == "click":
        locator = _build_locator(call.input["locator"])
        on_dialog: DialogResponse | None = call.input.get("expect_dialog")
        return Action(kind="click", locator=locator, on_dialog=on_dialog)

    if call.name == "type":
        locator = _build_locator(call.input["locator"])
        return Action(kind="type", locator=locator, value=call.input.get("text", ""))

    if call.name == "select":
        locator = _build_locator(call.input["locator"])
        return Action(kind="select", locator=locator, value=call.input.get("option", ""))

    if call.name == "wait_for":
        locator = _build_locator(call.input["locator"])
        return Action(kind="wait_for", locator=locator, timeout_ms=call.input.get("timeout_ms", 8000))

    if call.name == "extract":
        locator = _build_locator(call.input["locator"])
        return Action(kind="extract", locator=locator)

    raise ValueError(f"{call.name} is not an action tool (terminal tools have no Action)")
