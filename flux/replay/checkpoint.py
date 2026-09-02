"""'Does this condition currently hold' — the one probe both business-outcome
detection and the final success checkpoint are built from.

Brief's glossary: "Checkpoint: a condition you assert to confirm you
actually reached the state you expected, rather than assuming the click
worked." A known outcome and a checkpoint are the same kind of assertion —
this module is that assertion, expressed through the Surface protocol so
it stays surface-agnostic like everything else in replay.
"""

from __future__ import annotations

from flux.surface.base import Action, Locator, LocatorCandidate, Surface


def detect(surface: Surface, candidate: LocatorCandidate) -> bool:
    result = surface.act(Action(kind="exists", locator=Locator(candidates=[candidate])))
    return result.ok
