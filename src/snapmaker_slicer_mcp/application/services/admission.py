"""One native execution gate shared by all transport and edit services."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

P = ParamSpec('P')
T = TypeVar('T')


class NativeAdmission:
    def __init__(self) -> None:
        self._native = threading.Lock()
        self._queue = asyncio.Lock()

    @contextmanager
    def session(self) -> Iterator[None]:
        with self._native:
            yield

    async def run(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        await self._queue.acquire()
        try:
            pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        except BaseException:
            self._queue.release()
            raise

        def finished(completed: asyncio.Task[T]) -> None:
            self._queue.release()
            if not completed.cancelled():
                completed.exception()

        # Caller cancellation never admits another native operation early.
        pending.add_done_callback(finished)
        return await asyncio.shield(pending)
