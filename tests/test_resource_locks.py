from __future__ import annotations

import asyncio

import pytest

from backend.task_engine.resource_locks import ResourceLockManager


@pytest.mark.asyncio
async def test_same_resource_is_serialized() -> None:
    manager = ResourceLockManager()
    active = 0
    maximum = 0

    async def job() -> None:
        nonlocal active, maximum

        async with manager.acquire_many(["account:main"]):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1

    await asyncio.gather(job(), job(), job())

    assert maximum == 1


@pytest.mark.asyncio
async def test_resource_capacity_allows_parallel_access() -> None:
    manager = ResourceLockManager({"api:openrouter": 2})
    active = 0
    maximum = 0

    async def job() -> None:
        nonlocal active, maximum

        async with manager.acquire_many(["api:openrouter"]):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1

    await asyncio.gather(job(), job(), job())

    assert maximum == 2


@pytest.mark.asyncio
async def test_multiple_resources_do_not_deadlock() -> None:
    manager = ResourceLockManager()

    async def first() -> None:
        async with manager.acquire_many(["a", "b"]):
            await asyncio.sleep(0.01)

    async def second() -> None:
        async with manager.acquire_many(["b", "a"]):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(
        asyncio.gather(first(), second()),
        timeout=1,
    )
