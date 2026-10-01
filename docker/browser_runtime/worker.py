from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

from playwright.async_api import async_playwright


WORKER_META_SCHEMA = "arch-browser-runtime-001.worker-meta.v1"
MAX_STDIN_BYTES = 1024 * 1024


class WorkerError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--fixture-origin", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--max-result-bytes", type=int, required=True)
    return parser.parse_args()


def read_input() -> Any:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise WorkerError("input_too_large")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerError("input_invalid") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema_version")
        != "arch-browser-runtime-001.runner.v1"
        or "input" not in envelope
    ):
        raise WorkerError("input_schema_mismatch")
    return envelope["input"]


def load_run(script_path: Path):
    spec = importlib.util.spec_from_file_location(
        "browser_user_script",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise WorkerError("script_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if run is None or not asyncio.iscoroutinefunction(run):
        raise WorkerError("script_interface_invalid")
    return run


def suppress_process_output() -> None:
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    finally:
        if devnull > 2:
            os.close(devnull)


def write_result(
    path: Path,
    value: Any,
    *,
    max_bytes: int,
) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkerError("result_not_json") from exc
    if len(encoded) > max_bytes:
        raise WorkerError("result_too_large")
    path.write_bytes(encoded)


async def execute(args: argparse.Namespace, raw_input: Any) -> None:
    result_path = Path(args.result_path).resolve()
    try:
        result_path.relative_to(Path("/tmp").resolve())
    except ValueError as exc:
        raise WorkerError("result_path_invalid") from exc

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
        )
        page = await context.new_page()

        metadata = {
            "schema_version": WORKER_META_SCHEMA,
            "browser_version": browser.version,
            "automation_runtime_version": importlib.metadata.version(
                "playwright"
            ),
        }
        sys.stdout.write(
            json.dumps(
                metadata,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stdout.flush()

        suppress_process_output()

        try:
            run = load_run(Path(args.script))
            result = await run(
                page,
                {
                    "fixture_origin": args.fixture_origin,
                    "input": raw_input,
                },
            )
            write_result(
                result_path,
                result,
                max_bytes=args.max_result_bytes,
            )
        finally:
            await context.close()
            await browser.close()


def main() -> int:
    try:
        args = parse_args()
        if not 1024 <= args.max_result_bytes <= 4 * 1024 * 1024:
            raise WorkerError("result_limit_invalid")
        raw_input = read_input()
        asyncio.run(execute(args, raw_input))
        return 0
    except BaseException:
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
