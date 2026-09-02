"""Prompt construction for the discovery loop."""

from __future__ import annotations

from flux.surface.base import ActionResult, Observation

SYSTEM_PROMPT_TEMPLATE = """\
You are operating a legacy bank back-office web application on behalf of an \
automation system. You cannot see pixels — your only view of the screen is \
the accessibility tree below, the same information a screen reader gets. \
Treat it as ground truth.

Your goal: {goal}

Rules:
- Call exactly one tool per turn. Do not narrate — act.
- Prefer locating controls by role+accessible-name over visible text; both are \
more reliable than nothing, but role+name survives layout changes better.
- This is a real system with side effects. Read-only navigation is safe to \
try freely; anything that looks like it creates, modifies, or confirms a \
record is not reversible — proceed deliberately and only after you're on \
the right record.
- Some confirmations appear as native browser dialogs, not page content. If \
you click something you expect to trigger one, say so via `expect_dialog`. \
If you don't, the dialog will be dismissed by default and your action will \
not have completed — check the next observation to confirm before assuming \
it worked.
- Before calling `goal_complete`, if your last step was a click that should have \
finished the flow, `extract` some stable text that is only present on the \
success state you landed on (not just the button you clicked — that control is \
gone once you've navigated past it). This becomes the checkpoint a replay \
later verifies against, so it has to describe where you ended up, not what \
you last clicked.
- When you believe the goal is met, call `goal_complete` with every value \
you extracted and a `checkpoint` description precise enough that someone \
replaying this later (without you) could verify they reached the same state.
- If you get stuck — the target doesn't exist, you're denied access, or you \
genuinely don't see a safe way forward — call `give_up` with why, rather \
than guessing or retrying blindly.
"""


def system_prompt(goal: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(goal=goal)


def render_observation(observation: Observation, *, note: str | None = None) -> str:
    parts = []
    if note:
        parts.append(note)
    parts.append(f"URL: {observation.url}")
    parts.append(f"Title: {observation.title}")
    if observation.pending_dialog:
        parts.append(
            f"NOTE: a {observation.pending_dialog.kind} dialog appeared and was "
            f"dismissed by default: {observation.pending_dialog.message!r}"
        )
    parts.append("Accessibility tree:")
    parts.append(observation.ax_tree)
    return "\n".join(parts)


def render_tool_result(call_name: str, result: ActionResult, observation: Observation) -> str:
    parts = []
    if result.ok:
        parts.append(f"{call_name} succeeded" + (f" (resolved via {result.resolved_via.describe()})" if result.resolved_via else ""))
        if result.data:
            parts.append(f"extracted: {result.data}")
    else:
        parts.append(f"{call_name} FAILED: {result.error}")
    if result.dialog_seen:
        parts.append(f"a {result.dialog_seen.kind} dialog appeared: {result.dialog_seen.message!r}")
    parts.append("")
    parts.append(render_observation(observation))
    return "\n".join(parts)
