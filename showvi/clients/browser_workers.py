from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")


@dataclass
class BrowserWorkerSnapshot:
    provider: str
    account_key: str
    busy: bool
    queued: int
    last_error: str
    last_success_at: Optional[float]
    average_task_seconds: float
    completed_tasks: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account_key": self.account_key,
            "busy": self.busy,
            "queued": self.queued,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
            "average_task_seconds": self.average_task_seconds,
            "completed_tasks": self.completed_tasks,
        }


class BrowserAccountWorker:
    def __init__(self, *, provider: str, account_key: str, factory: Callable[[], Any]) -> None:
        self.provider = provider
        self.account_key = account_key
        self._factory = factory
        self._service: Any = None
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._busy = False
        self._queued = 0
        self._last_error = ""
        self._last_success_at: Optional[float] = None
        self._completed_tasks = 0
        self._total_task_seconds = 0.0

    def _ensure_service(self) -> Any:
        with self._state_lock:
            if self._service is None:
                self._service = self._factory()
            return self._service

    def run(self, callback: Callable[[Any], T], *, task_name: str = "browser_task") -> T:
        del task_name
        with self._state_lock:
            self._queued += 1

        with self._run_lock:
            with self._state_lock:
                self._queued = max(0, self._queued - 1)
                self._busy = True

            started = time.perf_counter()
            try:
                result = callback(self._ensure_service())
            except Exception as exc:
                elapsed = time.perf_counter() - started
                with self._state_lock:
                    self._busy = False
                    self._completed_tasks += 1
                    self._total_task_seconds += elapsed
                    self._last_error = str(exc)
                raise

            elapsed = time.perf_counter() - started
            with self._state_lock:
                self._busy = False
                self._completed_tasks += 1
                self._total_task_seconds += elapsed
                self._last_error = ""
                self._last_success_at = time.time()
            return result

    def snapshot(self) -> BrowserWorkerSnapshot:
        with self._state_lock:
            average = self._total_task_seconds / self._completed_tasks if self._completed_tasks else 0.0
            return BrowserWorkerSnapshot(
                provider=self.provider,
                account_key=self.account_key,
                busy=self._busy,
                queued=self._queued,
                last_error=self._last_error,
                last_success_at=self._last_success_at,
                average_task_seconds=round(average, 3),
                completed_tasks=self._completed_tasks,
            )

    def close(self) -> None:
        with self._run_lock:
            service = None
            with self._state_lock:
                service = self._service
                self._service = None
            if service and hasattr(service, "close"):
                try:
                    service.close()
                except Exception:
                    pass


class BrowserWorkerRegistry:
    def __init__(self) -> None:
        self._workers: Dict[str, BrowserAccountWorker] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(provider: str, account_key: str) -> str:
        normalized_provider = (provider or "unknown").strip().lower() or "unknown"
        normalized_account = (account_key or "default").strip() or "default"
        return f"{normalized_provider}:{normalized_account}"

    def get_or_create(self, *, provider: str, account_key: str, factory: Callable[[], Any]) -> BrowserAccountWorker:
        key = self._key(provider, account_key)
        with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                worker = BrowserAccountWorker(provider=provider, account_key=account_key or "default", factory=factory)
                self._workers[key] = worker
            return worker

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [worker.snapshot().to_dict() for worker in self._workers.values()]


class SubmitThrottle:
    def __init__(self, *, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_submit_at = 0.0
        self._last_wait_seconds = 0.0

    def wait_turn(self) -> float:
        with self._lock:
            now = time.time()
            wait_seconds = self.min_interval_seconds - (now - self._last_submit_at)
            if wait_seconds <= 0:
                self._last_wait_seconds = 0.0
                self._last_submit_at = now
                return 0.0
            # Reserve the slot now; actual sleep happens outside the lock.
            self._last_wait_seconds = wait_seconds
            self._last_submit_at = now + wait_seconds

        # Sleep outside the lock so other threads aren't blocked.
        time.sleep(wait_seconds)
        return wait_seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "min_interval_seconds": self.min_interval_seconds,
                "last_submit_at": self._last_submit_at,
                "last_wait_seconds": self._last_wait_seconds,
            }


class SubmitThrottleRegistry:
    def __init__(self) -> None:
        self._throttles: Dict[str, SubmitThrottle] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(provider: str, account_key: str) -> str:
        normalized_provider = (provider or "unknown").strip().lower() or "unknown"
        normalized_account = (account_key or "default").strip() or "default"
        return f"{normalized_provider}:{normalized_account}"

    def get_or_create(self, *, provider: str, account_key: str, min_interval_seconds: float) -> SubmitThrottle:
        key = self._key(provider, account_key)
        with self._lock:
            throttle = self._throttles.get(key)
            if throttle is None:
                throttle = SubmitThrottle(min_interval_seconds=min_interval_seconds)
                self._throttles[key] = throttle
            return throttle


_BROWSER_WORKER_REGISTRY = BrowserWorkerRegistry()
_SUBMIT_THROTTLE_REGISTRY = SubmitThrottleRegistry()


def get_browser_worker_registry() -> BrowserWorkerRegistry:
    return _BROWSER_WORKER_REGISTRY


def get_submit_throttle_registry() -> SubmitThrottleRegistry:
    return _SUBMIT_THROTTLE_REGISTRY
