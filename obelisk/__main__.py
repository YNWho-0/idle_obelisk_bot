"""CLI.

  python -m obelisk identify screens/*.png    # offline: what does the bot see?
  python -m obelisk peek                      # one live raw capture, no input
  python -m obelisk freebie [--dry-run]       # run the Freebie job once, live
  python -m obelisk lootbug [--dry-run]       # run the Lootbug job once, live
  python -m obelisk founder [--dry-run]       # run the Founder-drop job once, live
  python -m obelisk run                       # scheduler loop
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import cv2

from . import screens
from .device import Device
from .jobs import (FounderJob, FreebieJob, JobAborted, LootbugJob,
                   LootbugPopupInterrupt, save_failure)
from .scheduler import Scheduler

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "obelisk.log"


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
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
    sub.add_parser("run")
    args = ap.parse_args()

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
        dev.ensure_resolution()      # re-applies wm size if Waydroid lost it
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
        # priority order: the founder crate can vanish; lootbugs/freebies bank up
        Scheduler(dev, [FounderJob(), LootbugJob(), FreebieJob()],
                  interrupts=[LootbugPopupInterrupt()], poll=3.0).run_forever()


if __name__ == "__main__":
    main()
