from __future__ import annotations

from datetime import datetime, timezone

from flux.escalation.detector import discovery_should_escalate, replay_should_escalate
from flux.replay.errors import ReplayBusinessOutcome, ReplayFailure, ReplaySuccess


def _run(stop_reason: str, success: bool = False):
    from flux.agent.loop import DiscoveryRun

    return DiscoveryRun(goal="g", target="t", run_id="r", stop_reason=stop_reason, success=success, steps=[])


def test_goal_complete_never_escalates() -> None:
    assert discovery_should_escalate(_run("goal_complete", success=True)) is False


def test_stuck_reasons_all_escalate() -> None:
    for reason in ("dead_end", "give_up", "timeout", "max_steps"):
        assert discovery_should_escalate(_run(reason)) is True


def test_replay_success_does_not_escalate() -> None:
    assert replay_should_escalate(ReplaySuccess(outputs={})) is False


def test_replay_business_outcome_does_not_escalate() -> None:
    assert replay_should_escalate(ReplayBusinessOutcome(name="x", description="d")) is False


def test_replay_policy_failure_does_not_escalate() -> None:
    # A missing secret/approval is fixed by re-running with the right flags,
    # not by handing an operator a live browser.
    failure = ReplayFailure(category="policy", step_index=None, expected="e", observed="o")
    assert replay_should_escalate(failure) is False


def test_replay_action_and_checkpoint_failures_escalate() -> None:
    for category in ("action", "checkpoint", "timeout"):
        failure = ReplayFailure(category=category, step_index=2, expected="e", observed="o")
        assert replay_should_escalate(failure) is True
