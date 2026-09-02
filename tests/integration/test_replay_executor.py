"""Deterministic replay against the real mock bank — no LLM anywhere in this file.

Builds one real artifact (discovery + record, same as Phase 3/4's tests)
and then exercises brief §3.3's three-way result contract against it:
success with a *different* input than it was recorded with (proving
parameterization is real, not coincidental), a declared business outcome,
a hard failure with debuggable detail, and the requires_approval gate for
an irreversible capability.
"""

from __future__ import annotations

from pathlib import Path

from flux.agent.loop import run_discovery
from flux.artifact.recorder import record
from flux.artifact.schema import NamedOutcome
from flux.observability.logger import RunLogger, new_run_id
from flux.replay.executor import replay
from flux.surface.base import LocatorCandidate
from flux.surface.browser import BrowserSurface

from tests.integration.test_discovery_loop import FakeLLMClient, _locator


def _logger(tmp_path: Path, prefix: str = "test-replay") -> RunLogger:
    return RunLogger(new_run_id(prefix), evidence_root=tmp_path, echo_to_stdout=False)


def _record_lookup_balance_artifact(page, mock_bank_server, tmp_path):
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
            "locator": _locator("table_row_value", "Savings Balance"),
            "output_name": "savings_balance",
            "reasoning": (
                "read the value from the Savings Balance row - using the row's own "
                "label, not the discovered figure, so this still works for any member"
            ),
        }),
        ("goal_complete", {"outputs": {"savings_balance": "$4210.55"}, "checkpoint": "member detail page shows savings balance"}),
    ]
    run = run_discovery(
        goal="look up member 10001 and read their savings balance",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient(script),
        logger=_logger(tmp_path, "discover-for-replay"),
        max_steps=10,
    )
    assert run.success, run.give_up_reason

    return record(
        run,
        name="lookup_member_savings_balance",
        description="Look up a member by ID and read their savings balance.",
        base_url=mock_bank_server,
        vendor_product="meridian-core-banking",
        input_params={"member_id": "10001"},
        known_outcomes=[
            NamedOutcome(
                name="member_not_found",
                description="No member matches the given ID.",
                detect=LocatorCandidate(strategy="text", text="No members matched", confidence=0.9),
            ),
        ],
    )


def test_replay_succeeds_with_a_different_input_than_it_was_recorded_with(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)

    result = replay(
        artifact,
        params={"member_id": "10002"},  # NOT the member this was recorded against
        surface=BrowserSurface(page),
        logger=_logger(tmp_path),
    )

    assert result.kind == "success"
    assert result.outputs == {"savings_balance": "$980.00"}  # member 10002's real balance, not 10001's


def test_replay_detects_a_declared_business_outcome_not_a_crash(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)

    result = replay(
        artifact,
        params={"member_id": "77777"},  # not seeded - no search results
        surface=BrowserSurface(page),
        logger=_logger(tmp_path),
    )

    assert result.kind == "business_outcome"
    assert result.name == "member_not_found"


def test_replay_hard_failure_reports_step_expected_and_observed(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)
    # Corrupt the extract step's locator so nothing can ever resolve it, and
    # it doesn't match any known outcome either - a genuine hard failure.
    broken = artifact.model_copy(deep=True)
    for step in broken.steps:
        if step.kind == "extract":
            step.locator.candidates = [
                LocatorCandidate(strategy="text", text="Definitely Not On This Page", confidence=0.9)
            ]

    result = replay(broken, params={"member_id": "10002"}, surface=BrowserSurface(page), logger=_logger(tmp_path))

    assert result.kind == "failure"
    assert result.category == "action"
    extract_step = next(s for s in broken.steps if s.kind == "extract")
    assert result.step_index == extract_step.index
    assert result.expected  # non-empty: the step's description
    assert "locator candidates tried" in result.detail


def test_replay_blocks_an_unapproved_irreversible_artifact(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    script = [
        ("type", {"locator": _locator("label", "Username"), "text": "operator", "reasoning": "log in"}),
        ("type", {"locator": _locator("label", "Password"), "text": "letmein", "reasoning": "log in"}),
        ("click", {"locator": _locator("role", "Sign In", role="button"), "reasoning": "submit login"}),
        ("navigate", {"url": f"{mock_bank_server}/member/10003"}),
        ("click", {
            "locator": _locator("role", "Open New Sub-Account", role="link"),
            "reasoning": "start opening a sub-account",
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
        ("extract", {
            "locator": _locator("text", "opened successfully"),
            "output_name": "confirmation_text",
            "reasoning": "verify the account was actually created before declaring done - this becomes the checkpoint",
        }),
        ("goal_complete", {"outputs": {}, "checkpoint": "sub-account opened successfully"}),
    ]
    run = run_discovery(
        goal="open a new sub-account for member 10003 with a $50 deposit",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient(script),
        logger=_logger(tmp_path, "discover-irreversible"),
        max_steps=10,
    )
    assert run.success

    artifact = record(
        run, name="open_sub_account", description="Open a new sub-account for a member.",
        base_url=mock_bank_server, vendor_product="meridian-core-banking",
        input_params={"member_id": "10003"},
    )
    assert artifact.requires_approval is True

    blocked = replay(artifact, params={"member_id": "10003"}, surface=surface, logger=_logger(tmp_path, "replay-blocked"))
    assert blocked.kind == "failure"
    assert blocked.category == "policy"

    # A fresh page/surface for the approved attempt - the blocked attempt above
    # never navigated anywhere, but starting clean keeps this test independent.
    approved_result = replay(
        artifact, params={"member_id": "10003"}, surface=surface, logger=_logger(tmp_path, "replay-approved"),
        approved=True,
    )
    assert approved_result.kind == "success"
