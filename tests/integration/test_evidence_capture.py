"""Screenshot + ax-tree capture on failure — brief §3.5's "richer signal on failure".

Proves the files actually land on disk with real content, and that both
the discovery loop's stuck states and the replay executor's failure paths
trigger it — not just that the helper function works in isolation.
"""

from __future__ import annotations

from pathlib import Path

from flux.agent.loop import run_discovery
from flux.artifact.recorder import record
from flux.observability.evidence import capture_failure_evidence
from flux.observability.logger import RunLogger, new_run_id
from flux.replay.executor import replay
from flux.surface.base import Action, LocatorCandidate
from flux.surface.browser import BrowserSurface

from tests.integration.test_discovery_loop import FakeLLMClient, _locator
from tests.integration.test_replay_executor import MOCK_LOGIN_SECRETS, _record_lookup_balance_artifact


def _logger(tmp_path: Path, prefix: str) -> RunLogger:
    return RunLogger(new_run_id(prefix), evidence_root=tmp_path, echo_to_stdout=False)


def test_capture_failure_evidence_writes_screenshot_and_ax_tree(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    surface.act(Action(kind="navigate", value=f"{mock_bank_server}/login"))
    logger = _logger(tmp_path, "evidence")

    paths = capture_failure_evidence(surface, logger, tag="manual-check")

    assert set(paths) == {"screenshot", "ax_tree"}
    screenshot_path = Path(paths["screenshot"])
    ax_path = Path(paths["ax_tree"])
    assert screenshot_path.exists() and screenshot_path.stat().st_size > 0
    assert ax_path.exists()
    assert "Sign In" in ax_path.read_text(encoding="utf-8")


def test_discovery_dead_end_leaves_evidence_on_disk(page, mock_bank_server, tmp_path):
    surface = BrowserSurface(page)
    broken = ("click", {"locator": _locator("role", "This Button Does Not Exist", role="button")})
    logger = _logger(tmp_path, "discover-dead-end")

    run = run_discovery(
        goal="click something that isn't there",
        target=f"{mock_bank_server}/login",
        surface=surface,
        llm=FakeLLMClient([broken] * 10),
        logger=logger,
        max_steps=10,
    )

    assert run.stop_reason == "dead_end"
    evidence_files = list(logger.run_dir.glob("stop-dead_end.*"))
    assert any(f.suffix == ".png" for f in evidence_files)
    assert any(f.name.endswith(".ax.txt") for f in evidence_files)


def test_replay_hard_failure_leaves_evidence_on_disk(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)
    broken = artifact.model_copy(deep=True)
    for step in broken.steps:
        if step.kind == "extract":
            step.locator.candidates = [
                LocatorCandidate(strategy="text", text="Definitely Not On This Page", confidence=0.9)
            ]

    logger = _logger(tmp_path, "replay-failure")
    result = replay(
        broken, params={"member_id": "10002"}, surface=BrowserSurface(page), logger=logger,
        secrets=MOCK_LOGIN_SECRETS,
    )

    assert result.kind == "failure"
    extract_step = next(s for s in broken.steps if s.kind == "extract")
    evidence_files = list(logger.run_dir.glob(f"failure-step{extract_step.index}.*"))
    assert any(f.suffix == ".png" for f in evidence_files)
    assert any(f.name.endswith(".ax.txt") for f in evidence_files)
