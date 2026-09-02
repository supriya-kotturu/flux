"""Resolve a ranked `Locator` against a live Playwright page.

Candidates are tried highest-confidence first. Resolution asks Playwright's
own accessible-name-aware locators (`get_by_role`, `get_by_label`,
`get_by_text`) to do the matching — that's the same machinery an assistive
technology would use, which is exactly why it survives table-based legacy
markup with no test IDs: role and accessible name are computed from
semantics (labels, button text, ARIA), not from CSS classes or DOM shape.
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


def text(value: str, *, exact: bool = False, confidence: float = 0.6) -> LocatorCandidate:
    return LocatorCandidate(strategy="text", text=value, exact=exact, confidence=confidence)


def css(selector: str, *, confidence: float = 0.3) -> LocatorCandidate:
    return LocatorCandidate(strategy="structural_path", css=selector, confidence=confidence)


def coordinates(x: float, y: float, *, confidence: float = 0.05) -> LocatorCandidate:
    return LocatorCandidate(strategy="coordinates", x=x, y=y, confidence=confidence)


def make(*candidates: LocatorCandidate) -> Locator:
    return Locator(candidates=list(candidates))
