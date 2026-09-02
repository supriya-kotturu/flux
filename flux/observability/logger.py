"""Structured, append-only run logging.

Every discovery run and every replay run gets one JSONL file under
``evidence/runs/<run_id>/log.jsonl`` — one line per event. This is the
"structured log of what the agent did and why" required by brief §3.5.

Redaction is a seam, not an afterthought: every event passes through an
injected ``redactor`` before it's serialized *or* echoed to stdout, so
nothing sensitive reaches disk or the console. Defaults to
flux.safety.redaction.redact_log_event — pass a different one only to
override it (e.g. a test asserting on the pre-redaction shape).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Controller = Literal["agent", "human", "system"]

Redactor = Callable[[dict[str, Any]], dict[str, Any]]


def new_run_id(prefix: str) -> str:
    """e.g. 'discover-20260901-153000-3f9a1c' """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:6]}"


class RunLogger:
    """One instance per discovery or replay run.

    Usage:
        logger = RunLogger(run_id, evidence_root)
        logger.event("step_started", controller="agent", step_index=2, action="click")
    """

    def __init__(
        self,
        run_id: str,
        evidence_root: Path,
        redactor: Redactor | None = None,
        echo_to_stdout: bool = True,
    ) -> None:
        self.run_id = run_id
        self.run_dir = evidence_root / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "log.jsonl"
        if redactor is None:
            from flux.safety.redaction import redact_log_event

            redactor = redact_log_event
        self._redactor = redactor
        self._echo = echo_to_stdout

    def event(self, event_type: str, controller: Controller = "system", **data: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event_type": event_type,
            "controller": controller,
            **data,
        }
        record = self._redactor(record)
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self._echo:
            # Built from the already-redacted `record`, not the original
            # `data` kwargs — the console is as much a persistence surface
            # as the file when a run is being watched or piped to a log.
            shown = {k: v for k, v in record.items() if k not in ("ts", "run_id", "event_type", "controller")}
            print(f"[{record['ts']}] {controller:6s} {event_type:20s} {shown}")

    def evidence_path(self, filename: str) -> Path:
        """Path for a richer artifact (screenshot, ax-tree dump) tied to this run."""
        return self.run_dir / filename
