"""Richer failure evidence beyond the structured log line — brief §3.5's
"at least one richer signal on failure (screenshot, DOM snapshot, trace)".

Captures a screenshot and the current accessibility-tree snapshot,
written under the run's own evidence directory (RunLogger.evidence_path)
so they sit next to the log that references them by filename. Best-effort
by design: a capture failure gets logged and never masks or replaces the
real failure it was trying to document.

This doubles as the context payload for an escalation's intervention
request (flux.escalation, Phase 8) — "what did the screen look like when
it got stuck" is the same question either way.
"""

from __future__ import annotations

from flux.observability.logger import RunLogger
from flux.surface.base import Surface


def capture_failure_evidence(surface: Surface, logger: RunLogger, tag: str) -> dict[str, str]:
    paths: dict[str, str] = {}

    try:
        screenshot_path = logger.evidence_path(f"{tag}.png")
        screenshot_path.write_bytes(surface.screenshot())
        paths["screenshot"] = str(screenshot_path)
    except Exception as exc:  # noqa: BLE001 - evidence capture must never itself crash a run
        logger.event("evidence_capture_failed", controller="system", kind="screenshot", error=str(exc))

    try:
        ax_path = logger.evidence_path(f"{tag}.ax.txt")
        ax_path.write_text(surface.observe().ax_tree, encoding="utf-8")
        paths["ax_tree"] = str(ax_path)
    except Exception as exc:  # noqa: BLE001
        logger.event("evidence_capture_failed", controller="system", kind="ax_tree", error=str(exc))

    if paths:
        logger.event("evidence_captured", controller="system", tag=tag, **paths)
    return paths
