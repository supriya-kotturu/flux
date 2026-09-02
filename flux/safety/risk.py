"""Classifies whether an action is safe/reversible or risky/irreversible — brief §3.4.

MVP heuristic: a step is irreversible exactly when it was recorded with an
explicit `on_dialog="accept"`. The mock bank's own irreversible action
(opening a sub-account) is precisely the one gated behind a native
confirm() dialog, and during discovery a human reviewer already had to
read that dialog's wording and decide accepting it was correct — the
signal already exists, this just formalizes reading it.

A narrow starting point, not the final policy: see docs/ROADMAP.md §5
(Cuts) for what a fuller model — verb/route-pattern matching across a
wider action vocabulary, not just dialog-gated actions — would add.
"""

from __future__ import annotations

from typing import Literal

from flux.surface.base import Action

RiskLevel = Literal["safe", "irreversible"]


def classify(action: Action) -> RiskLevel:
    return "irreversible" if action.on_dialog == "accept" else "safe"
