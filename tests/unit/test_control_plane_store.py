from __future__ import annotations

import threading
import time

import pytest

from flux.escalation.handoff import ControlPlaneStore


@pytest.fixture()
def store(tmp_path):
    return ControlPlaneStore(directory=tmp_path)


def test_raise_request_starts_pending_and_agent_controlled(store) -> None:
    request = store.raise_request(run_id="r1", capability="lookup_balance", step_description="step 3", reason="locator failed")
    assert request.status == "pending"
    assert request.controller == "agent"
    assert request in store.list_pending()


def test_take_control_flips_to_human_in_progress(store) -> None:
    request = store.raise_request(run_id="r1", capability="c", step_description="s", reason="r")
    updated = store.take_control(request.id)
    assert updated.controller == "human"
    assert updated.status == "in_progress"


def test_resume_flips_back_to_agent_and_records_summary(store) -> None:
    request = store.raise_request(run_id="r1", capability="c", step_description="s", reason="r")
    store.take_control(request.id)
    resumed = store.resume(request.id, human_actions_summary="clicked past the interstitial")
    assert resumed.controller == "agent"
    assert resumed.status == "resumed"
    assert resumed.human_actions_summary == "clicked past the interstitial"
    assert resumed.resumed_at is not None


def test_resumed_requests_drop_out_of_pending_list(store) -> None:
    request = store.raise_request(run_id="r1", capability="c", step_description="s", reason="r")
    store.resume(request.id)
    assert store.list_pending() == []


def test_wait_for_resume_times_out_if_never_resumed(store) -> None:
    request = store.raise_request(run_id="r1", capability="c", step_description="s", reason="r")
    with pytest.raises(TimeoutError):
        store.wait_for_resume(request.id, poll_interval=0.05, timeout=0.2)


def test_wait_for_resume_returns_once_a_second_process_resumes_it(tmp_path) -> None:
    """Simulates the real handoff: one process blocks waiting, a *separate*
    ControlPlaneStore instance (standing in for the operator's own terminal
    running `flux operator resume`) resumes it concurrently. Two independent
    store instances over the same directory, on purpose — proves this works
    through the filesystem, not shared in-memory state."""
    waiting_store = ControlPlaneStore(directory=tmp_path)
    operator_store = ControlPlaneStore(directory=tmp_path)
    request = waiting_store.raise_request(run_id="r1", capability="c", step_description="s", reason="r")

    def act_as_operator():
        time.sleep(0.15)
        operator_store.resume(request.id, human_actions_summary="dismissed the dialog manually")

    operator_thread = threading.Thread(target=act_as_operator)
    operator_thread.start()

    start = time.monotonic()
    resumed = waiting_store.wait_for_resume(request.id, poll_interval=0.05, timeout=5.0)
    elapsed = time.monotonic() - start

    operator_thread.join()
    assert resumed.status == "resumed"
    assert 0.1 < elapsed < 2.0  # actually waited for the resume, didn't return instantly or hang
