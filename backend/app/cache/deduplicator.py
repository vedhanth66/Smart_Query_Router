"""In-flight request deduplication (SingleFlight pattern).

Coalesces simultaneous identical backend optimization requests so that multiple
callers share a single in-flight execution rather than launching duplicate
model calls.

Guarantees:
- Strict tenant and user isolation (scoped deduplication keys).
- Time-bounded execution with per-call timeout and bounded in-flight lifetime.
- Non-blocking error propagation (if the leader fails, all waiters fail safely).
- Shielded execution so a client disconnect does not orphan or kill running flights
  that have other active waiters.
- Automatic cleanup of completed or expired flights.
"""

import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Coroutine, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DeduplicationTimeoutError(Exception):
    """Raised when awaiting an in-flight operation exceeds the configured timeout."""
    pass


@dataclass
class DeduplicatorStats:
    """Operational statistics for in-flight request deduplication."""
    leaders: int = 0
    waiters: int = 0
    timeouts: int = 0
    errors: int = 0
    evictions: int = 0

    def to_dict(self, active_count: int = 0) -> dict[str, Any]:
        """Serialize statistics to dictionary."""
        total_requests = self.leaders + self.waiters
        coalesce_ratio = round(self.waiters / total_requests, 4) if total_requests > 0 else 0.0
        return {
            "active_flights": active_count,
            "leaders": self.leaders,
            "waiters": self.waiters,
            "timeouts": self.timeouts,
            "errors": self.errors,
            "evictions": self.evictions,
            "total_requests": total_requests,
            "coalesce_ratio": coalesce_ratio,
        }


@dataclass
class CoalescedResult(Generic[T]):
    """Result container returning operation payload along with deduplication provenance."""
    data: T
    is_deduplicated: bool
    is_leader: bool
    waiters_count: int
    key: str


@dataclass
class InFlightOperation:
    """Represents an active, in-flight background task shared by one or more callers."""
    key: str
    created_at: float
    task: asyncio.Task
    waiters: int = 1

    def is_expired(self, max_seconds: float, now: float | None = None) -> bool:
        """Check whether this in-flight operation has exceeded max lifetime."""
        current = now if now is not None else time.time()
        return (current - self.created_at) >= max_seconds


class InFlightDeduplicator:
    """Thread-safe / asyncio-safe in-flight request deduplicator.
    
    Ensures simultaneous identical requests within the same tenant/user scope
    share a single execution task.
    """

    def __init__(self, max_in_flight_seconds: float = 15.0):
        self.max_in_flight_seconds = max_in_flight_seconds
        self._in_flight: dict[str, InFlightOperation] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._stats = DeduplicatorStats()

    def generate_dedup_key(
        self,
        tenant_id: str,
        user_id: str,
        cache_key: str,
    ) -> str:
        """Generates a composite, tenant- and user-isolated deduplication key.
        
        Format: sqr:flight:<tenant_id>:<user_id>:<cache_key>
        """
        clean_tenant = (tenant_id or "default_tenant").strip()
        clean_user = (user_id or "default_user").strip()
        return f"sqr:flight:{clean_tenant}:{clean_user}:{cache_key}"

    def _purge_expired_locked(self, now: float) -> int:
        """Evicts expired operations while caller holds self._lock. Returns count purged."""
        expired_keys = [
            k for k, op in self._in_flight.items()
            if op.is_expired(self.max_in_flight_seconds, now)
        ]
        for k in expired_keys:
            op = self._in_flight.pop(k, None)
            if op and not op.task.done():
                op.task.cancel()
            self._stats.evictions += 1
            logger.warning("Purged expired in-flight operation for key %s", k)
        return len(expired_keys)

    def _on_task_done(self, key: str, task: asyncio.Task) -> None:
        """Completion callback to clean up finished task from in-flight registry."""
        op = self._in_flight.get(key)
        if op is not None and op.task is task:
            self._in_flight.pop(key, None)

    async def execute_or_join(
        self,
        key: str,
        coro_fn: Callable[[], Coroutine[Any, Any, T]],
        timeout_seconds: float = 15.0,
    ) -> CoalescedResult[T]:
        """Executes the given coroutine or joins an existing in-flight task with matching key.
        
        Args:
            key: Tenant- and user-scoped unique deduplication key.
            coro_fn: Zero-arg callable returning coroutine to execute if leader.
            timeout_seconds: Maximum seconds to wait for result.
            
        Returns:
            CoalescedResult containing result data and deduplication metadata.
            
        Raises:
            DeduplicationTimeoutError: If execution exceeds timeout_seconds.
            Exception: Any exception raised by the executed coroutine.
        """
        now = time.time()
        async with self._lock:
            # 1. Clean up any expired flights
            self._purge_expired_locked(now)

            # 2. Check if active operation exists
            op = self._in_flight.get(key)
            if op is not None and not op.task.done() and not op.is_expired(self.max_in_flight_seconds, now):
                # Follower / Waiter joins existing flight
                op.waiters += 1
                self._stats.waiters += 1
                is_leader = False
                target_op = op
            else:
                # Leader: launch new background task
                task = asyncio.create_task(coro_fn())
                new_op = InFlightOperation(
                    key=key,
                    created_at=now,
                    task=task,
                    waiters=1,
                )
                self._in_flight[key] = new_op
                self._stats.leaders += 1
                is_leader = True
                target_op = new_op
                task.add_done_callback(lambda t, k=key: self._on_task_done(k, t))

        # 3. Await task completion with bounded timeout
        # Using asyncio.shield prevents client cancellation from terminating
        # the flight if other waiters depend on it.
        try:
            res = await asyncio.wait_for(
                asyncio.shield(target_op.task),
                timeout=timeout_seconds,
            )
            return CoalescedResult(
                data=res,
                is_deduplicated=(target_op.waiters > 1),
                is_leader=is_leader,
                waiters_count=target_op.waiters,
                key=key,
            )
        except asyncio.TimeoutError:
            self._stats.timeouts += 1
            raise DeduplicationTimeoutError(
                f"In-flight operation timed out after {timeout_seconds}s for key: {key}"
            )
        except Exception:
            self._stats.errors += 1
            raise

    async def purge_expired(self) -> int:
        """Explicitly purge expired in-flight entries. Returns count purged."""
        async with self._lock:
            return self._purge_expired_locked(time.time())

    async def clear(self) -> None:
        """Cancel all active tasks and reset in-flight registry."""
        async with self._lock:
            for op in self._in_flight.values():
                if not op.task.done():
                    op.task.cancel()
            self._in_flight.clear()

    async def get_stats(self) -> dict[str, Any]:
        """Return operational statistics."""
        async with self._lock:
            return self._stats.to_dict(len(self._in_flight))


# Singleton default in-flight deduplicator
default_deduplicator = InFlightDeduplicator(max_in_flight_seconds=15.0)
