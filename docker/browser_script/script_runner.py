from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any


SCRIPT_SCHEMA = "arch-browser-runtime-001.script-runner.v1"
BROWSER_SCHEMA = "arch-browser-runtime-001.browser-server.v1"
MAX_PROTOCOL_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024


class ScriptRunnerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BrowserOperationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Protocol:
    def __init__(self) -> None:
        self._input_fd = os.dup(0)
        self._output_fd = os.dup(1)
        os.set_inheritable(self._input_fd, False)
        os.set_inheritable(self._output_fd, False)
        self._reader = os.fdopen(
            self._input_fd,
            "r",
            encoding="utf-8",
            errors="strict",
            buffering=1,
        )
        self._writer = os.fdopen(
            self._output_fd,
            "w",
            encoding="utf-8",
            errors="strict",
            buffering=1,
        )
        self._next_id = 1

    def read_initial(self) -> dict[str, Any]:
        raw = self._reader.readline(MAX_INPUT_BYTES + 1)
        if raw == "":
            raise ScriptRunnerError("input_missing")
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES or not raw.endswith("\n"):
            raise ScriptRunnerError("input_too_large")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScriptRunnerError("input_invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema_version", "type", "input", "fixture_origin"}
            or value.get("schema_version") != SCRIPT_SCHEMA
            or value.get("type") != "init"
            or not isinstance(value.get("fixture_origin"), str)
        ):
            raise ScriptRunnerError("input_schema_mismatch")
        return value

    def emit(self, value: dict[str, Any]) -> None:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(raw.encode("utf-8")) > MAX_PROTOCOL_BYTES:
            raise ScriptRunnerError("protocol_output_too_large")
        self._writer.write(raw + "\n")
        self._writer.flush()

    def request(self, op: str, args: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self.emit(
            {
                "schema_version": SCRIPT_SCHEMA,
                "type": "request",
                "id": request_id,
                "op": op,
                "args": args,
            }
        )
        raw = self._reader.readline(MAX_PROTOCOL_BYTES + 1)
        if raw == "":
            raise ScriptRunnerError("browser_response_missing")
        if len(raw.encode("utf-8")) > MAX_PROTOCOL_BYTES or not raw.endswith("\n"):
            raise ScriptRunnerError("browser_response_too_large")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScriptRunnerError("browser_response_invalid") from exc
        if not isinstance(response, dict):
            raise ScriptRunnerError("browser_response_invalid")
        allowed_success = {
            "schema_version",
            "type",
            "id",
            "ok",
            "result",
        }
        allowed_error = {
            "schema_version",
            "type",
            "id",
            "ok",
            "error_code",
        }
        if set(response) not in (allowed_success, allowed_error):
            raise ScriptRunnerError("browser_response_shape_invalid")
        if (
            response.get("schema_version") != BROWSER_SCHEMA
            or response.get("type") != "response"
            or response.get("id") != request_id
            or not isinstance(response.get("ok"), bool)
        ):
            raise ScriptRunnerError("browser_response_invalid")
        if response["ok"] is not True:
            code = response.get("error_code")
            if not isinstance(code, str):
                code = "browser_operation_failed"
            raise BrowserOperationError(code)
        return response["result"]


class LocatorProxy:
    __slots__ = ("__protocol", "__selector")

    def __init__(self, protocol: _Protocol, selector: str) -> None:
        self.__protocol = protocol
        self.__selector = selector

    async def inner_text(self, *, timeout: int | None = None) -> str:
        args: dict[str, Any] = {"selector": self.__selector}
        if timeout is not None:
            args["timeout_ms"] = timeout
        result = self.__protocol.request("locator_inner_text", args)
        if not isinstance(result, str):
            raise ScriptRunnerError("browser_result_invalid")
        return result


class PageProxy:
    __slots__ = ("__protocol",)

    def __init__(self, protocol: _Protocol) -> None:
        self.__protocol = protocol

    async def goto(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        args: dict[str, Any] = {"url": url}
        if wait_until is not None:
            args["wait_until"] = wait_until
        if timeout is not None:
            args["timeout_ms"] = timeout
        return self.__protocol.request("goto", args)

    def locator(self, selector: str) -> LocatorProxy:
        if not isinstance(selector, str):
            raise TypeError("selector must be str")
        return LocatorProxy(self.__protocol, selector)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--max-result-bytes", type=int, required=True)
    return parser.parse_args()


def validate_script(path: str) -> Path:
    resolved = Path(path).resolve()
    root = Path("/input").resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ScriptRunnerError("script_path_invalid") from exc
    if not resolved.is_file():
        raise ScriptRunnerError("script_missing")
    return resolved


def load_run(script_path: Path):
    spec = importlib.util.spec_from_file_location(
        "browser_user_script",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise ScriptRunnerError("script_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if run is None or not asyncio.iscoroutinefunction(run):
        raise ScriptRunnerError("script_interface_invalid")
    return run


def suppress_untrusted_stdio() -> None:
    devnull_read = os.open(os.devnull, os.O_RDONLY)
    devnull_write = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_read, 0)
        os.dup2(devnull_write, 1)
        os.dup2(devnull_write, 2)
    finally:
        if devnull_read > 2:
            os.close(devnull_read)
        if devnull_write > 2:
            os.close(devnull_write)


def bounded_result(value: Any, max_bytes: int) -> Any:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ScriptRunnerError("result_not_json") from exc
    if len(raw.encode("utf-8")) > max_bytes:
        raise ScriptRunnerError("result_too_large")
    return json.loads(raw)


async def execute(
    *,
    protocol: _Protocol,
    script: Path,
    initial: dict[str, Any],
    max_result_bytes: int,
) -> Any:
    run = load_run(script)
    page = PageProxy(protocol)
    context = {
        "fixture_origin": initial["fixture_origin"],
        "input": initial["input"],
    }
    result = await run(page, context)
    return bounded_result(result, max_result_bytes)


def main() -> int:
    protocol: _Protocol | None = None
    try:
        args = parse_args()
        if not 1024 <= args.max_result_bytes <= 4 * 1024 * 1024:
            raise ScriptRunnerError("result_limit_invalid")
        script = validate_script(args.script)
        protocol = _Protocol()
        initial = protocol.read_initial()

        suppress_untrusted_stdio()

        result = asyncio.run(
            execute(
                protocol=protocol,
                script=script,
                initial=initial,
                max_result_bytes=args.max_result_bytes,
            )
        )
        protocol.emit(
            {
                "schema_version": SCRIPT_SCHEMA,
                "type": "result",
                "result": result,
            }
        )
        return 0
    except BaseException:
        if protocol is not None:
            try:
                protocol.emit(
                    {
                        "schema_version": SCRIPT_SCHEMA,
                        "type": "error",
                        "error_code": "script_failed",
                    }
                )
            except BaseException:
                pass
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
