"""Playwright accessibility-snapshot capture — the *observation* half of the surface.

The *action* half (flux.surface.locator) deliberately doesn't parse this
text back apart to find elements — it asks Playwright's own role/label/text
locators to do that, which already implement robust accessible-name
computation and auto-waiting. This module's only job is giving the
discovery agent (Phase 3) and the run log (Phase 7) a compact, readable
snapshot of what's currently on screen.
"""

from __future__ import annotations

from playwright.sync_api import Page

_MAX_CHARS = 8000  # keep prompts/log lines bounded on very large legacy pages


def capture_ax_tree(page: Page) -> str:
    snapshot = page.locator("body").aria_snapshot(timeout=5000)
    if len(snapshot) > _MAX_CHARS:
        snapshot = snapshot[:_MAX_CHARS] + "\n... (truncated)"
    return snapshot
