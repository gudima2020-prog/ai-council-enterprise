from __future__ import annotations

import json
import logging

from backend.core.logging import (
    JsonFormatter,
    SecretMaskingFilter,
    SafeTextFormatter,
    mask_secrets,
    safe_log_message,
)


def test_mask_secrets() -> None:
    value = "api_key=super-secret-value"
    assert "super-secret-value" not in mask_secrets(value)


def test_secret_masking_filter_preserves_numeric_argument_type() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: %s %s "%s %d %s"',
        args=(
            "GET",
            "http://testserver/api/health",
            "HTTP/1.1",
            200,
            "OK",
        ),
        exc_info=None,
    )

    assert SecretMaskingFilter().filter(record) is True
    assert record.args[3] == 200
    assert isinstance(record.args[3], int)
    assert record.getMessage() == (
        'HTTP Request: GET http://testserver/api/health '
        '"HTTP/1.1 200 OK"'
    )


def test_safe_message_handles_malformed_third_party_record() -> None:
    record = logging.LogRecord(
        name="third_party",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Status: %d",
        args=("200",),
        exc_info=None,
    )

    message = safe_log_message(record)

    assert "Status: %d" in message
    assert "'200'" in message


def test_safe_text_formatter_handles_malformed_record() -> None:
    record = logging.LogRecord(
        name="third_party",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Status: %d",
        args=("200",),
        exc_info=None,
    )
    record.request_id = "-"
    record.correlation_id = "-"

    output = SafeTextFormatter("%(levelname)s | %(message)s").format(record)

    assert output.startswith("INFO | Status: %d")
    assert "'200'" in output


def test_json_formatter_handles_malformed_record() -> None:
    record = logging.LogRecord(
        name="third_party",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Status: %d",
        args=("200",),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["logger"] == "third_party"
    assert "Status: %d" in payload["message"]
