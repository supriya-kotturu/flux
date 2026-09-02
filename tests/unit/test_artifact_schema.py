from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flux.artifact.schema import (
    Artifact,
    AppTarget,
    Checkpoint,
    ParamSpec,
    Provenance,
    Step,
)
from flux.artifact import store
from flux.surface.base import Locator, LocatorCandidate


def _minimal_artifact(name: str = "lookup_member_balance") -> Artifact:
    return Artifact(
        id=name,
        name=name,
        description="Look up a member and read their savings balance.",
        app_target=AppTarget(
            base_url="http://127.0.0.1:5055",
            entry_url="http://127.0.0.1:5055/login",
            vendor_product="meridian-core-banking",
        ),
        input_schema={"member_id": ParamSpec(type="string", description="The member ID to look up.")},
        output_schema={"savings_balance": ParamSpec(type="string")},
        steps=[
            Step(
                index=0,
                kind="type",
                locator=Locator(candidates=[LocatorCandidate(strategy="label", name="Member ID or last name", confidence=0.85)]),
                value_template="{{member_id}}",
                description="Enter the member ID to search for.",
            ),
            Step(
                index=1,
                kind="extract",
                locator=Locator(candidates=[LocatorCandidate(strategy="text", text="4210.55", confidence=0.5)]),
                output_name="savings_balance",
                description="Read the savings balance from the member record.",
            ),
        ],
        checkpoint=Checkpoint(
            description="Member detail page shows a savings balance.",
            detect=LocatorCandidate(strategy="text", text="Savings Balance", confidence=0.6),
        ),
        provenance=Provenance(discovery_run_id="discover-test-run", recorded_at=datetime.now(timezone.utc)),
        created_at=datetime.now(timezone.utc),
    )


def test_artifact_round_trips_through_json() -> None:
    artifact = _minimal_artifact()
    dumped = artifact.model_dump_json()
    restored = Artifact.model_validate_json(dumped)
    assert restored == artifact


def test_store_save_and_load(tmp_path: Path) -> None:
    artifact = _minimal_artifact()
    path = store.save(artifact, directory=tmp_path)
    assert path.exists()
    loaded = store.load(artifact.name, directory=tmp_path)
    assert loaded == artifact


def test_requires_approval_defaults_false() -> None:
    assert _minimal_artifact().requires_approval is False
