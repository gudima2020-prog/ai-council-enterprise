from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import mimetypes
from pathlib import Path, PurePosixPath
import sys
from threading import Thread
from typing import Any
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright


SERVER_SCHEMA = "arch-browser-runtime-001.browser-server.v1"
MAX_PROTOCOL_BYTES = 1024 * 1024
_ALLOWED_WAIT_UNTIL = {"commit", "domcontentloaded", "load", "networkidle"}


class ServerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FixtureHandler(BaseHTTPRequestHandler):
    fixture_root: Path

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_HEAD(self) -> None:
        self._serve(head_only=True)

    def do_GET(self) -> None:
        self._serve(head_only=False)

    def _serve(self, *, head_only: bool) -> None:
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise ServerError("fixture_request_invalid")
            decoded = unquote(parsed.path)
            if "\x00" in decoded or "\\" in decoded:
                raise ServerError("fixture_request_invalid")
            relative = PurePosixPath(decoded.lstrip("/"))
            if any(part in {"", ".", ".."} for part in relative.parts):
                if decoded not in {"", "/"}:
                    raise ServerError("fixture_request_invalid")
            target = self.fixture_root.joinpath(*relative.parts)
            if decoded in {"", "/"} or target.is_dir():
                target = target / "index.html"
            resolved = target.resolve()
            try:
                resolved.relative_to(self.fixture_root)
            except ValueError as exc:
                raise ServerError("fixture_request_escape") from exc
            if not resolved.is_file() or resolved.is_symlink():
                self.send_error(404)
                return
            data = resolved.read_bytes()
        except ServerError:
            self.send_error(400)
            return
        except OSError:
            self.send_error(404)
            return

        content_type = (
            mimetypes.guess_type(resolved.name)[0]
            or "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'",
        )
        if resolved.name == "download.txt":
            self.send_header(
                "Content-Disposition",
                'attachment; filename="download.txt"',
            )
        self.end_headers()
        if not head_only:
            self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", required=True)
    return parser.parse_args()


def validate_fixture(path: str) -> Path:
    resolved = Path(path).resolve()
    root = Path("/fixture").resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerError("runtime_path_invalid") from exc
    if not resolved.is_dir():
        raise ServerError("runtime_input_missing")
    return resolved


def emit(value: dict[str, Any]) -> None:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(raw.encode("utf-8")) > MAX_PROTOCOL_BYTES:
        raise ServerError("protocol_output_too_large")
    sys.stdout.write(raw + "\n")
    sys.stdout.flush()


def read_message() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.readline(MAX_PROTOCOL_BYTES + 1)
    if raw == b"":
        return None
    if len(raw) > MAX_PROTOCOL_BYTES or not raw.endswith(b"\n"):
        raise ServerError("protocol_input_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServerError("protocol_invalid") from exc
    if not isinstance(value, dict):
        raise ServerError("protocol_invalid")
    return value


def _request_id(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 1_000_000
    ):
        raise ServerError("request_id_invalid")
    return value


def _timeout_ms(value: Any, *, default: int = 10_000) -> int:
    if value is None:
        return default
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 30_000
    ):
        raise ServerError("request_timeout_invalid")
    return value


def _fixture_url(value: Any, fixture_origin: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ServerError("navigation_url_invalid")
    parsed = urlsplit(value)
    expected = urlsplit(fixture_origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != expected.port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ServerError("navigation_denied")
    return value


async def dispatch(page, request: dict[str, Any], fixture_origin: str) -> Any:
    if set(request) != {"schema_version", "type", "id", "op", "args"}:
        raise ServerError("request_shape_invalid")
    if (
        request["schema_version"] != SERVER_SCHEMA
        or request["type"] != "request"
    ):
        raise ServerError("request_schema_invalid")
    request_id = _request_id(request["id"])
    del request_id
    op = request["op"]
    args = request["args"]
    if not isinstance(args, dict):
        raise ServerError("request_args_invalid")

    if op == "goto":
        if not set(args).issubset({"url", "wait_until", "timeout_ms"}):
            raise ServerError("request_args_invalid")
        if "url" not in args:
            raise ServerError("request_args_invalid")
        url = _fixture_url(args["url"], fixture_origin)
        wait_until = args.get("wait_until", "load")
        if wait_until not in _ALLOWED_WAIT_UNTIL:
            raise ServerError("wait_until_invalid")
        timeout = _timeout_ms(args.get("timeout_ms"))
        await page.goto(url, wait_until=wait_until, timeout=timeout)
        return {"url": page.url}

    if op == "locator_inner_text":
        if not set(args).issubset({"selector", "timeout_ms"}):
            raise ServerError("request_args_invalid")
        selector = args.get("selector")
        if (
            not isinstance(selector, str)
            or not selector
            or len(selector) > 2048
            or "\x00" in selector
        ):
            raise ServerError("selector_invalid")
        timeout = _timeout_ms(args.get("timeout_ms"))
        return await page.locator(selector).inner_text(timeout=timeout)

    raise ServerError("operation_denied")


async def run_server(args: argparse.Namespace) -> int:
    fixture = validate_fixture(args.fixture_root)
    FixtureHandler.fixture_root = fixture
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(
        target=httpd.serve_forever,
        name="browser-fixture-server",
        daemon=True,
    )
    thread.start()
    fixture_origin = f"http://127.0.0.1:{httpd.server_address[1]}"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                chromium_sandbox=False,
                args=(
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                ),
            )
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
            )
            page = await context.new_page()
            try:
                emit(
                    {
                        "schema_version": SERVER_SCHEMA,
                        "type": "ready",
                        "browser_version": browser.version,
                        "automation_runtime_version": importlib.metadata.version(
                            "playwright"
                        ),
                        "fixture_origin": fixture_origin,
                    }
                )

                while True:
                    message = read_message()
                    if message is None:
                        return 70
                    if (
                        set(message) == {"schema_version", "type"}
                        and message.get("schema_version") == SERVER_SCHEMA
                        and message.get("type") == "shutdown"
                    ):
                        return 0

                    request_id = message.get("id")
                    try:
                        _request_id(request_id)
                        result = await dispatch(page, message, fixture_origin)
                        emit(
                            {
                                "schema_version": SERVER_SCHEMA,
                                "type": "response",
                                "id": request_id,
                                "ok": True,
                                "result": result,
                            }
                        )
                    except ServerError as exc:
                        emit(
                            {
                                "schema_version": SERVER_SCHEMA,
                                "type": "response",
                                "id": request_id,
                                "ok": False,
                                "error_code": exc.code,
                            }
                        )
                    except BaseException:
                        emit(
                            {
                                "schema_version": SERVER_SCHEMA,
                                "type": "response",
                                "id": request_id,
                                "ok": False,
                                "error_code": "browser_operation_failed",
                            }
                        )
            finally:
                await context.close()
                await browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def main() -> int:
    try:
        import asyncio

        return asyncio.run(run_server(parse_args()))
    except ServerError as exc:
        try:
            emit(
                {
                    "schema_version": SERVER_SCHEMA,
                    "type": "fatal",
                    "error_code": exc.code,
                }
            )
        except BaseException:
            pass
        return 70
    except BaseException:
        try:
            emit(
                {
                    "schema_version": SERVER_SCHEMA,
                    "type": "fatal",
                    "error_code": "browser_server_failed",
                }
            )
        except BaseException:
            pass
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
