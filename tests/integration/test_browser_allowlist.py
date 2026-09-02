"""Allowlist enforcement wired into BrowserSurface.act() itself.

Proves the policy is enforced at the surface, not just at the call sites
that happen to remember to check it first — the same enforcement point
covers a discovery run and a replay run automatically.
"""

from __future__ import annotations

from flux.safety.allowlist import Allowlist
from flux.surface import locator as loc
from flux.surface.base import Action
from flux.surface.browser import BrowserSurface


def test_navigate_outside_the_allowlist_is_blocked_before_any_request(page, mock_bank_server):
    allowlist = Allowlist.for_domain(mock_bank_server)
    surface = BrowserSurface(page, allowlist=allowlist)

    result = surface.act(Action(kind="navigate", value="http://evil.example.com/phish"))

    assert result.ok is False
    assert result.error.startswith("blocked_by_allowlist")
    # never actually navigated
    assert "evil.example.com" not in page.url


def test_navigate_within_the_allowlist_is_permitted(page, mock_bank_server):
    allowlist = Allowlist.for_domain(mock_bank_server)
    surface = BrowserSurface(page, allowlist=allowlist)

    result = surface.act(Action(kind="navigate", value=f"{mock_bank_server}/login"))

    assert result.ok is True


def test_disallowed_action_kind_is_blocked(page, mock_bank_server):
    allowlist = Allowlist(
        allowed_domains=frozenset({"127.0.0.1"}),
        allowed_action_kinds=frozenset({"navigate", "extract", "exists"}),
    )
    surface = BrowserSurface(page, allowlist=allowlist)
    surface.act(Action(kind="navigate", value=f"{mock_bank_server}/login"))

    result = surface.act(Action(kind="click", locator=loc.make(loc.role_name("button", "Sign In"))))

    assert result.ok is False
    assert "not permitted by policy" in result.error


def test_no_allowlist_means_unrestricted(page, mock_bank_server):
    surface = BrowserSurface(page)  # no allowlist passed
    result = surface.act(Action(kind="navigate", value=f"{mock_bank_server}/login"))
    assert result.ok is True
