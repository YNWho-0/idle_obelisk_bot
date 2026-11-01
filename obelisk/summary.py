"""Daily summary built from logs/obelisk.log* (current + rotated files)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

RE_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+)\s+([\w.]+): (.*)$")
RE_JOB = re.compile(r"^job (\w+) -> (\w+)$")
RE_ABORT = re.compile(r"^job (\w+) aborted: (.*) \(\d+/\d+\)$")
RE_GEM_OK = re.compile(r"^gem guard: (\d+) -> (\d+) ok$")
RE_SESSION = re.compile(r"^session end: (\d+) s online")

LABELS = [
    ("founder", "claimed", "Founder drops claimed"),
    ("lootbug", "looted", "Lootbug batches looted"),
    ("freebie", "claimed", "Freebie claims"),
    ("freebie", "nothing", "Freebie checks with nothing banked"),
]


def lines_for(date: str, log_dir: Path = LOG_DIR):
    """Parsed (time, level, logger, message) for one YYYY-MM-DD, oldest first."""
    out = []
    for path in sorted(log_dir.glob("obelisk.log*")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            m = RE_LINE.match(raw)
            if m and m.group(1) == date:
                out.append((m.group(2), m.group(3), m.group(4), m.group(5)))
    out.sort(key=lambda t: t[0])
    return out


def summarize(date: str, log_dir: Path = LOG_DIR) -> str:
    rows = lines_for(date, log_dir)
    if not rows:
        return f"Idle Obelisk bot: {date}\n\nNo log lines for this day (bot not running?).\n"

    jobs, aborts, problems = Counter(), Counter(), []
    gem_ok = gem_skip = gem_trip = 0
    first_gems = last_gems = None
    founder_times, starts = [], 0
    sessions, online = 0, 0
    interrupts = Counter()
    for t, level, _, msg in rows:
        if m := RE_JOB.match(msg):
            jobs[(m.group(1), m.group(2))] += 1
            if m.group(1) == "founder" and m.group(2) == "claimed":
                founder_times.append(t[:5])
        elif m := RE_ABORT.match(msg):
            aborts[(m.group(1), m.group(2))] += 1
        elif m := RE_GEM_OK.match(msg):
            gem_ok += 1
            if first_gems is None:
                first_gems = int(m.group(1))
            last_gems = int(m.group(2))
        elif msg.startswith("gem guard: couldn't read"):
            gem_skip += 1
        if msg.startswith("GEM GUARD"):
            gem_trip += 1
        if m := RE_SESSION.match(msg):
            sessions += 1
            online += int(m.group(1))
        if msg.startswith("bot started"):
            starts += 1
        if msg.startswith("interrupt: dismissing notification"):
            interrupts["notifications dismissed"] += 1
        elif msg.startswith("interrupt: closing lootbug popup"):
            interrupts["stray Lootbug popups closed"] += 1
        elif msg.startswith("game is not in front"):
            interrupts["game relaunches"] += 1
        elif msg.startswith("screen is") and "re-applying wm size" in msg:
            interrupts["resolution re-applied"] += 1
        if level in ("WARNING", "ERROR", "CRITICAL"):
            problems.append(f"{t} {level}: {msg}")

    failures = sorted(p.name for p in (log_dir / "failures").glob(f"{date.replace('-', '')}-*.png"))

    out = [f"Idle Obelisk bot: {date}", "",
           f"Active: {rows[0][0]} - {rows[-1][0]}"
           + (f"   ({starts} start{'s' if starts != 1 else ''})" if starts else ""), ""]
    if sessions:
        out.append(f"Sessions: {sessions}, game in front {online // 60} min total "
                   f"(avg {online / sessions / 60:.1f} min); background (offline) the rest")
        out.append("")
    out.append("Jobs")
    for job, result, label in LABELS:
        out.append(f"  {label:38s} {jobs.get((job, result), 0):5d}")
    other = {k: v for k, v in jobs.items() if (k[0], k[1]) not in {(j, r) for j, r, _ in LABELS}}
    for (job, result), n in sorted(other.items()):
        out.append(f"  {job} -> {result:31s} {n:5d}")
    if founder_times:
        out.append(f"  Founder times: {' '.join(founder_times)}")
    out.append("")
    out.append("Gems")
    if first_gems is not None:
        out.append(f"  {first_gems} -> {last_gems}   ({last_gems - first_gems:+d} during checked jobs' span)")
    out.append(f"  Gem guard: {gem_ok} checked ok, {gem_skip} unreadable"
               + (f", {gem_trip} TRIPPED" if gem_trip else ""))
    out.append("")
    if interrupts:
        out.append("Recovery / interrupts")
        for k, v in sorted(interrupts.items()):
            out.append(f"  {k:38s} {v:5d}")
        out.append("")
    status = "OK" if not (aborts or problems or gem_trip or failures) else "NEEDS A LOOK"
    out.append(f"Status: {status}")
    for (job, reason), n in sorted(aborts.items()):
        out.append(f"  aborted {n}x  {job}: {reason}")
    for p in problems[:10]:
        out.append(f"  {p}")
    if len(problems) > 10:
        out.append(f"  ... {len(problems) - 10} more warnings/errors")
    for f in failures:
        out.append(f"  screenshot: logs/failures/{f}")
    return "\n".join(out) + "\n"
