from __future__ import annotations

from flux.safety.risk import classify
from flux.surface.base import Action
from flux.surface.locator import make, role_name


def test_dialog_confirmed_click_is_irreversible() -> None:
    action = Action(kind="click", locator=make(role_name("button", "Confirm and Open Account")), on_dialog="accept")
    assert classify(action) == "irreversible"


def test_ordinary_click_is_safe() -> None:
    action = Action(kind="click", locator=make(role_name("button", "Search")))
    assert classify(action) == "safe"


def test_dialog_dismissed_click_is_still_safe() -> None:
    action = Action(kind="click", locator=make(role_name("button", "Search")), on_dialog="dismiss")
    assert classify(action) == "safe"
