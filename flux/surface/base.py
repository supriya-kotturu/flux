"""The perception/action contract every concrete surface implements.

Nothing above this module — the discovery loop, the artifact schema, the
replay executor, the safety layer — knows whether it's driving a browser,
a legacy frame-and-table web app, or (someday) a desktop app via OS
accessibility APIs. They only know `Observation` in, `Action` out,
`ActionResult` back. That's the seam brief §3.7 asks about: a new surface
means a new class implementing `Surface`, not a new replay engine.

Locators are ranked candidate lists, not single selectors. Discovery
records every candidate it could construct for a target (role+name, label,
visible text, a structural CSS path, raw coordinates as a last resort),
each with a confidence score. Replay tries them in confidence order and
reports which one actually resolved — a candidate other than rank-1
winning is a drift signal (see docs/ROADMAP.md §3.3), not something to
silently swallow.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

LocatorStrategy = Literal["role_name", "label", "text", "structural_path", "coordinates"]


class LocatorCandidate(BaseModel):
    """One way to find a control. `confidence` ranks it against siblings."""

    strategy: LocatorStrategy
    role: str | None = None  # role_name: ARIA role, e.g. "button", "textbox"
    name: str | None = None  # role_name / label: accessible name / label text
    text: str | None = None  # text: visible text content to match
    css: str | None = None  # structural_path: CSS selector — last-resort structural anchor
    x: float | None = None  # coordinates
    y: float | None = None  # coordinates
    exact: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    def describe(self) -> str:
        if self.strategy == "role_name":
            return f"role={self.role!r} name={self.name!r}"
        if self.strategy == "label":
            return f"label={self.name!r}"
        if self.strategy == "text":
            return f"text={self.text!r}"
        if self.strategy == "structural_path":
            return f"css={self.css!r}"
        return f"coordinates=({self.x}, {self.y})"


class Locator(BaseModel):
    """Ranked candidates for one target control. Highest confidence first."""

    candidates: list[LocatorCandidate]


class DialogInfo(BaseModel):
    """A native browser dialog (confirm/alert/prompt) encountered during an action."""

    kind: Literal["alert", "confirm", "prompt", "beforeunload"]
    message: str


class Observation(BaseModel):
    """What the surface currently sees. Never carries secrets — see flux.safety.redaction.

    `ax_tree` is Playwright's own accessibility-snapshot text (role +
    accessible name per node, indented) — the same representation an
    assistive technology would get. It's already the right shape for both
    an LLM prompt and a human-readable log line, so we don't re-model it
    into a parallel structured tree.
    """

    url: str
    title: str
    ax_tree: str
    pending_dialog: DialogInfo | None = None
    screenshot_ref: str | None = None


ActionKind = Literal["click", "type", "select", "navigate", "wait_for", "extract"]

# Safety default: an action never auto-accepts a native dialog unless the
# caller explicitly opts in for that one action. A step that turns out to
# need confirmation and wasn't told to expect one fails visibly (dialog
# dismissed, action didn't complete) rather than an irreversible
# confirmation sailing through unattended.
DialogResponse = Literal["accept", "dismiss"]


class Action(BaseModel):
    kind: ActionKind
    locator: Locator | None = None
    value: str | None = None  # type: text to enter / select: option value / navigate: URL / wait_for: none needed
    timeout_ms: int = 5000
    on_dialog: DialogResponse | None = None  # armed for exactly this action; default behavior is "dismiss"


class ActionResult(BaseModel):
    ok: bool
    resolved_via: LocatorCandidate | None = None
    # Extra ways the *same* resolved element could be found, captured live at
    # execution time (a post-hoc pass can't do this — the page has already
    # moved on). The artifact recorder (Phase 4) folds these in alongside
    # `resolved_via` to build each step's ranked fallback candidate list.
    alternate_candidates: list[LocatorCandidate] = Field(default_factory=list)
    data: dict[str, Any] | None = None
    error: str | None = None
    dialog_seen: DialogInfo | None = None


class Surface(Protocol):
    def observe(self) -> Observation: ...

    def act(self, action: Action) -> ActionResult: ...

    def close(self) -> None: ...
