"""Human escalation & handoff — brief §3.6.

The seam: automation must be able to pause, cede control of the *live*
session (not a fresh one), and resume once a human has acted. Playwright
already exposes the running browser over CDP — `BrowserSurface.launch()`
opens `--remote-debugging-port` by default (flux.surface.browser) — so
"take control" is literally handing a human the URL to that same tab's
live DevTools front-end: the same mechanism `chrome://inspect` uses. No
custom co-browsing protocol, no second browser, no fresh session.

`ControlPlaneStore` answers "who's driving, and is this request still
open" for one intervention at a time: a JSON file on disk under
`evidence/escalations/`, so a second process (an operator running
`flux operator resume ...` in another terminal) and the paused automation
process agree on state without sharing memory.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Controller = Literal["agent", "human"]
RequestStatus = Literal["pending", "in_progress", "resumed"]

DEFAULT_ESCALATIONS_DIR = Path("evidence") / "escalations"


class InterventionRequest(BaseModel):
    """Carries what brief §3.6 asks for: which capability/goal, the current
    step, the current state, and why it stopped — enough for a human to
    act without having watched the run themselves."""

    id: str
    created_at: datetime
    run_id: str
    capability: str  # the goal (discovery) or artifact name (replay)
    step_description: str
    reason: str
    screenshot_path: str | None = None
    ax_tree_path: str | None = None
    devtools_url: str | None = None
    status: RequestStatus = "pending"
    controller: Controller = "agent"
    human_actions_summary: str | None = None
    resumed_at: datetime | None = None


def get_devtools_url(cdp_port: int) -> str | None:
    """The live tab's own DevTools front-end URL, resolved from the running
    browser's CDP HTTP endpoint. Opening this in any Chromium browser shows
    the actual rendered, interactive page — not a description of it."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2) as resp:
            pages = json.loads(resp.read())
    except (OSError, urllib.error.URLError, ValueError):
        return None
    for entry in pages:
        if entry.get("type") == "page" and entry.get("devtoolsFrontendUrl"):
            frontend = entry["devtoolsFrontendUrl"]
            return f"http://127.0.0.1:{cdp_port}{frontend}" if frontend.startswith("/") else frontend
    return None


class ControlPlaneStore:
    def __init__(self, directory: Path = DEFAULT_ESCALATIONS_DIR) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, request_id: str) -> Path:
        return self.directory / f"{request_id}.json"

    def raise_request(
        self,
        *,
        run_id: str,
        capability: str,
        step_description: str,
        reason: str,
        screenshot_path: str | None = None,
        ax_tree_path: str | None = None,
        cdp_port: int | None = None,
    ) -> InterventionRequest:
        request = InterventionRequest(
            id=uuid.uuid4().hex[:8],
            created_at=datetime.now(timezone.utc),
            run_id=run_id,
            capability=capability,
            step_description=step_description,
            reason=reason,
            screenshot_path=screenshot_path,
            ax_tree_path=ax_tree_path,
            devtools_url=get_devtools_url(cdp_port) if cdp_port else None,
        )
        self._save(request)
        return request

    def _save(self, request: InterventionRequest) -> None:
        self._path(request.id).write_text(request.model_dump_json(indent=2), encoding="utf-8")

    def get(self, request_id: str) -> InterventionRequest:
        return InterventionRequest.model_validate_json(self._path(request_id).read_text(encoding="utf-8"))

    def list_pending(self) -> list[InterventionRequest]:
        pending = []
        for path in sorted(self.directory.glob("*.json")):
            request = InterventionRequest.model_validate_json(path.read_text(encoding="utf-8"))
            if request.status != "resumed":
                pending.append(request)
        return pending

    def take_control(self, request_id: str) -> InterventionRequest:
        """A human signals they're now driving — recorded so the log/evidence
        trail attributes what happens next to a person, not the agent."""
        request = self.get(request_id)
        request.controller = "human"
        request.status = "in_progress"
        self._save(request)
        return request

    def resume(self, request_id: str, human_actions_summary: str = "") -> InterventionRequest:
        request = self.get(request_id)
        request.controller = "agent"
        request.status = "resumed"
        request.human_actions_summary = human_actions_summary
        request.resumed_at = datetime.now(timezone.utc)
        self._save(request)
        return request

    def wait_for_resume(
        self, request_id: str, poll_interval: float = 1.0, timeout: float | None = None
    ) -> InterventionRequest:
        start = time.monotonic()
        while True:
            request = self.get(request_id)
            if request.status == "resumed":
                return request
            if timeout is not None and time.monotonic() - start > timeout:
                raise TimeoutError(
                    f"intervention request {request_id} was not resumed within {timeout}s — "
                    f"still pending in {self.directory}, resume later with `flux operator resume {request_id}`"
                )
            time.sleep(poll_interval)
