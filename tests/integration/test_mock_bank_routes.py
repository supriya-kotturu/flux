"""Route-level tests for the mock bank portal — HTTP layer only, no browser.

These exist to pin down the mock app's own behavior (Phase 1) independent
of the automation stack that will drive it later (Phase 2+). Every
deliberately-seeded runtime condition (not-found, permission-denied,
session-timeout, slow-load, validation error) gets a test here so the
error-taxonomy work in Phase 5 has a known-good surface to replay against.
"""

from __future__ import annotations

import pytest

from mock_bank import data
from mock_bank.app import app as flask_app
from mock_bank.data import store


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    store.reset()
    with flask_app.test_client() as c:
        yield c
    store.reset()


def _login(client):
    return client.post("/login", data={"username": "operator", "password": "letmein"}, follow_redirects=True)


def test_search_redirects_to_login_when_unauthenticated(client):
    resp = client.get("/search")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_rejects_wrong_credentials(client):
    resp = client.post("/login", data={"username": "operator", "password": "wrong"})
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def test_login_accepts_valid_credentials_and_reaches_search(client):
    resp = _login(client)
    assert resp.status_code == 200
    assert b"Member Search" in resp.data


def test_search_finds_seeded_member(client):
    _login(client)
    resp = client.post("/search", data={"query": "10001"})
    assert b"Nguyen" in resp.data


def test_member_detail_happy_path_shows_balance(client):
    _login(client)
    resp = client.get("/member/10001")
    assert b"4210.55" in resp.data


def test_member_not_found_is_a_business_outcome_not_a_crash(client):
    _login(client)
    resp = client.get("/member/99999")
    assert resp.status_code == 200
    assert b"No member found with ID 99999" in resp.data


def test_permission_denied_member(client):
    _login(client)
    resp = client.get(f"/member/{data.PERMISSION_DENIED_ID}")
    assert resp.status_code == 200
    assert b"Access Denied" in resp.data


def test_session_timeout_member_returns_to_login_with_notice(client):
    _login(client)
    resp = client.get(f"/member/{data.SESSION_TIMEOUT_ID}")
    assert resp.status_code == 200
    assert b"Your session has expired" in resp.data
    # session was actually cleared server-side, not just a page that says so
    resp2 = client.get("/search")
    assert resp2.status_code == 302


def test_slow_load_member_eventually_responds(client, monkeypatch):
    monkeypatch.setattr(data, "SLOW_LOAD_SECONDS", 0)
    _login(client)
    resp = client.get(f"/member/{data.SLOW_LOAD_ID}")
    assert resp.status_code == 200
    assert b"Slow Loader" in resp.data


def test_sub_account_validation_error_below_minimum_deposit(client):
    _login(client)
    resp = client.post(
        "/member/10001/sub-account/new",
        data={"account_type": "savings", "initial_deposit": "5.00"},
    )
    assert resp.status_code == 200
    assert b"Minimum opening deposit is $25.00" in resp.data


def test_sub_account_validation_error_non_numeric_deposit(client):
    _login(client)
    resp = client.post(
        "/member/10001/sub-account/new",
        data={"account_type": "savings", "initial_deposit": "not-a-number"},
    )
    assert resp.status_code == 200
    assert b"must be a dollar amount" in resp.data


def test_sub_account_full_flow_reaches_confirmation_screen(client):
    _login(client)
    resp = client.post(
        "/member/10001/sub-account/new",
        data={"account_type": "savings", "initial_deposit": "100.00"},
        follow_redirects=True,
    )
    assert b"Confirm New Sub-Account" in resp.data

    resp = client.post("/member/10001/sub-account/confirm", follow_redirects=True)
    assert resp.status_code == 200
    assert b"opened successfully" in resp.data
    assert b"SA-10001-0001" in resp.data
