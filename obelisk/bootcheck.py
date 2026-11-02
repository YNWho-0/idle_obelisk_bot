"""Post-boot health check: run a few minutes after boot (obelisk-bootcheck.timer)
and send the result to the phone, so a reboot's outcome is visible without
logging in."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from . import ADB_SERIAL

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"


def uptime_s() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 1e9


def _run(*cmd, timeout=10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"error: {e}"


def check() -> tuple[bool, list[str]]:
    lines, ok = [], True
    boot = dt.datetime.now() - dt.timedelta(seconds=uptime_s())
    lines.append(f"booted {boot:%H:%M}, up {uptime_s() / 60:.0f} min")

    for unit in ("obelisk-bot", "obelisk-dashboard"):
        st = _run("systemctl", "--user", "is-active", unit)
        good = st == "active"
        ok &= good
        lines.append(f"{'OK ' if good else 'BAD'} {unit}: {st}")
    st = _run("systemctl", "--user", "is-active", "obelisk-summary.timer")
    ok &= st == "active"
    lines.append(f"{'OK ' if st == 'active' else 'BAD'} summary timer: {st}")

    wd = _run("waydroid", "status")
    session = "Session:\tRUNNING" in wd
    ok &= session
    lines.append(f"{'OK ' if session else 'BAD'} waydroid session: "
                 f"{'running' if session else 'NOT running (autostart missing?)'}")

    size = _run("adb", "-s", ADB_SERIAL, "shell", "wm", "size")
    good = "720x1280" in size
    ok &= good
    lines.append(f"{'OK ' if good else 'BAD'} adb + screen size: "
                 f"{'720x1280' if good else size.splitlines()[-1] if size else 'no answer'}")

    # did the bot complete a session since boot?
    ended = None
    for path in sorted(LOGS.glob("obelisk.log*")):
        try:
            for raw in path.read_text(errors="replace").splitlines():
                if "session end:" in raw:
                    t = dt.datetime.fromisoformat(raw[:19])
                    if t >= boot:
                        ended = (t, raw.split("session end:", 1)[1].strip())
        except (OSError, ValueError):
            continue
    ok &= ended is not None
    lines.append(f"{'OK ' if ended else 'BAD'} bot session since boot: "
                 f"{ended[0]:%H:%M} ({ended[1]})" if ended else
                 "BAD bot session since boot: none yet")
    return ok, lines


def run() -> None:
    from .notify import alert
    ok, lines = check()
    text = "\n".join(lines)
    print(text)
    latest = LOGS / "latest.jpg"
    fresh = latest.exists() and latest.stat().st_mtime > (
        dt.datetime.now() - dt.timedelta(seconds=uptime_s())).timestamp()
    alert(f"bootcheck_{int(uptime_s() // 1)}", f"Pi rebooted: {'all OK' if ok else 'PROBLEMS'}",
          text, priority="default" if ok else "high",
          tags="white_check_mark" if ok else "warning", force=True,
          image=latest if fresh else None)
