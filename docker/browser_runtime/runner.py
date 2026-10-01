from __future__ import annotations

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import subprocess
import sys
from threading import Thread
from typing import Any
from urllib.parse import unquote, urlsplit


RUNNER_SCHEMA = "arch-browser-runtime-001.runner.v1"
WORKER_META_SCHEMA = "arch-browser-runtime-001.worker-meta.v1"
MAX_STDIN_BYTES = 1024 * 1024


class RunnerError(RuntimeError):
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
                raise RunnerError("fixture_request_invalid")
            decoded = unquote(parsed.path)
            if "\x00" in decoded or "\\" in decoded:
                raise RunnerError("fixture_request_invalid")
            relative = PurePosixPath(decoded.lstrip("/"))
            if any(part in {"", ".", ".."} for part in relative.parts):
                if decoded not in {"", "/"}:
                    raise RunnerError("fixture_request_invalid")
            target = self.fixture_root.joinpath(*relative.parts)
            if decoded in {"", "/"} or target.is_dir():
                target = target / "index.html"
            resolved = target.resolve()
            try:
                resolved.relative_to(self.fixture_root)
            except ValueError as exc:
                raise RunnerError("fixture_request_escape") from exc
            if not resolved.is_file() or resolved.is_symlink():
                self.send_error(404)
                return
            data = resolved.read_bytes()
        except RunnerError:
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
        self.end_headers()
        if not head_only:
            self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--max-result-bytes", type=int, required=True)
    return parser.parse_args()


def read_stdin() -> bytes:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise RunnerError("input_too_large")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("input_invalid") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema_version") != RUNNER_SCHEMA
        or "input" not in envelope
    ):
        raise RunnerError("input_schema_mismatch")
    return raw


def validate_path(path: str, expected_prefix: str) -> Path:
    resolved = Path(path).resolve()
    if not str(resolved).startswith(expected_prefix):
        raise RunnerError("runtime_path_invalid")
    return resolved


def parse_worker_meta(raw: str) -> dict[str, str]:
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RunnerError("worker_protocol_invalid")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RunnerError("worker_protocol_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != WORKER_META_SCHEMA
        or not isinstance(value.get("browser_version"), str)
        or not value["browser_version"].strip()
        or not isinstance(value.get("automation_runtime_version"), str)
        or not value["automation_runtime_version"].strip()
    ):
        raise RunnerError("worker_protocol_invalid")
    return {
        "browser_version": value["browser_version"].strip(),
        "automation_runtime_version": value[
            "automation_runtime_version"
        ].strip(),
    }


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    sys.stdout.flush()


def main() -> int:
    try:
        args = parse_args()
        if not 1024 <= args.max_result_bytes <= 4 * 1024 * 1024:
            raise RunnerError("result_limit_invalid")

        script = validate_path(args.script, "/input/")
        fixture = validate_path(args.fixture_root, "/fixture")
        if not script.is_file() or not fixture.is_dir():
            raise RunnerError("runtime_input_missing")

        stdin_bytes = read_stdin()
        FixtureHandler.fixture_root = fixture
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            FixtureHandler,
        )
        thread = Thread(
            target=server.serve_forever,
            name="browser-fixture-server",
            daemon=True,
        )
        thread.start()
        fixture_origin = (
            f"http://127.0.0.1:{server.server_address[1]}"
        )
        result_path = Path("/tmp/browser-result.json")
        if result_path.exists():
            result_path.unlink()

        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "/opt/browser-runtime/worker.py",
                    "--script",
                    str(script),
                    "--fixture-origin",
                    fixture_origin,
                    "--result-path",
                    str(result_path),
                    "--max-result-bytes",
                    str(args.max_result_bytes),
                ],
                input=stdin_bytes,
                capture_output=True,
                timeout=None,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        if completed.returncode != 0:
            raise RunnerError("script_failed")

        metadata = parse_worker_meta(
            completed.stdout.decode("utf-8", errors="replace")
        )
        if not result_path.is_file():
            raise RunnerError("result_missing")
        raw_result = result_path.read_bytes()
        if len(raw_result) > args.max_result_bytes:
            raise RunnerError("result_too_large")
        try:
            result = json.loads(raw_result.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("result_invalid") from exc

        emit(
            {
                "schema_version": RUNNER_SCHEMA,
                "ok": True,
                "result": result,
                **metadata,
            }
        )
        return 0
    except RunnerError as exc:
        emit(
            {
                "schema_version": RUNNER_SCHEMA,
                "ok": False,
                "error_code": exc.code,
            }
        )
        return 70
    except BaseException:
        emit(
            {
                "schema_version": RUNNER_SCHEMA,
                "ok": False,
                "error_code": "runner_failed",
            }
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
