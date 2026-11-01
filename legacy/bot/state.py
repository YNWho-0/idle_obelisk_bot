"""
Thread-safe shared state used to bridge the automation loop (running in
its own thread/process context) and the web dashboard (Flask, running in
a background thread). Everything the dashboard needs to display or
control lives here.
"""

from __future__ import annotations

import threading
import time
from collections import deque, defaultdict
from typing import Optional, Deque, Dict


class StateStore:
    def __init__(self, max_log_lines: int = 300):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.status = "starting"          # starting | running | paused | error
        self.tasks_enabled = True         # pause/resume toggle for reactive tasks
        self.unknown_streak = 0
        self.task_counts: Dict[str, int] = defaultdict(int)
        self.last_screenshot_jpeg: Optional[bytes] = None
        self.last_screenshot_at: float = 0.0
        self.log_lines: Deque[str] = deque(maxlen=max_log_lines)
        self.workflow_last_run: Dict[str, float] = {}
        self.last_error: Optional[str] = None

    # -- writers (called from the bot thread) --------------------------------

    def set_status(self, status: str):
        with self._lock:
            self.status = status

    def set_tasks_enabled(self, enabled: bool):
        with self._lock:
            self.tasks_enabled = enabled

    def record_task_run(self, name: str):
        with self._lock:
            self.task_counts[name] += 1

    def set_unknown_streak(self, n: int):
        with self._lock:
            self.unknown_streak = n

    def set_screenshot(self, jpeg_bytes: bytes):
        with self._lock:
            self.last_screenshot_jpeg = jpeg_bytes
            self.last_screenshot_at = time.time()

    def record_workflow_run(self, name: str):
        with self._lock:
            self.workflow_last_run[name] = time.time()

    def set_error(self, message: Optional[str]):
        with self._lock:
            self.last_error = message

    def append_log(self, line: str):
        # deque.append is effectively atomic under the GIL; a lock here
        # would only add contention for no real safety benefit.
        self.log_lines.append(line)

    # -- readers (called from the Flask thread) -------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "tasks_enabled": self.tasks_enabled,
                "unknown_streak": self.unknown_streak,
                "task_counts": dict(self.task_counts),
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "last_screenshot_at": self.last_screenshot_at,
                "workflow_last_run": dict(self.workflow_last_run),
                "last_error": self.last_error,
            }

    def get_screenshot(self) -> Optional[bytes]:
        with self._lock:
            return self.last_screenshot_jpeg

    def get_logs(self, limit: int = 200):
        return list(self.log_lines)[-limit:]
