"""Shared fixtures for browser-driven integration tests.

`page` / `browser` / `context` come from the pytest-playwright plugin
(headless Chromium by default; pass `--headed` to pytest to watch it).
This file only adds what's specific to flux: a live mock_bank server and a
fresh in-memory data store per test.
"""

from __future__ import annotations

import socket
import threading

import pytest
from werkzeug.serving import make_server

from mock_bank.app import app as flask_app
from mock_bank.data import store


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# The session's one pytest-playwright-managed browser exposes CDP too, so
# tests that need a real DevTools/CDP endpoint (flux.escalation's handoff
# mechanism) don't have to start an independent second Playwright instance —
# that conflicts with pytest-playwright's own sync-API event loop in the
# same thread ("using Playwright Sync API inside the asyncio loop").
TEST_CDP_PORT = 9333


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "args": [f"--remote-debugging-port={TEST_CDP_PORT}"]}


@pytest.fixture(scope="session")
def mock_bank_server() -> str:
    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()
