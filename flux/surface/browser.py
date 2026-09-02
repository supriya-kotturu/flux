"""Playwright-backed implementation of `Surface`.

Two ways to get one:
- `BrowserSurface.launch(...)` starts its own Playwright/browser/context —
  what the CLI and the discovery agent use for a real run. Launched headed
  with a CDP debug port open by default, because Phase 8's human handoff
  needs to attach a real operator to this exact session later.
- `BrowserSurface(page)` wraps an existing Playwright `Page` — what tests
  use, so they can inject pytest-playwright's fixtures instead of spinning
  up a second browser per test.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Dialog, Page
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PwTimeoutError
from playwright.sync_api import sync_playwright

from flux.safety.allowlist import Allowlist
from flux.surface.accessibility import capture_ax_tree
from flux.surface.base import (
    Action,
    ActionResult,
    DialogInfo,
    DialogResponse,
    Observation,
    Surface,
)
from flux.surface.locator import resolve

DEFAULT_CDP_PORT = 9222


class BrowserSurface(Surface):
    def __init__(self, page: Page, allowlist: Allowlist | None = None) -> None:
        self._page = page
        self._armed_dialog_response: DialogResponse = "dismiss"
        self._last_dialog: DialogInfo | None = None
        self._owns: tuple[Any, Any, Any] | None = None
        self._allowlist = allowlist
        page.on("dialog", self._on_dialog)

    @classmethod
    def launch(
        cls, headless: bool = False, cdp_port: int | None = DEFAULT_CDP_PORT,
        allowlist: Allowlist | None = None,
    ) -> "BrowserSurface":
        playwright = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if cdp_port is not None:
            launch_kwargs["args"] = [f"--remote-debugging-port={cdp_port}"]
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()
        surface = cls(page, allowlist=allowlist)
        surface._owns = (playwright, browser, context)
        return surface

    @property
    def page(self) -> Page:
        return self._page

    def close(self) -> None:
        if self._owns is not None:
            playwright, browser, context = self._owns
            context.close()
            browser.close()
            playwright.stop()
            self._owns = None

    # --- Surface protocol ---

    def observe(self) -> Observation:
        return Observation(
            url=self._page.url,
            title=self._page.title(),
            ax_tree=capture_ax_tree(self._page),
            pending_dialog=self._last_dialog,
        )

    def act(self, action: Action) -> ActionResult:
        denial = self._check_allowlist_before(action)
        if denial is not None:
            return ActionResult(ok=False, error=f"blocked_by_allowlist: {denial}")

        self._last_dialog = None
        self._armed_dialog_response = action.on_dialog or "dismiss"
        try:
            result = self._dispatch(action)
        except PwTimeoutError as exc:
            result = ActionResult(ok=False, error=f"timeout waiting for target: {exc}")
        except PlaywrightError as exc:
            result = ActionResult(ok=False, error=str(exc))
        finally:
            self._armed_dialog_response = "dismiss"

        if result.ok and self._allowlist is not None:
            # Enforced on the *outcome*, not just the requested navigate: a
            # click that happens to land off-domain (an external link on an
            # otherwise-allowed page) is caught here even though the click
            # itself wasn't a navigate action.
            landed_denial = self._allowlist.check_navigate(self._page.url)
            if landed_denial is not None:
                result = ActionResult(ok=False, error=f"blocked_by_allowlist: landed outside allowlist — {landed_denial}")

        result.dialog_seen = self._last_dialog
        return result

    def _check_allowlist_before(self, action: Action) -> str | None:
        if self._allowlist is None:
            return None
        kind_denial = self._allowlist.check_action_kind(action.kind)
        if kind_denial is not None:
            return kind_denial
        if action.kind == "navigate" and action.value:
            return self._allowlist.check_navigate(action.value)
        return None

    # --- internals ---

    def _on_dialog(self, dialog: Dialog) -> None:
        self._last_dialog = DialogInfo(kind=dialog.type, message=dialog.message)
        if self._armed_dialog_response == "accept":
            dialog.accept()
        else:
            dialog.dismiss()

    def _dispatch(self, action: Action) -> ActionResult:
        if action.kind == "navigate":
            if not action.value:
                return ActionResult(ok=False, error="navigate requires a URL in `value`")
            self._page.goto(action.value, timeout=action.timeout_ms)
            return ActionResult(ok=True)

        if action.locator is None:
            return ActionResult(ok=False, error=f"{action.kind} requires a locator")

        if action.kind == "exists":
            # A pure presence probe — no auto-wait, no side effect. This is
            # how replay checks a business outcome or the final checkpoint:
            # `resolve()`'s `.count()` calls return immediately with whatever
            # the DOM currently has, so this never blocks a replay run.
            picked = resolve(self._page, action.locator)
            if picked is None:
                return ActionResult(ok=False, error="no candidate resolved")
            return ActionResult(ok=True, resolved_via=picked[0])

        picked = resolve(self._page, action.locator)
        if picked is None:
            return ActionResult(ok=False, error="no locator candidate resolved to exactly one element")
        candidate, pw_locator = picked

        # Captured now, against the live element, before any action (like a
        # click) might navigate away and take it with it. Best-effort: a
        # failure here never blocks the actual action.
        alternates = _derive_alternate_candidates(pw_locator) if pw_locator is not None else []

        if action.kind == "click":
            if pw_locator is not None:
                pw_locator.click(timeout=action.timeout_ms)
            else:
                self._page.mouse.click(candidate.x, candidate.y)  # type: ignore[arg-type]
            self._settle()
            return ActionResult(ok=True, resolved_via=candidate, alternate_candidates=alternates)

        if action.kind == "type":
            if pw_locator is None:
                return ActionResult(ok=False, error="type is not supported via coordinates")
            pw_locator.fill(action.value or "", timeout=action.timeout_ms)
            return ActionResult(ok=True, resolved_via=candidate, alternate_candidates=alternates)

        if action.kind == "select":
            if pw_locator is None:
                return ActionResult(ok=False, error="select is not supported via coordinates")
            pw_locator.select_option(action.value, timeout=action.timeout_ms)
            return ActionResult(ok=True, resolved_via=candidate, alternate_candidates=alternates)

        if action.kind == "wait_for":
            if pw_locator is None:
                return ActionResult(ok=False, error="wait_for is not supported via coordinates")
            pw_locator.wait_for(state="visible", timeout=action.timeout_ms)
            return ActionResult(ok=True, resolved_via=candidate, alternate_candidates=alternates)

        if action.kind == "extract":
            if pw_locator is None:
                return ActionResult(ok=False, error="extract is not supported via coordinates")
            text_value = _read_value(pw_locator)
            return ActionResult(ok=True, resolved_via=candidate, alternate_candidates=alternates, data={"text": text_value})

        return ActionResult(ok=False, error=f"unknown action kind: {action.kind}")

    def _settle(self) -> None:
        """Best-effort: let a click-triggered navigation land before the caller observes."""
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=2000)
        except PwTimeoutError:
            pass


def _read_value(pw_locator: Any) -> str:
    try:
        value = pw_locator.input_value(timeout=500)
        if value:
            return value
    except PlaywrightError:
        pass
    return pw_locator.inner_text(timeout=2000)


# Anchors on the nearest ancestor `id` when there is one, else walks up to
# 4 levels building an nth-of-type chain. Deliberately simple — this is a
# last-resort fallback candidate, not the primary strategy.
_CSS_PATH_JS = """
el => {
  function seg(node) {
    if (node.id) return '#' + CSS.escape(node.id);
    const tag = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (!parent) return tag;
    const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
    const idx = siblings.indexOf(node) + 1;
    return tag + ':nth-of-type(' + idx + ')';
  }
  const parts = [];
  let cur = el;
  let depth = 0;
  while (cur && cur.nodeType === 1 && depth < 4) {
    parts.unshift(seg(cur));
    if (cur.id) break;
    cur = cur.parentElement;
    depth++;
  }
  return parts.join(' > ');
}
"""

_MAX_ALT_TEXT_LEN = 80


def _derive_alternate_candidates(pw_locator: Any) -> list["LocatorCandidate"]:
    """Best-effort extra ways to find the element `pw_locator` just resolved to.

    Only called with the live element still in hand, at execution time —
    this can't be reconstructed after the fact once the page has moved on,
    which is exactly why it lives here and not in the artifact recorder.
    """
    from flux.surface.base import LocatorCandidate

    alternates: list[LocatorCandidate] = []
    try:
        raw_text = (pw_locator.inner_text(timeout=500) or "").strip()
        if raw_text and len(raw_text) <= _MAX_ALT_TEXT_LEN:
            alternates.append(LocatorCandidate(strategy="text", text=raw_text, confidence=0.5))
    except PlaywrightError:
        pass
    try:
        css_path = pw_locator.evaluate(_CSS_PATH_JS)
        if css_path:
            alternates.append(LocatorCandidate(strategy="structural_path", css=css_path, confidence=0.25))
    except PlaywrightError:
        pass
    return alternates
