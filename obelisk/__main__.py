"""CLI.

  python -m obelisk identify screens/*.png    # offline: what does the bot see?
  python -m obelisk peek                      # one live raw capture, no input
  python -m obelisk freebie [--dry-run]       # run the Freebie job once, live
  python -m obelisk lootbug [--dry-run]       # run the Lootbug job once, live
  python -m obelisk founder [--dry-run]       # run the Founder-drop job once, live
  python -m obelisk run                       # scheduler loop
  python -m obelisk summary [--yesterday|--date D] [--write]   # daily report
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import logging.handlers
import sys
from pathlib import Path

import cv2

from . import screens
from .device import Device
from .jobs import (CloudSync, FounderJob, FreebieJob, JobAborted, LootbugJob, LootbugPopupInterrupt,
                   AnrInterrupt, MenuCloseInterrupt,
                   NotificationInterrupt, OfflineGainsInterrupt, save_failure)
from .session import SessionPlanner
from .scheduler import Scheduler

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "obelisk.log"


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    # rotate at midnight, keep 60 days (obelisk.log.YYYY-MM-DD); the daily
    # summary reads the current and rotated files
    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=60, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )


def cmd_identify(paths: list[str]) -> None:
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            print(f"{p}: unreadable")
            continue
        state, m = screens.freebie_state(img)
        where = f" @ {m.center} score {m.score:.3f}" if m else ""
        bug = screens.lootbug_sprite(img)
        print(f"{Path(p).name:28s} screen={screens.identify(img):13s} "
              f"fill={screens.store_fill(img):.2f} freebie={state}{where} "
              f"lootbugs={'@' + str(bug.center) if bug else '-'} "
              f"founder={'@' + str(fc.center) if (fc := screens.founder_crate(img)) else '-'}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="obelisk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identify").add_argument("paths", nargs="+")
    for name in ("freebie", "lootbug", "founder"):
        sub.add_parser(name).add_argument("--dry-run", action="store_true",
                                          help="log presses instead of sending them")
    sub.add_parser("peek", help="capture one live frame (no input), save and identify it")
    rp = sub.add_parser("run")
    rp.add_argument("--always-open", action="store_true",
                    help="keep the game in front all the time (burns buff timers)")
    cp = sub.add_parser("cloud", help="cloud save conflict status / resolution")
    cg = cp.add_mutually_exclusive_group()
    cg.add_argument("--status", action="store_true")
    cg.add_argument("--keep-pi", action="store_true",
                    help="resolve a conflict in favour of this device: next session uploads it")
    cg.add_argument("--conflict", metavar="REASON", help="mark a conflict by hand")
    nt = sub.add_parser("notify-test", help="send a test alert to the phone")
    sub.add_parser("bootcheck", help="post-boot health check, result sent to the phone")
    db = sub.add_parser("dashboard", help="read-only phone dashboard (Flask)")
    db.add_argument("--host", default="0.0.0.0")
    db.add_argument("--port", type=int)
    db.add_argument("--link", action="store_true",
                    help="print the phone links (with the secret token) and exit")
    sm = sub.add_parser("summary", help="daily summary from the logs")
    g = sm.add_mutually_exclusive_group()
    g.add_argument("--date", help="YYYY-MM-DD (default: today)")
    g.add_argument("--yesterday", action="store_true")
    sm.add_argument("--write", action="store_true", help="also save to logs/daily/<date>.txt")
    args = ap.parse_args()

    if args.cmd == "bootcheck":
        from .bootcheck import run
        run()
        return
    if args.cmd == "dashboard":
        from .dashboard import ensure_conf, serve
        if args.link:
            c = ensure_conf()
            port = c.get("port", "8765")
            print("Open once on your phone (a cookie remembers it):")
            print(f"  home Wi-Fi : http://192.168.86.149:{port}/?t={c['token']}")
            print(f"  Tailscale  : http://100.106.103.61:{port}/?t={c['token']}")
            return
        serve(args.host, args.port)
        return
    if args.cmd == "notify-test":
        from .notify import alert, topic
        latest = Path(__file__).resolve().parent.parent / "logs" / "latest.jpg"
        ok = alert("test", "Obelisk bot: test alert",
                   "If you see this (and a screenshot), alerts work. Tap to open the dashboard.",
                   tags="white_check_mark", force=True,
                   image=latest if latest.exists() else None)
        print("sent" if ok else "NOT sent", "to topic", topic())
        return
    if args.cmd == "cloud":
        from .jobs import State
        st = State()
        if args.keep_pi:
            st.set("cloud_conflict", None)
            st.set("cloud_last_upload", None)
            print("conflict cleared; the next session uploads this device's save "
                  "(overwriting the cloud save)")
        elif args.conflict:
            st.set("cloud_conflict", args.conflict)
            print("conflict set:", args.conflict)
        print("cloud_conflict:", st.get("cloud_conflict"), " cloud_last_upload:", st.get("cloud_last_upload"))
        return
    if args.cmd == "summary":
        from .summary import LOG_DIR, summarize
        day = (dt.date.today() - dt.timedelta(days=1)).isoformat() if args.yesterday \
            else (args.date or dt.date.today().isoformat())
        text = summarize(day)
        print(text, end="")
        if args.write:
            import time as _t
            cutoff = _t.time() - 30 * 86400          # keep failure screenshots 30 days
            for old in (LOG_DIR / "failures").glob("*.png"):
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            out = LOG_DIR / "daily" / f"{day}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            print(f"(saved {out})")
            from .notify import alert
            needs_look = "NEEDS A LOOK" in text
            alert(f"daily_{day}", f"Obelisk bot {day}: {'NEEDS A LOOK' if needs_look else 'OK'}",
                  text[:3500], priority="default" if needs_look else "low",
                  tags="warning" if needs_look else "bar_chart")
        return

    if args.cmd == "identify":
        cmd_identify(args.paths)
        return
    if args.cmd == "peek":
        out = Path(__file__).resolve().parent.parent / "screens" / "live_peek.png"
        cv2.imwrite(str(out), Device().capture())
        cmd_identify([str(out)])
        return

    setup_logging()
    dev = Device(dry_run=getattr(args, "dry_run", False))
    if args.cmd == "run":
        try:
            dev.ensure_resolution()  # re-applies wm size if Waydroid lost it
        except Exception as e:
            _report_down("Obelisk bot can't start", e)
            raise
    else:
        dev.check_resolution()
    if args.cmd in ("freebie", "lootbug", "founder"):
        job = {"freebie": FreebieJob, "lootbug": LootbugJob, "founder": FounderJob}[args.cmd]()
        try:
            print(job.run(dev))
        except JobAborted as e:
            save_failure(e.frame, f"{args.cmd}_{e}")
            sys.exit(f"aborted: {e}")
    else:
        logging.getLogger("obelisk").info("bot started")
        # priority order: the founder crate can vanish; lootbugs/freebies bank up
        founder = FounderJob()
        planner = None if args.always_open else SessionPlanner(founder)
        logging.getLogger("obelisk").info(
            "mode: %s", "always open" if planner is None else "a short session every 50 min, game in background between")
        try:
            Scheduler(dev, [founder, LootbugJob(), FreebieJob()],
                      interrupts=[AnrInterrupt(), OfflineGainsInterrupt(), LootbugPopupInterrupt(), MenuCloseInterrupt()],   # NotificationInterrupt: v1 only
                      poll=3.0, planner=planner,
                      cloud=None if planner is None else CloudSync()).run_forever()
        except Exception as e:
            _report_down("Obelisk bot crashed", e)
            raise


def _report_down(title: str, e: Exception) -> None:
    """Alert/track the outage; exits with code 3 (no more systemd restarts)
    once it has lasted too long. Silent while still booting (bootcheck reports)."""
    from .bootcheck import uptime_s
    from .outage import EXIT_GAVE_UP, report_down
    if uptime_s() < 600:
        return
    if report_down(title, e):
        logging.getLogger("obelisk").error("giving up: %s: %s", type(e).__name__, e)
        sys.exit(EXIT_GAVE_UP)


if __name__ == "__main__":
    main()
