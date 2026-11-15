"""Structured JSON logging for tool calls.

The assignment asks for logs that record tool name, status, duration and a request
id, and that never expose secrets. Every record is one JSON object per line, which
is what makes the log readable by both a person and a parser.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOGGER_NAME = "mlops_agent"

# Values of keys that look like credentials are replaced, never written out.
_SECRET_KEY_PATTERN = re.compile(r"(key|token|secret|password|passwd|credential|authorization)", re.IGNORECASE)
_REDACTED = "***redacted***"

# Long bearer tokens can also turn up inside free text such as an error message.
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{12,}", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Strip anything that looks like a credential out of a log payload."""
    if isinstance(value, dict):
        return {k: (_REDACTED if _SECRET_KEY_PATTERN.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _BEARER_PATTERN.sub(r"\1" + _REDACTED, value)
    return value


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON object."""

    _STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def new_request_id() -> str:
    """Short id tying one user question to every tool call it causes."""
    return uuid.uuid4().hex[:12]


def configure_logging(log_file: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Set up JSON logging to stderr and, when a path is given, to a file."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = JsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            # A read-only checkout should not stop the agent from running.
            logger.warning("log_file_unavailable", extra={"path": str(log_file)})
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


@contextmanager
def log_tool_call(tool: str, request_id: str, arguments: dict[str, Any] | None = None):
    """Time one tool call and emit a record whatever the outcome.

    The caller sets ``state["status"]`` so the log reflects the real result rather
    than just "no exception was raised".
    """
    logger = get_logger()
    state: dict[str, Any] = {"status": "ok"}
    started = time.perf_counter()
    logger.info(
        "tool_call_started",
        extra={"tool": tool, "request_id": request_id, "arguments": redact(arguments or {})},
    )
    try:
        yield state
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "tool_call_failed",
            extra={
                "tool": tool,
                "request_id": request_id,
                "status": "error",
                "duration_ms": round(duration_ms, 2),
                "error": str(exc),
            },
        )
        raise
    else:
        duration_ms = (time.perf_counter() - started) * 1000
        state["duration_ms"] = round(duration_ms, 2)
        logger.info(
            "tool_call_finished",
            extra={
                "tool": tool,
                "request_id": request_id,
                "status": state.get("status", "ok"),
                "duration_ms": round(duration_ms, 2),
            },
        )


def default_log_file() -> Path | None:
    """Log file path, unless logging to disk has been switched off."""
    if os.getenv("AGENT_DISABLE_FILE_LOG", "").lower() in {"1", "true", "yes"}:
        return None
    from agent.config import get_settings

    return get_settings().log_file
