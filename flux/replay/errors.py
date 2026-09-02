"""The replay result contract — brief §3.3's three-way distinction, made structural.

A caller pattern-matches on `kind` rather than catching exceptions for the
good paths: "no such member" is a `ReplayBusinessOutcome`, not a crash, and
that's enforced by the type, not a docstring convention.
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class ReplaySuccess(BaseModel):
    kind: Literal["success"] = "success"
    outputs: dict[str, Any] = Field(default_factory=dict)


class ReplayBusinessOutcome(BaseModel):
    """An expected, named result the caller needs to know about — not a failure.

    e.g. "no such member" for a lookup capability. Declared on the artifact
    itself (`Artifact.known_outcomes`), detected the same way a locator
    resolves — never inferred ad hoc from page text at replay time.
    """

    kind: Literal["business_outcome"] = "business_outcome"
    name: str
    description: str
    step_index: int | None = None


class ReplayFailure(BaseModel):
    """A hard stop: something the replay engine couldn't recover from or wasn't allowed to attempt.

    `category` distinguishes *why* — "policy" (blocked before anything ran,
    e.g. missing approval), "action" (a step's action didn't succeed and no
    known outcome explained it), "checkpoint" (every step reported success
    but the final state doesn't match), "timeout" (a transient condition
    that outlasted the recoverable-retry budget).
    """

    kind: Literal["failure"] = "failure"
    category: Literal["policy", "action", "checkpoint", "timeout"]
    step_index: int | None
    expected: str
    observed: str
    detail: str = ""


ReplayResult = Union[ReplaySuccess, ReplayBusinessOutcome, ReplayFailure]
