from __future__ import annotations

from flux.safety.redaction import field_looks_sensitive, redact_log_event, secret_ref_name


def test_field_looks_sensitive_matches_known_hints() -> None:
    assert field_looks_sensitive("Password")
    assert field_looks_sensitive("Social Security Number")
    assert field_looks_sensitive("PIN")
    assert not field_looks_sensitive("Member ID or last name")
    assert not field_looks_sensitive(None)
    assert not field_looks_sensitive("")


def test_secret_ref_name_slugifies() -> None:
    assert secret_ref_name("Password") == "password"
    assert secret_ref_name("Social Security Number") == "social_security_number"
    assert secret_ref_name("  Weird!! Label--Name  ") == "weird_label_name"


def test_redact_log_event_scrubs_text_for_sensitive_target_field() -> None:
    event = {
        "event_type": "model_decided",
        "input": {"locator": {"by": "label", "value": "Password"}, "text": "letmein", "reasoning": "log in"},
    }
    redacted = redact_log_event(event)
    assert redacted["input"]["text"] == "[REDACTED]"
    assert redacted["input"]["reasoning"] == "log in"  # non-secret fields untouched


def test_redact_log_event_leaves_non_sensitive_fields_alone() -> None:
    event = {
        "event_type": "model_decided",
        "input": {"locator": {"by": "label", "value": "Member ID or last name"}, "text": "10001"},
    }
    assert redact_log_event(event) == event


def test_redact_log_event_tolerates_events_without_input() -> None:
    event = {"event_type": "discovery_started", "goal": "look something up"}
    assert redact_log_event(event) == event
