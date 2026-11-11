"""Per-day numbers for the dashboard's Stats page, from logs/obelisk.log*.

Gems earned are attributed to the job that just ran: the scheduler logs
"job X -> Y" and then "gem guard: a -> b ok" around each job, so b - a is what
that job brought in. Spending and cloud downloads happen between jobs and so
never count as earnings; they only show in the balance line.

Problems are counted as episodes: the same warning/error again within
REPEAT_GAP of its last occurrence is the same problem still going on (on
2026-11-09 one frozen screen logged "screencap timed out" 47 times in an hour,
once per systemd restart).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .summary import RE_LINE

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
SOURCES = ("freebie", "founder", "lootbug")
REPEAT_GAP = dt.timedelta(hours=1)

RE_JOB = re.compile(r"^job (\w+) -> (\w+)$")
RE_GEM = re.compile(r"^gem guard: (\d+) -> (\d+) ok$")
RE_SESSION = re.compile(r"^session end: (\d+) s online")
RE_NUM = re.compile(r"\d+(\.\d+)?")
RE_PATH = re.compile(r"(?:/[\w.\-]+){2,}")     # file paths, not "(1/3)"


@dataclass
class Day:
    date: dt.date
    gems: Counter = field(default_factory=Counter)     # source -> gems earned
    jobs: Counter = field(default_factory=Counter)     # "founder claimed" -> n
    sessions: int = 0
    online: int = 0                                    # seconds with the game in front
    warnings: int = 0                                  # problem episodes started this day
    errors: int = 0
    notifications: int = 0
    lines: int = 0

    @property
    def earned(self) -> int:
        return sum(self.gems.values())

    @property
    def problems(self) -> int:
        return self.warnings + self.errors


@dataclass
class Stats:
    days: list[Day]                                    # oldest first, one per calendar day
    balance: list[tuple[dt.datetime, int]]             # every gem reading
    issues: list[tuple[str, int, int]]                 # (message, episodes, log lines), most first

    def total(self, attr: str) -> int:
        return sum(getattr(d, attr) for d in self.days)

    def job(self, key: str) -> int:
        return sum(d.jobs[key] for d in self.days)

    @property
    def active_days(self) -> int:
        return sum(1 for d in self.days if d.lines)


_cache: dict[Path, tuple[tuple[float, int], list]] = {}


def _parse(path: Path) -> list:
    """[(datetime, level, logger, msg)] of one log file; rotated files never
    change, so they are parsed once per dashboard process."""
    try:
        st = path.stat()
        key = (st.st_mtime, st.st_size)
        hit = _cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        text = path.read_text(errors="replace")
    except OSError:
        return []
    rows = []
    for raw in text.splitlines():
        m = RE_LINE.match(raw)
        if m:
            rows.append((dt.datetime.fromisoformat(f"{m.group(1)} {m.group(2)}"),
                         m.group(3), m.group(4), m.group(5)))
    _cache[path] = (key, rows)
    return rows


def issue_key(msg: str) -> str | None:
    """A warning/error with its numbers and paths blanked, so repeats group."""
    if msg.startswith("saved failure screenshot"):       # always follows another problem
        return None
    return RE_NUM.sub("#", RE_PATH.sub("…", msg))[:110]


def collect(days: int, today: dt.date | None = None, log_dir: Path = LOG_DIR) -> Stats:
    today = today or dt.date.today()
    first = today - dt.timedelta(days=days - 1)
    by_day = {first + dt.timedelta(n): Day(first + dt.timedelta(n)) for n in range(days)}
    since = dt.datetime.combine(first, dt.time())
    rows = []
    for path in sorted(log_dir.glob("obelisk.log*")):
        m = re.search(r"(\d{4}-\d\d-\d\d)$", path.name)
        if m and dt.date.fromisoformat(m.group(1)) < first:
            continue
        rows.extend(r for r in _parse(path) if r[0] >= since)
    rows.sort(key=lambda r: r[0])

    balance, last_job = [], None
    issues: dict[str, list] = {}                       # key -> [episodes, lines, last seen]
    for t, level, logger, msg in rows:
        d = by_day.get(t.date())
        if d is None:
            continue
        d.lines += 1
        if level in ("WARNING", "ERROR", "CRITICAL") and (k := issue_key(msg)):
            it = issues.setdefault(k, [0, 0, None])
            if it[2] is None or t - it[2] > REPEAT_GAP:  # a new episode
                it[0] += 1
                if level == "WARNING":
                    d.warnings += 1
                else:
                    d.errors += 1
            it[1] += 1
            it[2] = t
        if logger != "obelisk.scheduler" and logger != "obelisk.jobs":
            continue
        if m := RE_JOB.match(msg):
            d.jobs[f"{m.group(1)} {m.group(2)}"] += 1
            last_job = m.group(1)
        elif m := RE_GEM.match(msg):
            before, after = int(m.group(1)), int(m.group(2))
            balance.append((t, after))
            if last_job in SOURCES and after > before:
                d.gems[last_job] += after - before
            last_job = None
        elif msg.startswith("gem guard"):
            last_job = None
        elif m := RE_SESSION.match(msg):
            d.sessions += 1
            d.online += int(m.group(1))
        elif msg.startswith("interrupt: dismissing a notification"):
            d.notifications += 1
    top = sorted(issues.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:6]
    return Stats(days=list(by_day.values()), balance=balance,
                 issues=[(k, n, lines) for k, (n, lines, _) in top])
