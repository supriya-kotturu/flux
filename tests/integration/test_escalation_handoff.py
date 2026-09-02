"""The handoff mechanism end to end: a replay step gets stuck, an
intervention request is raised (with a real, resolvable DevTools URL for
the live session), a human bridges the gap by acting directly on that same
session, and replay resumes from the next step to a correct final result.

This is deliberately built on the real ControlPlaneStore + BrowserSurface,
not mocks — "the handoff mechanism and control-transfer model" (brief §3.6)
is the thing this file exists to prove is real.
"""

from __future__ import annotations

from pathlib import Path

from flux.escalation.detector import replay_should_escalate
from flux.escalation.handoff import ControlPlaneStore, get_devtools_url
from flux.observability.logger import RunLogger, new_run_id
from flux.replay.executor import replay
from flux.surface.base import Action
from flux.surface.browser import BrowserSurface

from tests.integration.conftest import TEST_CDP_PORT
from tests.integration.test_discovery_loop import _locator
from tests.integration.test_replay_executor import MOCK_LOGIN_SECRETS, _record_lookup_balance_artifact


def _logger(tmp_path: Path, prefix: str) -> RunLogger:
    return RunLogger(new_run_id(prefix), evidence_root=tmp_path, echo_to_stdout=False)


def test_devtools_url_resolves_against_a_real_launched_session(page):
    # `page` comes from pytest-playwright, whose session browser is launched
    # with --remote-debugging-port via the browser_type_launch_args override
    # in conftest.py - the real CDP endpoint a human would attach to.
    page.goto("about:blank")
    url = get_devtools_url(TEST_CDP_PORT)
    assert url is not None
    # Chrome may return either a relative front-end path (served locally,
    # prefixed onto our own host:port) or an absolute hosted-frontend URL
    # that connects back via its own ws= query param - either way, it has
    # to reference the live session's own CDP port to be the same session.
    assert url.startswith("http")
    assert str(TEST_CDP_PORT) in url


def test_full_handoff_stuck_step_human_bridges_it_replay_resumes(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)
    member_id_step = next(
        s for s in artifact.steps if s.locator and s.locator.candidates[0].name == "Member ID or last name"
    )

    # Break the member-ID field's locator - simulates drift automation can't
    # resolve on its own (e.g. the field got renamed on this tenant's variant).
    broken = artifact.model_copy(deep=True)
    for step in broken.steps:
        if step.index == member_id_step.index:
            step.locator.candidates = [
                _bad_candidate() for _ in [None]
            ]

    surface = BrowserSurface(page)
    logger = _logger(tmp_path, "replay-stuck")

    result = replay(broken, params={"member_id": "10002"}, surface=surface, logger=logger, secrets=MOCK_LOGIN_SECRETS)

    assert result.kind == "failure"
    assert replay_should_escalate(result)
    assert result.step_index == member_id_step.index
    assert "screenshot" in result.evidence_paths
    assert "ax_tree" in result.evidence_paths

    # --- escalate: raise the intervention request a human would act on ---
    store = ControlPlaneStore(directory=tmp_path / "escalations")
    request = store.raise_request(
        run_id=logger.run_id, capability=broken.name,
        step_description=broken.steps[member_id_step.index].description,
        reason=f"{result.category}: {result.observed}",
        screenshot_path=result.evidence_paths.get("screenshot"),
        ax_tree_path=result.evidence_paths.get("ax_tree"),
    )
    assert request.status == "pending"
    assert request in store.list_pending()
    store.take_control(request.id)

    # --- a human, driving the SAME session (not a fresh one), does only what
    # the broken step couldn't - types the member ID - and hands back control.
    # Automation resumes the rest of the recorded flow on its own.
    _human_bridges_the_gap(surface, member_id="10002")

    resumed_request = store.resume(request.id, human_actions_summary="typed the member ID manually")
    assert resumed_request.status == "resumed"

    # --- replay resumes on the same session from the next recorded step ---
    final = replay(
        broken, params={"member_id": "10002"}, surface=surface, logger=_logger(tmp_path, "replay-resumed"),
        secrets=MOCK_LOGIN_SECRETS, resume_from_step=member_id_step.index + 1,
    )

    assert final.kind == "success"
    assert final.outputs == {"savings_balance": "$980.00"}


def _bad_candidate():
    from flux.surface.base import LocatorCandidate

    return LocatorCandidate(strategy="label", name="This Field Does Not Exist", confidence=0.85)


def _human_bridges_the_gap(surface: BrowserSurface, member_id: str) -> None:
    from flux.surface import locator as loc

    surface.act(Action(kind="type", locator=loc.make(loc.label("Member ID or last name")), value=member_id))
