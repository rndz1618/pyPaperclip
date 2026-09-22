from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class WorkerController:
    """Owns the heartbeat lifecycle and drains the current task before shutdown."""

    def __init__(self, execute_one: Callable[[], dict[str, Any] | None], interval: float = 1.0) -> None:
        self.execute_one = execute_one
        self.interval = interval
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.to_thread(self.execute_one)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    async def drain(self, timeout: float = 60.0) -> None:
        self.stop_event.set()
        if self.task is None:
            return
        await asyncio.wait_for(self.task, timeout=timeout)
