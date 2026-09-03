from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.observability import JsonLogFormatter
from app.main import create_app
from conftest import wait_for_terminal


def test_json_formatter_allows_correlation_but_drops_secrets_and_exception_text():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("test.riffloom.safe-json")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    try:
        raise RuntimeError("secret-exception-value")
    except RuntimeError:
        logger.exception(
            "provider_call_failed",
            extra={
                "trace_id": "trc_safe_test",
                "provider": "sandbox-v1",
                "authorization": "Bearer secret-token-value",
                "prompt": "secret-prompt-value",
            },
        )

    raw = stream.getvalue()
    payload = json.loads(raw)
    assert payload["event"] == "provider_call_failed"
    assert payload["trace_id"] == "trc_safe_test"
    assert payload["provider"] == "sandbox-v1"
    assert payload["exception"]["type"] == "RuntimeError"
    assert payload["exception"]["frames"]
    assert "secret-token-value" not in raw
    assert "secret-prompt-value" not in raw
    assert "secret-exception-value" not in raw


def test_request_trace_is_validated_persisted_and_redacted_from_logs(tmp_path, capsys):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'observability.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        worker_step_delay=0.01,
        worker_threads=1,
        log_level="INFO",
        log_format="json",
    )
    identity = {
        "X-Riffloom-User": "user_demo",
        "X-Riffloom-Workspace": "ws_demo",
    }
    with TestClient(create_app(settings)) as client:
        invalid_id = client.get(
            "/api/v1/health",
            headers={"X-Request-ID": "invalid id with spaces"},
        )
        assert invalid_id.headers["X-Request-ID"].startswith("req_")
        assert invalid_id.headers["X-Request-ID"] != "invalid id with spaces"

        trace_id = "trc_pm_correlation_001"
        created = client.post(
            "/api/v1/collect-rewrite-tasks",
            headers={
                **identity,
                "Idempotency-Key": "trace-task-001",
                "X-Request-ID": "req_pm_request_001",
                "X-Trace-ID": trace_id,
            },
            json={
                "url": "https://sandbox.riffloom.local/notes/note-001?access=private",
                "prompt": "private-prompt-must-not-enter-logs",
                "usage_confirmed": True,
            },
        )
        assert created.status_code == 202
        assert created.headers["X-Request-ID"] == "req_pm_request_001"
        assert created.headers["X-Trace-ID"] == trace_id
        assert created.json()["trace_id"] == trace_id
        task = wait_for_terminal(client, created.json()["id"], identity)
        assert task["trace_id"] == trace_id

        validation = client.post(
            "/api/v1/tasks",
            headers={**identity, "Idempotency-Key": "trace-invalid-001"},
            json={
                "mode": "not-a-mode",
                "skill_id": "collect_breakdown_rewrite",
                "input": {"prompt": "private-validation-input"},
            },
        )
        assert validation.status_code == 422
        assert validation.json()["error"]["trace_id"].startswith("trc_")
        assert "private-validation-input" not in validation.text

        forbidden = client.get(
            "/api/v1/session",
            headers={
                "X-Riffloom-User": "user_demo",
                "X-Riffloom-Workspace": "ws_other",
                "X-Trace-ID": "trc_forbidden_001",
            },
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["trace_id"] == "trc_forbidden_001"

    raw_logs = capsys.readouterr().err
    entries = [json.loads(line) for line in raw_logs.splitlines() if line.startswith("{")]
    assert any(
        entry.get("event") == "http_request_completed"
            and entry.get("route") == "/api/v1/collect-rewrite-tasks"
        and entry.get("trace_id") == trace_id
        for entry in entries
    )
    assert any(
        entry.get("event") == "task_execution_finished"
        and entry.get("trace_id") == trace_id
        and entry.get("status") == "success"
        for entry in entries
    )
    assert any(
        entry.get("event") == "api_error"
        and entry.get("error_code") == "WORKSPACE_ACCESS_DENIED"
        for entry in entries
    )
    assert "private-prompt-must-not-enter-logs" not in raw_logs
    assert "private-validation-input" not in raw_logs
