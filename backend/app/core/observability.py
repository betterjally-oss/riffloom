from __future__ import annotations

import json
import logging
import re
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4


_correlation_id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)
_workspace_id: ContextVar[str | None] = ContextVar("workspace_id", default=None)

_allowed_fields = (
    "request_id",
    "trace_id",
    "task_id",
    "workspace_id",
    "attempt_no",
    "method",
    "route",
    "status",
    "status_code",
    "duration_ms",
    "error_code",
    "error_type",
    "retryable",
    "provider",
    "operation",
    "slot",
    "byte_size",
    "reason",
    "restore_state",
    "field_count",
)


def new_request_id() -> str:
    return f"req_{uuid4().hex[:20]}"


def new_trace_id() -> str:
    return f"trc_{uuid4().hex[:24]}"


def safe_correlation_id(value: str | None, *, kind: str) -> str:
    if value and _correlation_id_pattern.fullmatch(value):
        return value
    return new_request_id() if kind == "request" else new_trace_id()


def current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def bind_log_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
) -> Iterator[None]:
    values = (
        (_request_id, request_id),
        (_trace_id, trace_id),
        (_task_id, task_id),
        (_workspace_id, workspace_id),
    )
    tokens = [(variable, variable.set(value)) for variable, value in values]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": str(record.msg),
        }
        contextual = {
            "request_id": _request_id.get(),
            "trace_id": _trace_id.get(),
            "task_id": _task_id.get(),
            "workspace_id": _workspace_id.get(),
        }
        for field in _allowed_fields:
            value = getattr(record, field, None)
            if value is None:
                value = contextual.get(field)
            if value is not None:
                payload[field] = value

        if record.exc_info and record.exc_info[0] is not None:
            frames = traceback.extract_tb(record.exc_info[2])[-12:]
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "frames": [
                    {
                        "file": Path(frame.filename).name,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in frames
                ],
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_application_logging(*, level: str, log_format: str) -> None:
    namespace = logging.getLogger("riffloom")
    namespace.setLevel(getattr(logging, level.upper(), logging.INFO))
    namespace.propagate = False
    for handler in namespace.handlers:
        handler.close()
    namespace.handlers.clear()

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    elif log_format == "text":
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    else:
        raise RuntimeError("RIFFLOOM_LOG_FORMAT 仅支持 json 或 text")
    namespace.addHandler(handler)
