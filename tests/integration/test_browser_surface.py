"""Exercises BrowserSurface + locator resolution against the live mock bank.

These pin down the two things Phase 2 exists to get right: the fallback
chain actually falls back (and reports which candidate won, not just that
*something* worked), and the default-dismiss dialog policy actually blocks
an irreversible action instead of silently letting a native confirm()
through.
"""

from __future__ import annotations

from flux.surface import locator as loc
from flux.surface.base import Action, Locator
from flux.surface.browser import BrowserSurface


def _login(surface: BrowserSurface, base_url: str) -> None:
    surface.act(Action(kind="navigate", value=f"{base_url}/login"))
    surface.act(Action(kind="type", locator=loc.make(loc.label("Username")), value="operator"))
    surface.act(Action(kind="type", locator=loc.make(loc.label("Password")), value="letmein"))
    result = surface.act(Action(kind="click", locator=loc.make(loc.role_name("button", "Sign In"))))
    assert result.ok, result.error


def test_role_name_locator_finds_and_clicks_search(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)
    obs = surface.observe()
    assert obs.ax_tree is not None
    result = surface.act(
        Action(kind="type", locator=loc.make(loc.label("Member ID or last name")), value="10001")
    )
    assert result.ok
    result = surface.act(Action(kind="click", locator=loc.make(loc.role_name("button", "Search"))))
    assert result.ok


def test_fallback_chain_skips_a_broken_top_candidate_and_records_which_won(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)

    result = surface.act(
        Action(
            kind="click",
            locator=Locator(
                candidates=[
                    loc.role_name("button", "Does Not Exist", confidence=0.9),
                    loc.role_name("link", "Member Search", confidence=0.5),
                ]
            ),
        )
    )
    assert result.ok
    assert result.resolved_via is not None
    assert result.resolved_via.confidence == 0.5
    assert result.resolved_via.name == "Member Search"


def test_extract_reads_balance_from_member_detail(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)
    surface.act(Action(kind="navigate", value=f"{mock_bank_server}/member/10001"))

    result = surface.act(Action(kind="extract", locator=loc.make(loc.text("4210.55"))))
    assert result.ok
    assert result.data is not None
    assert "4210.55" in result.data["text"]


def test_member_not_found_is_readable_as_an_observation_not_an_exception(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)
    result = surface.act(Action(kind="navigate", value=f"{mock_bank_server}/member/99999"))
    assert result.ok
    outcome = surface.act(
        Action(kind="extract", locator=loc.make(loc.text("No member found with ID 99999")))
    )
    assert outcome.ok


def test_dialog_defaults_to_dismiss_and_blocks_the_irreversible_action(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)
    surface.act(Action(kind="navigate", value=f"{mock_bank_server}/member/10001/sub-account/new"))
    surface.act(Action(kind="type", locator=loc.make(loc.label("Initial Deposit ($)")), value="100.00"))
    surface.act(Action(kind="click", locator=loc.make(loc.role_name("button", "Continue"))))

    result = surface.act(
        Action(kind="click", locator=loc.make(loc.role_name("button", "Confirm and Open Account")))
    )

    assert result.dialog_seen is not None
    assert "cannot be undone" in result.dialog_seen.message
    # dismissed -> onsubmit returned false -> still on the confirm screen, not success
    still_here = surface.act(Action(kind="extract", locator=loc.make(loc.text("Confirm New Sub-Account"))))
    assert still_here.ok


def test_dialog_explicit_accept_completes_the_irreversible_action(page, mock_bank_server):
    surface = BrowserSurface(page)
    _login(surface, mock_bank_server)
    surface.act(Action(kind="navigate", value=f"{mock_bank_server}/member/10002/sub-account/new"))
    surface.act(Action(kind="type", locator=loc.make(loc.label("Initial Deposit ($)")), value="50.00"))
    surface.act(Action(kind="click", locator=loc.make(loc.role_name("button", "Continue"))))

    result = surface.act(
        Action(
            kind="click",
            locator=loc.make(loc.role_name("button", "Confirm and Open Account")),
            on_dialog="accept",
        )
    )

    assert result.ok
    assert result.dialog_seen is not None
    success = surface.act(Action(kind="extract", locator=loc.make(loc.text("opened successfully"))))
    assert success.ok
