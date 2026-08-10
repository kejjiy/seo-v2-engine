"""Circuit Breaker pattern for LLM API resilience.

Implements a circuit breaker that:
- Opens after consecutive failures (configurable threshold)
- Stays open for a cooldown period
- Allows retries after cooldown expires
"""
import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass
class CircuitState:
    """Tracks the state of the circuit breaker."""
    failure_count: int = 0
    last_failure_time: Optional[float] = None
    is_open: bool = False
    opened_at: Optional[float] = None
    failure_times: list[float] = field(default_factory=list)


class CircuitBreaker:
    """Circuit breaker for protecting against cascading LLM API failures.

    Thread-safe implementation that tracks failures within a time window
    and opens the circuit when the threshold is exceeded.
    """

    def __init__(
        self,
        failure_threshold: Optional[int] = None,
        cooldown_seconds: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ):
        self.failure_threshold = failure_threshold or settings.LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD
        self.cooldown_seconds = cooldown_seconds or settings.LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS
        self.window_seconds = window_seconds or settings.LLM_CIRCUIT_BREAKER_WINDOW_SECONDS
        self._state = CircuitState()
        self._lock = Lock()

    def _prune_old_failures(self, current_time: float) -> None:
        """Remove failures outside the window."""
        cutoff = current_time - self.window_seconds
        self._state.failure_times = [
            t for t in self._state.failure_times if t >= cutoff
        ]

    def _should_close_circuit(self, current_time: float) -> bool:
        """Check if cooldown period has elapsed."""
        if self._state.opened_at is None:
            return False
        return (current_time - self._state.opened_at) >= self.cooldown_seconds

    def is_open(self) -> bool:
        """Check if the circuit is currently open (blocking requests).

        Returns:
            True if circuit is open and requests should be blocked.
        """
        with self._lock:
            current_time = time.time()

            if not self._state.is_open:
                return False

            if self._should_close_circuit(current_time):
                log.info(
                    "Circuit breaker cooldown elapsed, closing circuit "
                    "(had %d failures in window)",
                    len(self._state.failure_times)
                )
                self._state.is_open = False
                self._state.failure_count = 0
                self._state.failure_times = []
                return False

            return True

    def record_failure(self) -> None:
        """Record a failure and potentially open the circuit."""
        with self._lock:
            current_time = time.time()
            self._prune_old_failures(current_time)

            self._state.failure_times.append(current_time)
            self._state.failure_count = len(self._state.failure_times)
            self._state.last_failure_time = current_time

            if self._state.failure_count >= self.failure_threshold:
                if not self._state.is_open:
                    self._state.is_open = True
                    self._state.opened_at = current_time
                    log.warning(
                        "Circuit breaker OPENED after %d failures within %d seconds. "
                        "Blocking new requests for %d seconds.",
                        self._state.failure_count,
                        self.window_seconds,
                        self.cooldown_seconds
                    )

    def record_success(self) -> None:
        """Record a success (resets failure tracking when circuit is closed)."""
        with self._lock:
            if not self._state.is_open:
                self._state.failure_count = 0
                self._state.failure_times = []

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState()
            log.info("Circuit breaker manually reset")

    @property
    def failure_count(self) -> int:
        """Current number of failures in the window."""
        with self._lock:
            return len(self._state.failure_times)

    @property
    def state_summary(self) -> dict:
        """Get a summary of the current circuit state."""
        with self._lock:
            return {
                "is_open": self._state.is_open,
                "failure_count": len(self._state.failure_times),
                "last_failure_time": self._state.last_failure_time,
                "opened_at": self._state.opened_at,
                "threshold": self.failure_threshold,
                "cooldown_seconds": self.cooldown_seconds,
            }


llm_circuit_breaker = CircuitBreaker()
