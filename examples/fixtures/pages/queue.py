"""A bounded work queue with retries.

Used as the code fixture: three functions, one class, and a module docstring.
"""

import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(order=True)
class Job:
    priority: int
    seq: int
    payload: Any = field(compare=False)
    attempts: int = field(default=0, compare=False)


class BoundedQueue:
    """A priority queue that refuses work when it is full."""

    def __init__(self, capacity: int = 128, max_attempts: int = 3):
        self.capacity = capacity
        self.max_attempts = max_attempts
        self._heap: List[Job] = []
        self._seq = 0

    def __len__(self) -> int:
        return len(self._heap)

    def put(self, payload: Any, priority: int = 5) -> bool:
        """Enqueue unless full. Returns False when the queue refused the job."""
        if len(self._heap) >= self.capacity:
            return False
        self._seq += 1
        heapq.heappush(self._heap, Job(priority, self._seq, payload))
        return True

    def get(self) -> Optional[Job]:
        """Pop the highest-priority job, or None when the queue is empty."""
        if not self._heap:
            return None
        return heapq.heappop(self._heap)

    def retry(self, job: Job, backoff: float = 0.5) -> bool:
        """Re-enqueue a failed job until its attempt budget runs out."""
        job.attempts += 1
        if job.attempts >= self.max_attempts:
            return False
        time.sleep(backoff * job.attempts)
        self._seq += 1
        job.seq = self._seq
        heapq.heappush(self._heap, job)
        return True


def drain(queue: BoundedQueue, handler) -> Dict[str, int]:
    """Run every job through handler. Returns counts of ok and failed jobs."""
    counts = {"ok": 0, "failed": 0}
    while len(queue):
        job = queue.get()
        if job is None:
            break
        try:
            handler(job.payload)
            counts["ok"] += 1
        except Exception:
            if queue.retry(job):
                continue
            counts["failed"] += 1
    return counts
