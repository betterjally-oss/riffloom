from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'riffloom-test.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        worker_step_delay=0.01,
        worker_threads=2,
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {
        "X-Riffloom-User": "user_demo",
        "X-Riffloom-Workspace": "ws_demo",
    }


def wait_for_terminal(
    client: TestClient,
    task_id: str,
    headers: dict[str, str],
    timeout: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"success", "partial_success", "failed", "cancelled"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach a terminal state")
