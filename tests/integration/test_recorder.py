"""Records a real discovery run (scripted fake LLM, live mock_bank) into an Artifact.

Proves the things the recorder exists for: the concrete "10001" used
during discovery is templated into {{member_id}} everywhere it appears,
the extract step's output shows up typed in output_schema, an irreversible
(dialog-confirmed) step flips requires_approval, and the whole thing
round-trips through the artifact store.
"""

from __future__ import annotations

from pathlib import Path

from flux.agent.loop import run_discovery
from flux.artifact.recorder import record
from flux.artifact.schema import AppTarget
from flux.artifact import store
from flux.observability.logger import RunLogger, new_run_id
from flux.surface.browser import BrowserSurface

from tests.integration.test_discovery_loop import FakeLLMClient, _locator


def _logger(tmp_path: Path) -> RunLogger:
    return RunLogger(new_run_id("test-discover"), evidence_root=tmp_path, echo_to_stdout=False)


def test_record_templates_the_concrete_input_value(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    script = [
        ("type", {"locator": _locator("label", "Username"), "text": "operator", "reasoning": "log in"}),
        ("type", {"locator": _locator("label", "Password"), "text": "letmein", "reasoning": "log in"}),
        ("click", {"locator": _locator("role", "Sign In", role="button"), "reasoning": "submit login"}),
        ("type", {
            "locator": _locator("label", "Member ID or last name"),
            "text": "10001",
            "reasoning": "search for the target member by ID",
        }),
        ("click", {"locator": _locator("role", "Search", role="button"), "reasoning": "run the search"}),
        ("click", {"locator": _locator("role", "Open Record", role="link"), "reasoning": "open the matched member's record"}),
        ("extract", {
            "locator": _locator("text", "4210.55"),
            "output_name": "savings_balance",
            "reasoning": "this is the savings balance figure the goal asked for",
        }),
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
    assert run.success

    artifact = record(
        run,
        name="lookup_member_savings_balance",
        description="Look up a member by ID and read their savings balance.",
        app_target=AppTarget(base_url=mock_bank_server, vendor_product="meridian-core-banking"),
        input_params={"member_id": "10001"},
    )

    # the concrete value used during discovery is gone, replaced by the placeholder
    step_texts = [s.value_template for s in artifact.steps if s.value_template]
    assert "{{member_id}}" in step_texts
    assert not any("10001" in (t or "") for t in step_texts)

    assert "savings_balance" in artifact.output_schema
    assert artifact.input_schema["member_id"].type == "string"
    assert artifact.requires_approval is False  # no dialog-confirmed step in this run
    assert artifact.provenance.discovery_run_id == run.run_id
    assert artifact.steps[0].description  # reasoning threaded through, not empty

    # round-trips cleanly through the store
    saved_path = store.save(artifact, directory=tmp_path)
    reloaded = store.load(artifact.name, directory=tmp_path)
    assert reloaded == artifact
    assert saved_path.exists()


def test_record_marks_dialog_confirmed_step_as_irreversible(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    script = [
        ("type", {"locator": _locator("label", "Username"), "text": "operator", "reasoning": "log in"}),
        ("type", {"locator": _locator("label", "Password"), "text": "letmein", "reasoning": "log in"}),
        ("click", {"locator": _locator("role", "Sign In", role="button"), "reasoning": "submit login"}),
        ("navigate", {"url": f"{mock_bank_server}/member/10002"}),
        ("click", {
            "locator": _locator("role", "Open New Sub-Account", role="link"),
            "reasoning": "start opening a sub-account for member 10002",
        }),
        ("type", {
            "locator": _locator("label", "Initial Deposit ($)"),
            "text": "50.00",
            "reasoning": "minimum required opening deposit",
        }),
        ("click", {"locator": _locator("role", "Continue", role="button"), "reasoning": "proceed to confirmation"}),
        ("click", {
            "locator": _locator("role", "Confirm and Open Account", role="button"),
            "reasoning": "confirm opening the account",
            "expect_dialog": "accept",
        }),
        ("goal_complete", {"outputs": {}, "checkpoint": "sub-account opened successfully"}),
    ]
    run = run_discovery(
        goal="open a new sub-account for member 10002 with a $50 deposit",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient(script),
        logger=_logger(tmp_path),
        max_steps=10,
    )
    assert run.success

    artifact = record(
        run,
        name="open_sub_account",
        description="Open a new sub-account for a member.",
        app_target=AppTarget(base_url=mock_bank_server, vendor_product="meridian-core-banking"),
        input_params={"member_id": "10002"},
    )

    assert artifact.requires_approval is True
    irreversible = [s for s in artifact.steps if s.risk_level == "irreversible"]
    assert len(irreversible) == 1
    assert irreversible[0].kind == "click"
