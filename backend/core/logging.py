from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import json
import logging
import re
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_PATTERNS = [
    re.compile(r"(sk-or-v1-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(secret\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)([^\s,;]+)"),
]


def mask_secrets(text: str) -> str:
    result = text

    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(
                lambda match: f"{match.group(1)}********",
                result,
            )
        else:
            result = pattern.sub(
                lambda match: f"{match.group(1)[:8]}********",
                result,
            )

    return result


def _mask_value_preserving_type(value: Any) -> Any:
    """
    Mask secrets without destroying values required by %-style formatting.

    The previous implementation converted every logging argument to str.
    That changed an integer status code 200 into "200", while httpx uses
    the %d placeholder. The formatter then raised TypeError.

    Strings are masked, containers are traversed, and numeric/boolean/None
    values keep their original types.
    """
    if isinstance(value, str):
        return mask_secrets(value)

    if isinstance(value, tuple):
        return tuple(_mask_value_preserving_type(item) for item in value)

    if isinstance(value, list):
        return [_mask_value_preserving_type(item) for item in value]

    if isinstance(value, dict):
        return {
            key: _mask_value_preserving_type(item)
            for key, item in value.items()
        }

    return value


def safe_log_message(record: logging.LogRecord) -> str:
    """
    Format a LogRecord without allowing malformed third-party records to
    crash application logging.

    Normal records use LogRecord.getMessage(). If a third-party formatter
    mismatch still occurs, the fallback preserves both the message template
    and arguments for diagnostics.
    """
    try:
        return mask_secrets(record.getMessage())
    except Exception:
        template = mask_secrets(str(record.msg))
        args = _mask_value_preserving_type(record.args)

        if not args:
            return template

        return f"{template} | args={args!r}"


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.correlation_id = correlation_id_var.get() or "-"
        return True


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(str(record.msg))

        if record.args:
            record.args = _mask_value_preserving_type(record.args)

        return True


class SafeTextFormatter(logging.Formatter):
    """
    Text formatter that cannot fail when record.getMessage() is malformed.
    """

    def format(self, record: logging.LogRecord) -> str:
        original_msg = record.msg
        original_args = record.args

        try:
            record.msg = safe_log_message(record)
            record.args = ()
            return super().format(record)
        finally:
            record.msg = original_msg
            record.args = original_args


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": safe_log_message(record),
            "request_id": getattr(record, "request_id", "-"),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            payload["exception"] = mask_secrets(
                self.formatException(record.exc_info)
            )

        return json.dumps(payload, ensure_ascii=False)


class LoggerManager:
    _configured = False

    @classmethod
    def configure(
        cls,
        *,
        debug: bool = False,
        log_dir: Path = LOG_DIR,
        max_bytes: int = 5_000_000,
        backup_count: int = 5,
    ) -> None:
        if cls._configured:
            return

        log_dir.mkdir(parents=True, exist_ok=True)
        level = logging.DEBUG if debug else logging.INFO

        root = logging.getLogger()
        root.setLevel(level)
        root.handlers.clear()

        context_filter = ContextFilter()
        masking_filter = SecretMaskingFilter()

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(
            SafeTextFormatter(
                "%(asctime)s | %(levelname)s | %(name)s | "
                "request=%(request_id)s | corr=%(correlation_id)s | %(message)s"
            )
        )
        console.addFilter(context_filter)
        console.addFilter(masking_filter)
        root.addHandler(console)

        system_handler = RotatingFileHandler(
            log_dir / "system.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        system_handler.setLevel(level)
        system_handler.setFormatter(JsonFormatter())
        system_handler.addFilter(context_filter)
        system_handler.addFilter(masking_filter)
        root.addHandler(system_handler)

        error_handler = RotatingFileHandler(
            log_dir / "errors.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter())
        error_handler.addFilter(context_filter)
        error_handler.addFilter(masking_filter)
        root.addHandler(error_handler)

        cls._configured = True

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return f"corr_{uuid.uuid4().hex}"


def set_log_context(*, request_id: str, correlation_id: str) -> None:
    request_id_var.set(request_id)
    correlation_id_var.set(correlation_id)


def clear_log_context() -> None:
    request_id_var.set(None)
    correlation_id_var.set(None)
