from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any


def normalize_resource_name(value: str) -> str:
    return value.strip().lower()


class ResourceLockManager:
    """Named async locks. Unknown resources default to capacity one."""

    def __init__(self, capacities: dict[str, int] | None = None) -> None:
        self._capacities: dict[str, int] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active: Counter[str] = Counter()
        self._waiting: Counter[str] = Counter()

        for name, capacity in (capacities or {}).items():
            self.configure(name, capacity)

    def configure(self, name: str, capacity: int) -> None:
        normalized = normalize_resource_name(name)

        if not normalized:
            raise ValueError("Resource name cannot be empty.")
        if capacity < 1:
            raise ValueError("Resource capacity must be at least 1.")
        if normalized in self._semaphores:
            raise RuntimeError(
                "Resource capacity cannot be changed after first use: "
                f"{normalized}"
            )

        self._capacities[normalized] = capacity

    def capacity(self, name: str) -> int:
        return self._capacities.get(normalize_resource_name(name), 1)

    def _semaphore(self, name: str) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(name)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.capacity(name))
            self._semaphores[name] = semaphore
        return semaphore

    @asynccontextmanager
    async def acquire_many(
        self,
        resources: Iterable[str],
    ) -> AsyncIterator[list[str]]:
        names = sorted(
            {
                normalize_resource_name(item)
                for item in resources
                if normalize_resource_name(item)
            }
        )
        acquired: list[str] = []

        try:
            for name in names:
                semaphore = self._semaphore(name)
                self._waiting[name] += 1
                try:
                    await semaphore.acquire()
                finally:
                    self._waiting[name] -= 1
                    if self._waiting[name] <= 0:
                        self._waiting.pop(name, None)

                self._active[name] += 1
                acquired.append(name)

            yield acquired
        finally:
            for name in reversed(acquired):
                self._active[name] -= 1
                if self._active[name] <= 0:
                    self._active.pop(name, None)
                self._semaphore(name).release()

    def stats(self) -> dict[str, Any]:
        known = sorted(
            set(self._capacities)
            | set(self._semaphores)
            | set(self._active)
            | set(self._waiting)
        )

        return {
            "resources": {
                name: {
                    "capacity": self.capacity(name),
                    "active": self._active.get(name, 0),
                    "waiting": self._waiting.get(name, 0),
                    "available": max(
                        self.capacity(name) - self._active.get(name, 0),
                        0,
                    ),
                }
                for name in known
            }
        }
