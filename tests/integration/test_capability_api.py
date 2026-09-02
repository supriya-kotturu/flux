"""Agent-facing capability interface — brief §8 stretch goal.

Drives the actual FastAPI app (not a reimplementation of its logic)
against a real artifact and the real replay executor.

FastAPI dispatches synchronous `def` route handlers (like `invoke_capability`)
to a worker thread even under `TestClient` — the same thing that happens
under real `uvicorn`. That thread is *not* the one pytest-playwright's own
Playwright instance lives on, so `get_surface_factory`'s real default
(`BrowserSurface.launch()`, a fresh independent Playwright instance) works
here rather than conflicting with it — unlike Phase 8's escalation tests,
which call `.launch()` directly from the main test thread and do have to
work around that conflict.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.api.server import app, get_artifacts_dir
from flux.artifact import store

from tests.integration.test_replay_executor import MOCK_LOGIN_SECRETS, _record_lookup_balance_artifact


@pytest.fixture()
def api_client(page, mock_bank_server, tmp_path):
    artifact = _record_lookup_balance_artifact(page, mock_bank_server, tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    store.save(artifact, directory=artifacts_dir)

    app.dependency_overrides[get_artifacts_dir] = lambda: artifacts_dir
    try:
        yield TestClient(app), artifact
    finally:
        app.dependency_overrides.clear()


def test_list_capabilities_includes_the_saved_artifact(api_client):
    client, artifact = api_client
    resp = client.get("/capabilities")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert artifact.name in names
    summary = next(c for c in resp.json() if c["name"] == artifact.name)
    assert "member_id" in summary["input_schema"]
    assert "savings_balance" in summary["output_schema"]
    assert summary["required_secrets"] == ["password"]


def test_get_capability_detail(api_client):
    client, artifact = api_client
    resp = client.get(f"/capabilities/{artifact.name}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == artifact.description
    assert body["requires_approval"] is False


def test_get_unknown_capability_is_404(api_client):
    client, _ = api_client
    resp = client.get("/capabilities/does_not_exist")
    assert resp.status_code == 404


def test_invoke_succeeds_with_a_different_input_than_recorded(api_client):
    client, artifact = api_client
    resp = client.post(
        f"/capabilities/{artifact.name}/invoke",
        json={"params": {"member_id": "10002"}, "secrets": MOCK_LOGIN_SECRETS},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "success"
    assert body["outputs"] == {"savings_balance": "$980.00"}


def test_invoke_reports_business_outcome_not_a_500(api_client):
    client, artifact = api_client
    resp = client.post(
        f"/capabilities/{artifact.name}/invoke",
        json={"params": {"member_id": "77777"}, "secrets": MOCK_LOGIN_SECRETS},
    )
    assert resp.status_code == 200  # a business outcome is a normal response, not an error
    body = resp.json()
    assert body["kind"] == "business_outcome"
    assert body["name"] == "member_not_found"


def test_invoke_without_required_secret_reports_a_policy_failure(api_client):
    client, artifact = api_client
    resp = client.post(
        f"/capabilities/{artifact.name}/invoke",
        json={"params": {"member_id": "10002"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "failure"
    assert body["category"] == "policy"
