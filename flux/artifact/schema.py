"""The capability artifact — an agent-invocable contract, not a step recording.

This is the piece brief §3.2 calls a focal point of the evaluation, so the
shape here is deliberate:

- `input_schema`/`output_schema` are typed, so a calling agent (and a human
  reviewer) know what this capability needs and returns without reading the
  steps.
- Each `Step` carries a *ranked* `Locator` (see flux.surface.base), not a
  single selector, and `description` is the discovery model's own stated
  reasoning for the control it picked — both feed directly into review.
- `known_outcomes` are declared on the artifact itself, detectable the same
  way a locator resolves. That's what makes "no such member" a contract
  return value at replay time instead of something the replay engine has
  to infer from page text on the spot (brief §3.3's business-outcome vs.
  failure distinction).
- `requires_approval` is a real (if minimal) draft/approved gate: any
  irreversible step flips it on, and unattended replay refuses to run
  until an operator clears it (flux.safety, Phase 6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from flux.surface.base import ActionKind, DialogResponse, LocatorCandidate, Locator

ARTIFACT_SCHEMA_VERSION = 1

ParamType = Literal["string", "number", "boolean", "decimal"]


class ParamSpec(BaseModel):
    type: ParamType = "string"
    description: str = ""
    required: bool = True


class AppTarget(BaseModel):
    """Where this artifact runs, and what it's a recording *of*.

    `vendor_product` names the underlying app template (e.g.
    "meridian-core-banking") independent of any one institution running
    it. `tenant_id` is None for a base/vendor-template artifact, or set for
    a tenant-specific override — see docs/ROADMAP.md §3.7 for how these
    combine for multi-tenant reuse. Not exercised beyond the field existing
    — we build and record against a single instance.
    """

    base_url: str
    entry_url: str  # where replay first navigates — the discovery run's target, templated like any step
    vendor_product: str | None = None
    tenant_id: str | None = None


class Step(BaseModel):
    index: int
    kind: ActionKind
    locator: Locator | None = None  # None only for `navigate`
    value_template: str | None = None  # may contain {{param_name}} placeholders
    output_name: str | None = None  # set when kind == "extract"
    description: str  # the discovery model's own stated reasoning, or an auto-generated fallback
    risk_level: Literal["safe", "irreversible"] = "safe"
    on_dialog: DialogResponse | None = None  # replayed verbatim — the approval gate covers the decision, not each step


class NamedOutcome(BaseModel):
    """A business outcome the caller needs to know about — not a crash.

    `detect` reuses the same Locator-candidate machinery steps use: if it
    resolves against the current page during replay, this outcome has
    occurred.
    """

    name: str
    description: str
    detect: LocatorCandidate


class Checkpoint(BaseModel):
    """The condition that confirms the artifact actually reached its declared success state."""

    description: str
    detect: LocatorCandidate


class Provenance(BaseModel):
    """Links back to the discovery run this artifact was recorded from — never the raw transcript."""

    discovery_run_id: str
    recorded_at: datetime


class Artifact(BaseModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    id: str
    version: int = 1
    name: str
    description: str
    app_target: AppTarget
    input_schema: dict[str, ParamSpec] = Field(default_factory=dict)
    output_schema: dict[str, ParamSpec] = Field(default_factory=dict)
    steps: list[Step]
    known_outcomes: list[NamedOutcome] = Field(default_factory=list)
    checkpoint: Checkpoint
    provenance: Provenance
    requires_approval: bool = False
    created_at: datetime
