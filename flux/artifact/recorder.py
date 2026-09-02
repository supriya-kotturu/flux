"""Converts a successful DiscoveryRun into a versioned Artifact.

Parameterization is a deliberate, reviewable step, not something inferred
automatically: the caller states which concrete values used during this
particular run correspond to which named input parameter (e.g.
`{"member_id": "10001"}`), and the recorder substitutes `{{member_id}}`
for every literal occurrence of "10001" across the recorded steps. A
person decided what varies per invocation — the recorder doesn't guess.

This is also where each step's locator becomes a ranked fallback list: the
top candidate is what the model actually used, and any `alternate_candidates`
BrowserSurface captured live at execution time (flux.surface.browser) are
folded in beneath it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flux.agent.loop import DiscoveryRun
from flux.artifact.schema import (
    AppTarget,
    Artifact,
    Checkpoint,
    NamedOutcome,
    ParamSpec,
    Provenance,
    Step,
)
from flux.surface.base import Locator, LocatorCandidate


def record(
    run: DiscoveryRun,
    *,
    name: str,
    description: str,
    app_target: AppTarget,
    input_params: dict[str, str] | None = None,
    input_schema: dict[str, ParamSpec] | None = None,
    known_outcomes: list[NamedOutcome] | None = None,
    checkpoint: Checkpoint | None = None,
) -> Artifact:
    if run.stop_reason != "goal_complete" or not run.success:
        raise ValueError(
            f"can only record an artifact from a successful run (goal_complete); "
            f"this run stopped with stop_reason={run.stop_reason!r}"
        )

    input_params = input_params or {}
    steps: list[Step] = []
    output_schema: dict[str, ParamSpec] = {}
    last_locator_step: Step | None = None

    for discovery_step in run.steps:
        if discovery_step.action is None:
            continue  # terminal step (goal_complete/give_up) - not a replay action

        action = discovery_step.action
        result = discovery_step.result
        locator = _build_step_locator(action.locator, result, input_params)
        value_template = _templatize(action.value, input_params) if action.value else None

        output_name = None
        if discovery_step.tool_name == "extract":
            output_name = discovery_step.tool_input.get("output_name")
            if output_name:
                output_schema[output_name] = ParamSpec(
                    type="string", description=f"extracted at step {discovery_step.index}"
                )

        step = Step(
            index=len(steps),
            kind=action.kind,
            locator=locator,
            value_template=value_template,
            output_name=output_name,
            description=discovery_step.tool_input.get("reasoning") or _auto_description(action),
            risk_level="irreversible" if action.on_dialog == "accept" else "safe",
        )
        steps.append(step)
        if locator is not None:
            last_locator_step = step

    resolved_checkpoint = checkpoint or _infer_checkpoint(run, last_locator_step)
    resolved_input_schema = input_schema or {p: ParamSpec(type="string") for p in input_params}

    now = datetime.now(timezone.utc)
    return Artifact(
        id=name,
        version=1,
        name=name,
        description=description,
        app_target=app_target,
        input_schema=resolved_input_schema,
        output_schema=output_schema,
        steps=steps,
        known_outcomes=known_outcomes or [],
        checkpoint=resolved_checkpoint,
        provenance=Provenance(discovery_run_id=run.run_id, recorded_at=now),
        requires_approval=any(s.risk_level == "irreversible" for s in steps),
        created_at=now,
    )


def _templatize(value: str, input_params: dict[str, str]) -> str:
    for param_name, concrete_value in input_params.items():
        if concrete_value and concrete_value in value:
            value = value.replace(concrete_value, "{{" + param_name + "}}")
    return value


def _templatize_candidate(candidate: LocatorCandidate, input_params: dict[str, str]) -> LocatorCandidate:
    updates: dict[str, str] = {}
    if candidate.name:
        updates["name"] = _templatize(candidate.name, input_params)
    if candidate.text:
        updates["text"] = _templatize(candidate.text, input_params)
    return candidate.model_copy(update=updates) if updates else candidate


def _build_step_locator(
    action_locator: Locator | None,
    result,
    input_params: dict[str, str],
) -> Locator | None:
    if action_locator is None:
        return None
    candidates = [_templatize_candidate(c, input_params) for c in action_locator.candidates]
    if result is not None:
        for alt in result.alternate_candidates:
            candidates.append(_templatize_candidate(alt, input_params))
    return Locator(candidates=candidates)


def _auto_description(action) -> str:
    if action.locator and action.locator.candidates:
        target = action.locator.candidates[0].describe()
    else:
        target = action.value or ""
    return f"{action.kind} {target}".strip()


def _infer_checkpoint(run: DiscoveryRun, last_locator_step: Step | None) -> Checkpoint:
    """Default: the last extract/wait_for step's top candidate is a reasonable proxy for
    'we reached the expected final state'. A reviewer can always override this explicitly."""
    description = run.checkpoint or "Reached the expected final state."
    if last_locator_step is not None and last_locator_step.locator and last_locator_step.locator.candidates:
        return Checkpoint(description=description, detect=last_locator_step.locator.candidates[0])
    # No locator-bearing step at all (shouldn't happen for a real capability, but don't crash).
    return Checkpoint(
        description=description,
        detect=LocatorCandidate(strategy="text", text=description, confidence=0.1),
    )
