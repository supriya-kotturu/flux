"""Deterministic replay: an Artifact + input params, no LLM in the loop.

The production execution path (brief §3.3) — an AI agent triggers this,
not the discovery loop. Each step replays the same `Action` the discovery
model chose, with `{{param}}` placeholders substituted for this
invocation's concrete values (the exact reverse of what the recorder did).

Error handling is structural, not a try/except pyramid:
- Any step whose action fails to succeed triggers a `known_outcomes` check
  *before* it's treated as a hard failure — a locator failing to resolve is
  often exactly the signal that a declared business outcome (not found,
  access denied) occurred instead of the happy path.
- A transient timeout gets a bounded number of retries (a real, if narrow,
  instance of "wait/retry a transient load") before it's escalated.
- Every step reporting success still isn't trusted blindly: the final
  checkpoint is independently probed, and a `known_outcomes` check runs
  again if it doesn't hold.
- A candidate other than the top-ranked one winning a step is logged as
  drift, not swallowed — see docs/ROADMAP.md §3.3 / §5 for why that's the
  multi-tenant reuse signal, not just a curiosity.
"""

from __future__ import annotations

import time
from typing import Any

from flux.artifact.schema import Artifact, Step
from flux.observability.evidence import capture_failure_evidence
from flux.observability.logger import RunLogger
from flux.replay.checkpoint import detect
from flux.replay.errors import ReplayBusinessOutcome, ReplayFailure, ReplayResult, ReplaySuccess
from flux.surface.base import Action, Locator, LocatorCandidate, Surface

MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_S = 1.0


def replay(
    artifact: Artifact,
    params: dict[str, Any],
    surface: Surface,
    logger: RunLogger,
    *,
    approved: bool = False,
    secrets: dict[str, str] | None = None,
    resume_from_step: int | None = None,
) -> ReplayResult:
    """`resume_from_step` is the handoff seam (brief §3.6): after a human has
    taken control of this same `surface` via live session control and
    manually gotten past whatever blocked step `resume_from_step - 1` was,
    calling replay again with that index skips entry navigation and every
    earlier step — picking up on the *same* session, not a fresh one — and
    verifies the checkpoint like any other run.
    """
    # Only the business params are logged. `secrets` never reaches this call —
    # not because RunLogger would fail to redact it, but because a value that
    # never gets passed to anything that logs is stronger than one that has
    # to be caught and scrubbed.
    logger.event("replay_started", controller="system", artifact=artifact.name, params=params)
    secrets = secrets or {}

    missing_secrets = [s for s in artifact.required_secrets if s not in secrets]
    if missing_secrets:
        return ReplayFailure(
            category="policy",
            step_index=None,
            expected=f"all required secrets: {sorted(artifact.required_secrets)}",
            observed=f"missing: {missing_secrets}",
            detail="replay was not attempted; supply via a secrets mapping (env vars, a secret manager), never --param",
        )

    if artifact.requires_approval and not approved:
        logger.event("replay_blocked", controller="system", reason="requires_approval")
        return ReplayFailure(
            category="policy",
            step_index=None,
            expected="artifact approved for unattended replay",
            observed="requires_approval=True and approved=False",
            detail=(
                f"'{artifact.name}' contains at least one irreversible step "
                "(recorded with a confirmed dialog). Pass approved=True after review."
            ),
        )

    missing = [
        param_name
        for param_name, spec in artifact.input_schema.items()
        if spec.required and param_name not in params
    ]
    if missing:
        return ReplayFailure(
            category="policy",
            step_index=None,
            expected=f"all required input parameters: {sorted(artifact.input_schema)}",
            observed=f"missing: {missing}",
            detail="replay was not attempted",
        )

    substitutions = {**params, **{f"secret:{k}": v for k, v in secrets.items()}}

    if resume_from_step is None:
        entry_url = _substitute(artifact.app_target.entry_url, substitutions)
        nav = surface.act(Action(kind="navigate", value=entry_url))
        if not nav.ok:
            evidence_paths = capture_failure_evidence(surface, logger, tag="failure-entry-navigate")
            return ReplayFailure(
                category="action", step_index=None,
                expected=f"load {entry_url}", observed=nav.error or "navigation failed",
                evidence_paths=evidence_paths,
            )

        outcome = _check_known_outcomes(artifact, surface, step_index=None)
        if outcome is not None:
            logger.event("business_outcome", controller="system", name=outcome.name, step_index=None)
            return outcome
    else:
        logger.event("replay_resumed", controller="human", resume_from_step=resume_from_step)

    outputs: dict[str, Any] = {}
    remaining_steps = [s for s in artifact.steps if resume_from_step is None or s.index >= resume_from_step]
    for step in remaining_steps:
        result = _execute_step(step, substitutions, surface, logger)

        if not result.ok:
            outcome = _check_known_outcomes(artifact, surface, step_index=step.index)
            if outcome is not None:
                logger.event("business_outcome", controller="system", name=outcome.name, step_index=step.index)
                return outcome
            logger.event("replay_failed", controller="system", step_index=step.index, error=result.error)
            evidence_paths = capture_failure_evidence(surface, logger, tag=f"failure-step{step.index}")
            blocked = (result.error or "").startswith("blocked_by_allowlist")
            return ReplayFailure(
                category="policy" if blocked else "action",
                step_index=step.index,
                expected=step.description or f"{step.kind} to succeed",
                observed=result.error or "action did not succeed",
                detail=_candidates_tried(step),
                evidence_paths=evidence_paths,
            )

        if step.kind == "extract" and step.output_name:
            outputs[step.output_name] = (result.data or {}).get("text")

        logger.event("step_ok", controller="system", step_index=step.index, kind=step.kind)

    if not detect(surface, artifact.checkpoint.detect):
        outcome = _check_known_outcomes(artifact, surface, step_index=None)
        if outcome is not None:
            logger.event("business_outcome", controller="system", name=outcome.name, step_index=None)
            return outcome
        logger.event("checkpoint_failed", controller="system")
        evidence_paths = capture_failure_evidence(surface, logger, tag="failure-checkpoint")
        return ReplayFailure(
            category="checkpoint",
            step_index=None,
            expected=artifact.checkpoint.description,
            observed="checkpoint condition not detected on the final page",
            detail="",
            evidence_paths=evidence_paths,
        )

    logger.event("replay_succeeded", controller="system", outputs=outputs)
    return ReplaySuccess(outputs=outputs)


def _execute_step(step: Step, params: dict[str, Any], surface: Surface, logger: RunLogger):
    locator = _substitute_locator(step.locator, params) if step.locator else None
    action = Action(
        kind=step.kind,
        locator=locator,
        value=_substitute(step.value_template, params) if step.value_template else None,
        on_dialog=step.on_dialog,
    )

    attempt = 0
    while True:
        result = surface.act(action)
        is_transient_timeout = not result.ok and (result.error or "").startswith("timeout")
        if result.ok or not is_transient_timeout or attempt >= MAX_TRANSIENT_RETRIES:
            _log_drift_if_any(step, result, logger)
            return result
        attempt += 1
        logger.event("recoverable_retry", controller="system", step_index=step.index, attempt=attempt, reason=result.error)
        time.sleep(RETRY_BACKOFF_S)


def _log_drift_if_any(step: Step, result, logger: RunLogger) -> None:
    if not result.ok or result.resolved_via is None or not step.locator or not step.locator.candidates:
        return
    top_confidence = max(c.confidence for c in step.locator.candidates)
    if result.resolved_via.confidence < top_confidence:
        logger.event(
            "locator_drift", controller="system", step_index=step.index,
            resolved_via=result.resolved_via.describe(), top_ranked_confidence=top_confidence,
        )


def _check_known_outcomes(artifact: Artifact, surface: Surface, step_index: int | None) -> ReplayBusinessOutcome | None:
    for outcome in artifact.known_outcomes:
        if detect(surface, outcome.detect):
            return ReplayBusinessOutcome(name=outcome.name, description=outcome.description, step_index=step_index)
    return None


def _candidates_tried(step: Step) -> str:
    if not step.locator:
        return ""
    return "locator candidates tried: " + ", ".join(c.describe() for c in step.locator.candidates)


def _substitute(text: str, params: dict[str, Any]) -> str:
    for name, value in params.items():
        text = text.replace("{{" + name + "}}", str(value))
    return text


def _substitute_locator(locator: Locator, params: dict[str, Any]) -> Locator:
    candidates: list[LocatorCandidate] = []
    for candidate in locator.candidates:
        updates: dict[str, str] = {}
        if candidate.name and "{{" in candidate.name:
            updates["name"] = _substitute(candidate.name, params)
        if candidate.text and "{{" in candidate.text:
            updates["text"] = _substitute(candidate.text, params)
        candidates.append(candidate.model_copy(update=updates) if updates else candidate)
    return Locator(candidates=candidates)
