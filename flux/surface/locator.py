"""Resolve a ranked `Locator` against a live Playwright page.

Candidates are tried highest-confidence first. Resolution asks Playwright's
own accessible-name-aware locators (`get_by_role`, `get_by_label`,
`get_by_text`) to do the matching — that's the same machinery an assistive
technology would use, which is exactly why it survives table-based legacy
markup with no test IDs: role and accessible name are computed from
semantics (labels, button text, ARIA), not from CSS classes or DOM shape.

`table_row_value` is the one strategy built specifically for the legacy
two-column label/value table the brief calls out as the norm in this
environment (e.g. "Savings Balance" | "$4,210.55"): it locates a `<tr>`
containing the given label text and targets that row's last cell. Unlike
matching the value's own text, this doesn't need to already know the
value — which matters because a *discovered* value (a balance, a status)
is exactly the kind of thing a recorded locator can't hardcode without
breaking replay for every other input.

`structural_path` (a CSS selector) and `coordinates` are explicit
fallbacks for when nothing about a control is nameable — expected to rank
low and to be a signal, when they win, that the target needs a better
locator recorded.

A candidate only "wins" if it resolves to exactly one element. Zero or
ambiguous (>1) matches fall through to the next candidate rather than
guessing.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator as PwLocator
from playwright.sync_api import Page

from flux.surface.base import Locator, LocatorCandidate


def resolve(page: Page, locator: Locator) -> tuple[LocatorCandidate, PwLocator | None] | None:
    """Returns (winning candidate, Playwright Locator or None for coordinates), or None."""
    ranked = sorted(locator.candidates, key=lambda c: c.confidence, reverse=True)
    for candidate in ranked:
        if candidate.strategy == "coordinates":
            if candidate.x is not None and candidate.y is not None:
                return candidate, None
            continue
        pw_locator = _build(page, candidate)
        if pw_locator is None:
            continue
        try:
            count = pw_locator.count()
        except PlaywrightError:
            continue
        if count == 1:
            return candidate, pw_locator
    return None


def _build(page: Page, candidate: LocatorCandidate) -> PwLocator | None:
    if candidate.strategy == "role_name" and candidate.role:
        if candidate.name:
            return page.get_by_role(candidate.role, name=candidate.name, exact=candidate.exact)
        return page.get_by_role(candidate.role)
    if candidate.strategy == "label" and candidate.name:
        return page.get_by_label(candidate.name, exact=candidate.exact)
    if candidate.strategy == "table_row_value" and candidate.name:
        # Deliberately not `tr:has-text(label) >> td >> last`: on a page whose
        # own chrome is nested tables (see mock_bank/templates/base.html —
        # exactly the "deeply nested tables" legacy reality brief §1 calls
        # out), an ancestor <tr> can *also* contain the label text anywhere
        # in its subtree and out-rank the real row. Anchoring on the label
        # cell's own exact accessible name, then taking its immediate <td>
        # sibling, stays scoped to the one row that actually has it.
        label_cell = page.get_by_role("cell", name=candidate.name, exact=True)
        return label_cell.locator("xpath=following-sibling::td[1]")
    if candidate.strategy == "text" and candidate.text:
        return page.get_by_text(candidate.text, exact=candidate.exact)
    if candidate.strategy == "structural_path" and candidate.css:
        return page.locator(candidate.css)
    return None


# --- convenience builders, used by tests and (later) the discovery agent's tools ---


def role_name(role: str, name: str, *, exact: bool = False, confidence: float = 0.9) -> LocatorCandidate:
    return LocatorCandidate(strategy="role_name", role=role, name=name, exact=exact, confidence=confidence)


def label(name: str, *, exact: bool = False, confidence: float = 0.85) -> LocatorCandidate:
    return LocatorCandidate(strategy="label", name=name, exact=exact, confidence=confidence)


def table_row_value(row_label: str, *, confidence: float = 0.55) -> LocatorCandidate:
    return LocatorCandidate(strategy="table_row_value", name=row_label, confidence=confidence)


def text(value: str, *, exact: bool = False, confidence: float = 0.6) -> LocatorCandidate:
    return LocatorCandidate(strategy="text", text=value, exact=exact, confidence=confidence)


def css(selector: str, *, confidence: float = 0.3) -> LocatorCandidate:
    return LocatorCandidate(strategy="structural_path", css=selector, confidence=confidence)


def coordinates(x: float, y: float, *, confidence: float = 0.05) -> LocatorCandidate:
    return LocatorCandidate(strategy="coordinates", x=x, y=y, confidence=confidence)


def make(*candidates: LocatorCandidate) -> Locator:
    return Locator(candidates=list(candidates))
