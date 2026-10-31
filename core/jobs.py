"""jobs.py

The extraction job gate: at most `running` extraction jobs (Docling, the LLM
and the uploaded files in memory) run at once and `queued` more may wait.
Beyond that a job is refused at once with `Busy` instead of piling up.
In-process, like the rate limits. Used by the extraction and library upload
routes (`api.deps.job_slot`) and the assistant's `extract_document` tool.

*Usage:*
>>> gate = JobGate(running=2, queued=8)
>>> async with gate.slot():
...     await pipeline.extract_file(data, "stack.pdf")
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class Busy(RuntimeError):
    """Every extraction slot and the queue are taken."""


class JobGate:
    def __init__(self, running: int, queued: int):
        self.slots = asyncio.Semaphore(max(1, running))
        self.capacity = max(1, running) + max(0, queued)
        self.held = 0  # running + waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self.held >= self.capacity:
            raise Busy("The server is busy with other documents. Retry shortly.")
        self.held += 1
        try:
            async with self.slots:
                yield
        finally:
            self.held -= 1
