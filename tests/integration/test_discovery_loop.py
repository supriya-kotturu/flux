"""Discovery loop control flow, driven by a scripted fake LLM against the real browser + mock bank.

This is exactly the "mock the boundary cleanly" the brief allows: the loop's
stopping conditions, message threading, and action translation are all real
and all exercised here without a network call or an API key. A live
Anthropic-backed run is a separate, one-off demonstration (see /evidence),
not something the automated suite depends on.
"""

from __future__ import annotations

from pathlib import Path

from flux.agent.llm_client import LLMResponse, ToolCall
from flux.agent.loop import run_discovery
from flux.observability.logger import RunLogger, new_run_id
from flux.surface.browser import BrowserSurface


class FakeLLMClient:
    """Replays a fixed sequence of tool calls, ignoring the actual message content."""

    def __init__(self, script: list[tuple[str, dict]]) -> None:
        self._script = list(script)
        self._i = 0

    def next_step(self, messages, tools, system) -> LLMResponse:
        assert self._i < len(self._script), "FakeLLMClient script exhausted before the loop stopped"
        name, tool_input = self._script[self._i]
        call = ToolCall(id=f"call_{self._i}", name=name, input=tool_input)
        self._i += 1
        return LLMResponse(
            stop_reason="tool_use",
            text=None,
            tool_calls=[call],
            content_blocks=[{"type": "tool_use", "id": call.id, "name": name, "input": tool_input}],
        )


def _logger(tmp_path: Path, prefix: str = "test-discover") -> RunLogger:
    return RunLogger(new_run_id(prefix), evidence_root=tmp_path, echo_to_stdout=False)


def _locator(by: str, value: str, **extra) -> dict:
    d = {"by": by, "value": value}
    d.update(extra)
    return d


def test_happy_path_reaches_goal_complete_with_expected_output(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    script = [
        ("type", {"locator": _locator("label", "Username"), "text": "operator"}),
        ("type", {"locator": _locator("label", "Password"), "text": "letmein"}),
        ("click", {"locator": _locator("role", "Sign In", role="button")}),
        ("type", {"locator": _locator("label", "Member ID or last name"), "text": "10001"}),
        ("click", {"locator": _locator("role", "Search", role="button")}),
        ("click", {"locator": _locator("role", "Open Record", role="link")}),
        ("extract", {"locator": _locator("text", "4210.55"), "output_name": "savings_balance"}),
        ("goal_complete", {"outputs": {"savings_balance": "$4210.55"}, "checkpoint": "member detail page for 10001"}),
    ]
    run = run_discovery(
        goal="look up member 10001 and read their savings balance",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient(script),
        logger=_logger(tmp_path),
        max_steps=10,
    )

    assert run.stop_reason == "goal_complete"
    assert run.success is True
    assert run.outputs == {"savings_balance": "$4210.55"}
    # every non-terminal step actually executed and succeeded
    action_steps = [s for s in run.steps if s.action is not None]
    assert len(action_steps) == 7
    assert all(s.result is not None and s.result.ok for s in action_steps)


def test_give_up_is_reported_not_treated_as_failure_of_the_loop_itself(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    script = [("give_up", {"reason": "no safe path found"})]
    run = run_discovery(
        goal="do something impossible",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient(script),
        logger=_logger(tmp_path),
    )
    assert run.stop_reason == "give_up"
    assert run.success is False
    assert run.give_up_reason == "no safe path found"


def test_repeated_failures_trigger_dead_end_before_max_steps(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    broken = ("click", {"locator": _locator("role", "This Button Does Not Exist", role="button")})
    run = run_discovery(
        goal="click something that isn't there",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient([broken] * 10),
        logger=_logger(tmp_path),
        max_steps=10,
    )
    assert run.stop_reason == "dead_end"
    assert run.success is False
    assert len(run.steps) == 3  # DEAD_END_THRESHOLD


def test_max_steps_stops_a_loop_that_never_declares_done(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    harmless = ("click", {"locator": _locator("role", "Member Search", role="link")})
    run = run_discovery(
        goal="wander forever",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient([harmless] * 5),
        logger=_logger(tmp_path),
        max_steps=2,
    )
    assert run.stop_reason == "max_steps"
    assert run.success is False
    assert len(run.steps) == 2
