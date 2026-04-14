"""Shared background task tracking for services.

Every service that fires off background coroutines should route them
through a ``BackgroundTaskSet``.  It guarantees:

    * completed tasks are auto-removed from the tracking set
    * all tracked tasks can be cancelled en masse during shutdown
    * unhandled exceptions are **logged with a full traceback** instead
      of vanishing silently — the #1 source of "why is this service
      mysteriously broken" bugs in async code
    * failures are also counted so ``/status``-style endpoints can
      report how many background tasks have died

Logging conventions
-------------------
Each ``spawn()`` emits a DEBUG-level "task started" line and each
completion emits a DEBUG "task finished" line, so you can follow task
lifecycles in journalctl with ``-p debug`` without grep gymnastics.
Failures are logged at WARNING with ``exc_info=True`` so the full
traceback lands in the journal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable


class BackgroundTaskSet:
    """Owns a set of fire-and-forget asyncio tasks with lifecycle tracking."""

    def __init__(self, logger: logging.Logger, *, label: str = "background") -> None:
        self._log = logger
        self._label = label
        self._tasks: set[asyncio.Task] = set()
        self._failure_count: int = 0
        self._last_failure: tuple[str, str] | None = None  # (task_name, exc_repr)

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self):
        return iter(self._tasks)

    def __contains__(self, task: object) -> bool:
        return task in self._tasks

    def __bool__(self) -> bool:
        return bool(self._tasks)

    @property
    def failure_count(self) -> int:
        """Number of spawned tasks that finished with an unhandled exception.

        Useful for health endpoints: a growing failure count is a
        strong signal that something is broken even if no user has
        reported it yet.
        """
        return self._failure_count

    @property
    def last_failure(self) -> tuple[str, str] | None:
        """(task_name, exception_repr) of the most recent failure, or None."""
        return self._last_failure

    def spawn(self, coro: Awaitable, *, name: str | None = None) -> asyncio.Task:
        """Launch a coroutine with automatic lifecycle tracking.

        The returned task is added to the tracking set and auto-removed
        on completion.  Unhandled exceptions are logged at WARNING with
        a full traceback, counted in ``failure_count``, and exposed via
        ``last_failure``.
        """
        task = asyncio.ensure_future(coro)
        if name:
            task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        self._log.debug("[%s] task started: %s", self._label, task.get_name())
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            self._log.debug("[%s] task cancelled: %s",
                            self._label, task.get_name())
            return
        exc = task.exception()
        if exc is None:
            self._log.debug("[%s] task finished: %s",
                            self._label, task.get_name())
            return
        self._failure_count += 1
        self._last_failure = (task.get_name(), repr(exc))
        # exc_info=(type, value, tb) prints the full traceback.  Passing
        # the exception directly (not True) works because the task is
        # already done and has its traceback attached.
        self._log.warning(
            "[%s] task %s failed: %s",
            self._label, task.get_name(), exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    async def cancel_all(self) -> None:
        """Cancel every tracked task and await completion."""
        if not self._tasks:
            return
        count = len(self._tasks)
        self._log.debug("[%s] cancelling %d background task(s)",
                        self._label, count)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
