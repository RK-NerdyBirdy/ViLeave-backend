"""
app/services/email_queue.py
───────────────────────────
In-memory email queue with a dedicated background worker.

The queue stores async callables plus keyword arguments. The worker retries
delivery with exponential backoff so transient SMTP failures do not drop mail.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EmailSender = Callable[..., Awaitable[None]]


@dataclass(slots=True)
class EmailQueueItem:
    sender: EmailSender
    kwargs: dict[str, Any] = field(default_factory=dict)
    description: str = "email task"


class EmailQueueManager:
    def __init__(self, max_retries: int = 3, base_delay_seconds: float = 1.0) -> None:
        self._queue: asyncio.Queue[EmailQueueItem | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._max_retries = max_retries
        self._base_delay_seconds = base_delay_seconds

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker(), name="email-queue-worker")

    async def stop(self) -> None:
        if not self._worker_task:
            return

        await self._queue.put(None)
        try:
            await self._worker_task
        finally:
            self._worker_task = None

    async def enqueue(self, sender: EmailSender, description: str | None = None, **kwargs: Any) -> None:
        await self._queue.put(
            EmailQueueItem(
                sender=sender,
                kwargs=kwargs,
                description=description or getattr(sender, "__name__", "email task"),
            )
        )

    async def _worker(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    self._queue.task_done()
                    break

                try:
                    await self._deliver(item)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("Email queue worker cancelled")
            raise

    async def _deliver(self, item: EmailQueueItem) -> None:
        for attempt in range(1, self._max_retries + 2):
            try:
                await item.sender(**item.kwargs)
                logger.info("Email task delivered: %s", item.description)
                return
            except Exception as exc:  # pragma: no cover - SMTP/network dependent
                if attempt > self._max_retries:
                    logger.exception("Email task failed permanently: %s", item.description)
                    return

                delay = self._base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Email task failed (%s) attempt %d/%d; retrying in %.1fs",
                    item.description,
                    attempt,
                    self._max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)