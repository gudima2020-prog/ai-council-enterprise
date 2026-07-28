from __future__ import annotations

import asyncio


class TaskCancelledError(RuntimeError):
    pass


class TaskCancellationToken:
    """Cooperative cancellation token passed to task handlers."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str | None = None) -> None:
        self._reason = reason or "Task cancellation requested."
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelledError(
                self._reason or "Task cancellation requested."
            )
