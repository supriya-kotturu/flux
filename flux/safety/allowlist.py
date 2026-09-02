"""Explicit, configurable allowlist of what the agent may act on — brief §3.4.

Enforced inside `BrowserSurface.act()` itself (flux.surface.browser), not
just at the discovery-loop call site — so a saved capability can't act
outside its original bounds at replay time just because nobody's watching
that particular call site. Discovery and replay share one enforcement
point instead of two policies that could drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from flux.surface.base import ActionKind

DEFAULT_ALLOWED_ACTION_KINDS: frozenset[ActionKind] = frozenset(
    {"click", "type", "select", "navigate", "wait_for", "extract", "exists"}
)


@dataclass(frozen=True)
class Allowlist:
    allowed_domains: frozenset[str]
    allowed_action_kinds: frozenset[ActionKind] = field(default=DEFAULT_ALLOWED_ACTION_KINDS)

    @classmethod
    def for_domain(cls, url: str, *extra_domains: str) -> "Allowlist":
        """Convenience: allow the given URL's own host, plus any extras (e.g. an SSO domain)."""
        host = urlsplit(url).hostname or ""
        return cls(allowed_domains=frozenset({host, *extra_domains}))

    def check_navigate(self, url: str) -> str | None:
        """None if allowed, else a human-readable denial reason."""
        host = urlsplit(url).hostname or ""
        if not any(host == d or host.endswith("." + d) for d in self.allowed_domains if d):
            return f"domain {host!r} is not in the allowlist ({sorted(d for d in self.allowed_domains if d)})"
        return None

    def check_action_kind(self, kind: ActionKind) -> str | None:
        if kind not in self.allowed_action_kinds:
            return f"action kind {kind!r} is not permitted by policy"
        return None
