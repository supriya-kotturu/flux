"""The discovery loop: observe -> decide -> act, until the goal is met or a stopping condition fires.

Brief §3.1 requires four stopping conditions: goal met, max steps, timeout,
and dead-end. The first three are straightforward; "dead-end" here means N
consecutive failed actions — the agent isn't making progress and isn't
declaring `give_up` either, so the loop declares it for them rather than
burning the whole step budget on a stuck state.

This module never imports `anthropic` or `flux.agent.llm_client.AnthropicClient`
directly — it depends on the `LLMClient` protocol, so tests can drive it with
a scripted fake against the real BrowserSurface + mock_bank, with no network
call and no API key.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel

from flux.agent.llm_client import LLMClient, ToolCall
from flux.agent.prompts import render_observation, render_tool_result, system_prompt
from flux.agent.tools import TOOL_SCHEMAS, is_terminal, tool_call_to_action
from flux.observability.logger import RunLogger
from flux.surface.base import Action, ActionResult, Observation, Surface

StopReason = Literal["goal_complete", "give_up", "max_steps", "timeout", "dead_end"]

DEFAULT_MAX_STEPS = 20
DEFAULT_TIMEOUT_S = 180.0
DEAD_END_THRESHOLD = 3  # consecutive failed actions


class DiscoveryStep(BaseModel):
    index: int
    observation: Observation
    tool_name: str
    tool_input: dict[str, Any]
    action: Action | None  # None for terminal tool calls (goal_complete / give_up)
    result: ActionResult | None


class DiscoveryRun(BaseModel):
    goal: str
    target: str
    run_id: str
    stop_reason: StopReason
    success: bool
    steps: list[DiscoveryStep]
    outputs: dict[str, Any] | None = None
    checkpoint: str | None = None
    give_up_reason: str | None = None


def run_discovery(
    *,
    goal: str,
    target: str,
    surface: Surface,
    llm: LLMClient,
    logger: RunLogger,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> DiscoveryRun:
    logger.event("discovery_started", controller="agent", goal=goal, target=target)

    nav_result = surface.act(Action(kind="navigate", value=target))
    if not nav_result.ok:
        logger.event("discovery_stopped", controller="agent", stop_reason="dead_end", detail=nav_result.error)
        return DiscoveryRun(
            goal=goal, target=target, run_id=logger.run_id, stop_reason="dead_end",
            success=False, steps=[], give_up_reason=f"could not load target: {nav_result.error}",
        )

    system = system_prompt(goal)
    observation = surface.observe()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": render_observation(observation, note="Initial state.")}
    ]

    steps: list[DiscoveryStep] = []
    consecutive_failures = 0
    start = time.monotonic()

    for i in range(max_steps):
        if time.monotonic() - start > timeout_s:
            logger.event("discovery_stopped", controller="agent", stop_reason="timeout", step_index=i)
            return _finish(goal, target, logger, "timeout", False, steps)

        response = llm.next_step(messages=messages, tools=TOOL_SCHEMAS, system=system)

        if not response.tool_calls:
            logger.event("model_did_not_act", controller="agent", step_index=i, text=response.text)
            messages.append({"role": "assistant", "content": response.content_blocks})
            messages.append({"role": "user", "content": "You must call exactly one tool to proceed."})
            continue

        call = response.tool_calls[0]
        messages.append({"role": "assistant", "content": response.content_blocks})
        logger.event("model_decided", controller="agent", step_index=i, tool=call.name, input=call.input)

        if is_terminal(call):
            return _handle_terminal(goal, target, logger, call, steps, observation)

        try:
            action = tool_call_to_action(call)
        except ValueError as exc:
            logger.event("malformed_tool_call", controller="agent", step_index=i, error=str(exc))
            messages.append(_tool_result_message(call, f"malformed call: {exc}"))
            consecutive_failures += 1
            if consecutive_failures >= DEAD_END_THRESHOLD:
                return _finish(goal, target, logger, "dead_end", False, steps)
            continue

        result = surface.act(action)
        observation = surface.observe()
        logger.event(
            "action_result", controller="agent", step_index=i, tool=call.name,
            ok=result.ok, error=result.error, resolved_via=result.resolved_via.describe() if result.resolved_via else None,
        )
        steps.append(DiscoveryStep(
            index=i, observation=observation, tool_name=call.name, tool_input=call.input,
            action=action, result=result,
        ))
        messages.append(_tool_result_message(call, render_tool_result(call.name, result, observation)))

        consecutive_failures = 0 if result.ok else consecutive_failures + 1
        if consecutive_failures >= DEAD_END_THRESHOLD:
            logger.event("discovery_stopped", controller="agent", stop_reason="dead_end", step_index=i)
            return _finish(goal, target, logger, "dead_end", False, steps)

    logger.event("discovery_stopped", controller="agent", stop_reason="max_steps")
    return _finish(goal, target, logger, "max_steps", False, steps)


def _tool_result_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "content": content}]}


def _handle_terminal(
    goal: str, target: str, logger: RunLogger, call: ToolCall,
    steps: list[DiscoveryStep], observation: Observation,
) -> DiscoveryRun:
    if call.name == "goal_complete":
        outputs = call.input.get("outputs", {})
        checkpoint = call.input.get("checkpoint", "")
        logger.event("goal_complete", controller="agent", outputs=outputs, checkpoint=checkpoint)
        steps.append(DiscoveryStep(
            index=len(steps), observation=observation, tool_name=call.name,
            tool_input=call.input, action=None, result=None,
        ))
        return DiscoveryRun(
            goal=goal, target=target, run_id=logger.run_id, stop_reason="goal_complete",
            success=True, steps=steps, outputs=outputs, checkpoint=checkpoint,
        )

    reason = call.input.get("reason", "")
    logger.event("give_up", controller="agent", reason=reason)
    steps.append(DiscoveryStep(
        index=len(steps), observation=observation, tool_name=call.name,
        tool_input=call.input, action=None, result=None,
    ))
    return DiscoveryRun(
        goal=goal, target=target, run_id=logger.run_id, stop_reason="give_up",
        success=False, steps=steps, give_up_reason=reason,
    )


def _finish(
    goal: str, target: str, logger: RunLogger, stop_reason: StopReason,
    success: bool, steps: list[DiscoveryStep],
) -> DiscoveryRun:
    return DiscoveryRun(
        goal=goal, target=target, run_id=logger.run_id, stop_reason=stop_reason,
        success=success, steps=steps,
    )
