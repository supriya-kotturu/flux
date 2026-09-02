"""Agent-facing capability interface — brief §8 stretch goal.

"Think of it as a capability an AI agent can call" (brief §3.2) is the
framing the whole artifact schema was built around from Phase 4 onward;
this is that framing taken literally. An AI agent discovers what's
available (`GET /capabilities`), reads one capability's typed contract
(`GET /capabilities/{name}`), and invokes it by name with typed args
(`POST /capabilities/{name}/invoke`).

Deliberately thin: this wraps the exact same artifact store and replay
executor `flux replay` already uses — no new execution engine, no job
queue, no state beyond what's already on disk. A capability call launches
a browser, replays deterministically, and returns, synchronously. Brief
§7 is explicit that scaling infrastructure isn't rewarded here; this is
the same "don't build it speculatively" call the rest of the project
makes, applied to the one stretch goal chosen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Callable, Union

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from flux.artifact import store
from flux.artifact.schema import Artifact
from flux.observability.logger import RunLogger, new_run_id
from flux.replay.errors import ReplayBusinessOutcome, ReplayFailure, ReplayResult, ReplaySuccess
from flux.replay.executor import replay as run_replay
from flux.safety.allowlist import Allowlist
from flux.surface.base import Surface
from flux.surface.browser import BrowserSurface

app = FastAPI(
    title="flux capability interface",
    description="Saved automation artifacts, exposed as capabilities an AI agent can discover and invoke.",
)

# A discriminated union (each variant carries its own `kind` literal) gives
# FastAPI's generated OpenAPI schema a real oneOf instead of a vague blob -
# a calling agent can tell success/business_outcome/failure apart from the
# schema alone, not just at runtime.
CapabilityResult = Annotated[
    Union[ReplaySuccess, ReplayBusinessOutcome, ReplayFailure], Field(discriminator="kind")
]

SurfaceFactory = Callable[[bool, Allowlist | None], Surface]


def _default_surface_factory(headless: bool, allowlist: Allowlist | None) -> Surface:
    return BrowserSurface.launch(headless=headless, allowlist=allowlist)


def get_artifacts_dir() -> Path:
    return store.DEFAULT_ARTIFACTS_DIR


def get_surface_factory() -> SurfaceFactory:
    return _default_surface_factory


class CapabilitySummary(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    requires_approval: bool
    required_secrets: list[str]


class InvokeRequest(BaseModel):
    params: dict[str, str] = Field(default_factory=dict, description="Typed per Artifact.input_schema")
    secrets: dict[str, str] = Field(
        default_factory=dict,
        description="For any {{secret:name}} the capability requires (see required_secrets) — never logged, never echoed back",
    )
    approved: bool = Field(default=False, description="Required if the capability's requires_approval is true")
    headless: bool = True


def _summarize(artifact: Artifact) -> CapabilitySummary:
    return CapabilitySummary(
        name=artifact.name,
        description=artifact.description,
        input_schema={k: v.model_dump() for k, v in artifact.input_schema.items()},
        output_schema={k: v.model_dump() for k, v in artifact.output_schema.items()},
        requires_approval=artifact.requires_approval,
        required_secrets=artifact.required_secrets,
    )


def _load_or_404(name: str, artifacts_dir: Path) -> Artifact:
    try:
        return store.load(name, directory=artifacts_dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no capability named {name!r}") from None


@app.get("/capabilities", response_model=list[CapabilitySummary])
def list_capabilities(artifacts_dir: Path = Depends(get_artifacts_dir)) -> list[CapabilitySummary]:
    """Every saved artifact, as a capability an agent could invoke."""
    return [_summarize(store.load(p.stem, directory=artifacts_dir)) for p in sorted(artifacts_dir.glob("*.json"))]


@app.get("/capabilities/{name}", response_model=CapabilitySummary)
def get_capability(name: str, artifacts_dir: Path = Depends(get_artifacts_dir)) -> CapabilitySummary:
    """One capability's typed contract: what it needs, what it returns, what it requires."""
    return _summarize(_load_or_404(name, artifacts_dir))


@app.post("/capabilities/{name}/invoke", response_model=CapabilityResult)
def invoke_capability(
    name: str,
    request: InvokeRequest,
    artifacts_dir: Path = Depends(get_artifacts_dir),
    surface_factory: SurfaceFactory = Depends(get_surface_factory),
) -> ReplayResult:
    """Deterministically replays the named capability — no LLM in this path,
    the same executor `flux replay` uses. Returns success / business_outcome
    / failure, never a raw exception for the good paths."""
    artifact = _load_or_404(name, artifacts_dir)

    allowlist = Allowlist.for_domain(artifact.app_target.base_url)
    logger = RunLogger(new_run_id("api-invoke"), evidence_root=Path("evidence"))
    surface = surface_factory(request.headless, allowlist)
    try:
        return run_replay(
            artifact, request.params, surface, logger,
            approved=request.approved, secrets=request.secrets,
        )
    finally:
        surface.close()
