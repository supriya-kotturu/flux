from __future__ import annotations

from flux.safety.allowlist import Allowlist


def test_for_domain_derives_host_from_a_url() -> None:
    allowlist = Allowlist.for_domain("http://127.0.0.1:5055/login")
    assert allowlist.check_navigate("http://127.0.0.1:5055/member/10001") is None


def test_navigate_outside_allowed_domains_is_denied() -> None:
    allowlist = Allowlist.for_domain("http://127.0.0.1:5055/login")
    denial = allowlist.check_navigate("http://evil.example.com/phish")
    assert denial is not None
    assert "evil.example.com" in denial


def test_subdomains_of_an_allowed_domain_are_permitted() -> None:
    allowlist = Allowlist(allowed_domains=frozenset({"example.com"}))
    assert allowlist.check_navigate("https://sso.example.com/login") is None
    assert allowlist.check_navigate("https://example.com.attacker.net/") is not None


def test_extra_domains_can_be_added_eg_for_sso() -> None:
    allowlist = Allowlist.for_domain("http://127.0.0.1:5055/login", "sso.example.com")
    assert allowlist.check_navigate("https://sso.example.com/authorize") is None


def test_action_kind_outside_policy_is_denied() -> None:
    allowlist = Allowlist(allowed_domains=frozenset({"127.0.0.1"}), allowed_action_kinds=frozenset({"navigate", "extract", "exists"}))
    assert allowlist.check_action_kind("navigate") is None
    assert allowlist.check_action_kind("click") is not None
