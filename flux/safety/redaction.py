"""Keeps secrets and raw sensitive data out of what gets persisted — brief §3.4.

Two mechanisms, because they act at different times on different shapes:

- `redact_log_event` scrubs a structured log event (the `redactor` seam in
  flux.observability.logger) before it's written — the value typed into a
  field the model itself told us the identity of (its locator's target
  label, e.g. "Password").
- `field_looks_sensitive` / `secret_ref_name` drive a *recording-time*
  decision in the artifact recorder (flux.artifact.recorder): a step whose
  target field looks like a credential never gets its discovered value
  written into the artifact at all — literal or templated. It's replaced
  with a `{{secret:name}}` reference that only resolves from an explicit
  secrets mapping supplied out of band at replay time (env vars, a secret
  manager) — never from the artifact file, and never logged, since
  `replay()` never passes `secrets` to anything that logs its input.
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_FIELD_HINTS = (
    "password", "passwd", "pwd", "secret", "token", "ssn", "social security",
    "pin", "cvv", "cvc", "credential", "api key", "api_key", "apikey",
)

REDACTED = "[REDACTED]"

_SENSITIVE_RE = re.compile("|".join(re.escape(h) for h in SENSITIVE_FIELD_HINTS), re.IGNORECASE)


def field_looks_sensitive(label: str | None) -> bool:
    return bool(label) and bool(_SENSITIVE_RE.search(label))


def secret_ref_name(label: str) -> str:
    """'Password' -> 'password', 'Social Security Number' -> 'social_security_number'."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "secret"


def redact_log_event(event: dict[str, Any]) -> dict[str, Any]:
    """Targeted, not a generic recursive walk: our log events carry a
    `tool_input`-shaped `input` dict where the field identity (locator's
    `value`) and the typed content (`text`) are siblings, not one self-
    describing key — a key-name-based redactor alone would miss it."""
    tool_input = event.get("input")
    if not isinstance(tool_input, dict):
        return event
    locator = tool_input.get("locator")
    target_label = locator.get("value") if isinstance(locator, dict) else None
    if field_looks_sensitive(target_label) and "text" in tool_input:
        event = dict(event)
        event["input"] = {**tool_input, "text": REDACTED}
    return event
