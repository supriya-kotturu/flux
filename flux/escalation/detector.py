"""Decides whether a stopped run should escalate to a human — brief §3.6.

Kept separate from the loop/executor that produces the stop reason: "is
this stuck enough to interrupt a person" is a policy decision, not a
mechanical consequence of the result type, and belongs next to the rest of
flux.escalation rather than baked into flux.agent.loop or
flux.replay.executor.
"""

from __future__ import annotations

from flux.agent.loop import DiscoveryRun
from flux.replay.errors import ReplayResult

# Everything except a clean win. `give_up` and `dead_end` are the agent (or
# the loop, on its behalf) explicitly saying it can't safely continue;
# `timeout`/`max_steps` are "wasn't making progress" — equally worth a
# person's attention rather than silently discarding the run.
_DISCOVERY_ESCALATION_REASONS = frozenset({"dead_end", "give_up", "timeout", "max_steps"})

# A policy failure (missing approval, a missing secret) is a configuration
# problem an operator fixes by re-running with the right flags — handing
# them a live browser to drive wouldn't help. A business outcome isn't a
# failure at all. Only a step or the checkpoint actually failing against a
# real page — the case brief §3.6 describes as "a replay hits a condition
# it can't recover from" — warrants putting a human in front of that page.
_REPLAY_ESCALATION_CATEGORIES = frozenset({"action", "checkpoint", "timeout"})


def discovery_should_escalate(run: DiscoveryRun) -> bool:
    return run.stop_reason in _DISCOVERY_ESCALATION_REASONS


def replay_should_escalate(result: ReplayResult) -> bool:
    return result.kind == "failure" and result.category in _REPLAY_ESCALATION_CATEGORIES
