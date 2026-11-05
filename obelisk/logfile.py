"""The shared log file, safe for several processes writing at once.

The bot, the dashboard and the timer commands (health, summary) all write to
logs/obelisk.log. Python's TimedRotatingFileHandler assumes a single writer:
when a short-lived command rotated the file, the bot kept writing to the renamed
file until it restarted. Here each write takes a lock, rotates if the file was
last written on an earlier day, and reopens the file if another process rotated it.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import logging
import logging.handlers
import os
import re
import shutil
from pathlib import Path

KEEP_DAYS = 60
RE_ROTATED = re.compile(r"\.(\d{4}-\d{2}-\d{2})$")


class SharedDailyLog(logging.handlers.WatchedFileHandler):
    """Rotates to <file>.YYYY-MM-DD, named for the day of the file's last write."""

    def __init__(self, path: Path, keep_days: int = KEEP_DAYS):
        self.path = Path(path)
        self.keep_days = keep_days
        super().__init__(self.path, encoding="utf-8", delay=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # .lock, not .log.lock: readers glob obelisk.log*
            with open(self.path.with_suffix(".lock"), "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._rotate_if_old()
                super().emit(record)        # reopens the file if it was rotated
        except Exception:
            self.handleError(record)

    def _rotate_if_old(self) -> None:
        try:
            last_write = dt.date.fromtimestamp(self.path.stat().st_mtime)
        except FileNotFoundError:
            return
        today = dt.date.today()
        if last_write >= today:
            return
        target = Path(f"{self.path}.{last_write.isoformat()}")
        if target.exists():                 # never overwrite an earlier rotation
            with open(self.path, "rb") as src, open(target, "ab") as dst:
                shutil.copyfileobj(src, dst)
            self.path.unlink()
        else:
            self.path.rename(target)
        self._prune(today)

    def _prune(self, today: dt.date) -> None:
        for old in self.path.parent.glob(self.path.name + ".*"):
            m = RE_ROTATED.search(old.name)
            if m and (today - dt.date.fromisoformat(m.group(1))).days > self.keep_days:
                old.unlink()
