#!/usr/bin/env python3
"""Offline tests against saved screenshots (layout v2). No device needed.

  venv/bin/python -I tests/test_offline.py

The layout-v1 suite is kept in legacy/tests_v1/ for reference.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import obelisk.gems as _gems                       # noqa: E402
import obelisk.jobs as _jobs                       # noqa: E402
import obelisk.playtime as _pt                     # noqa: E402
from obelisk import screens                        # noqa: E402
from obelisk.jobs import (CloudSync, FounderJob, FreebieJob, JobAborted,  # noqa: E402
                          LootbugJob, LootbugPopupInterrupt, OfflineGainsInterrupt)
from obelisk.scheduler import Scheduler           # noqa: E402
from obelisk.session import SessionPlanner        # noqa: E402

# keep test artefacts out of the real logs/
_TMP = Path(tempfile.mkdtemp(prefix="obelisk-tests-"))
_jobs.FAIL_DIR = _TMP / "failures"
_gems.UNREAD_DIR = _TMP / "gem_unread"
_pt.UNREAD_DIR = _TMP / "playtime_unread"
_jobs.State.PATH = _TMP / "state.json"

S = ROOT / "screens"


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(S / name))
    assert img is not None, name
    return img


def inside(pt, box) -> bool:
    return box[0] <= pt[0] < box[2] and box[1] <= pt[1] < box[3]


F = {
    "mining_bugs": "n10_mining_bugs_crate_1.png",   # 3 bugs + founder crate, 632 gems
    "mining_bugs2": "n10_mining_bugs_crate_3.png",
    "crate_gone": "n32_founder_after.png",           # 1 bug, no crate, 658 gems
    "mining_1bug": "n60_resume_5.png",               # 1 bug, 768 gems
    "store_ready": "n42_store_freebie_ready.png",
    "store_toast": "n43_freebie_toast.png",
    "store_empty": "n44_freebie_claimed.png",
    "store_top": "n40_store.png",                    # opened at the top: no Freebie row
    "store_closed": "n45_store_closed.png",
    "bug_popup": "n21_lootbug_popup_settled.png",
    "grid": "n50_grid_menu.png",
    "cloud": "n51_cloud.png",                        # auto-save ON, upload enabled
    "cloud_autosave_off": "n52_autosave_off.png",
    "cloud_armed": "n53_upload_armed.png",
    "cloud_uploaded": "n54_uploaded.png",
    "off_opts": "n73_resume5min_2.png",
    "off_res": "n74_offline_results.png",
    "after_offline": "n75_after_offline_close.png",
    "options": "n70_options.png",
}
FRAMES = {k: load(v) for k, v in F.items()}
# every v2 screenshot shows one leftover red bug at ~(284,644); a copy with it
# painted over (snow colour) stands in for "no bugs left"
_nb = FRAMES["store_closed"].copy()
_nb[585:705, 235:335] = np.median(_nb[760:860, 600:700].reshape(-1, 3), axis=0).astype(np.uint8)
FRAMES["no_bugs"] = _nb

CLAIM = (550, 665, 644, 726)
GEM_150 = (550, 805, 644, 868)
STORE_X = (414, 1188, 466, 1234)
STORE_ICON = (573, 1184, 657, 1240)
GRID_BTN = (332, 1184, 392, 1240)
GRID_CLOUD = (194, 927, 260, 993)
BUG_SLOTS = (240, 590, 540, 720)
BUG_POPUP_X = (575, 148, 625, 197)
CRATE = (420, 560, 560, 660)
OFFLINE_BTN = (74, 1168, 646, 1218)
UPLOAD_BTN = (470, 655, 598, 708)
AUTOSAVE = (168, 815, 204, 851)


class FakeDevice:
    """Plays back screenshots. `script` maps the shown frame name to a list of
    (allowed press box, frames shown afterwards). Any other press fails."""

    def __init__(self, start: str, script: dict, in_front=True, after_launch=("mining_1bug",)):
        self.script = script
        self.queue = [start]
        self.presses: list[tuple[str, tuple[int, int]]] = []
        self.in_front, self.after_launch = in_front, after_launch
        self.launches = self.homes = 0

    def capture(self) -> np.ndarray:
        name = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        self._shown = name
        return FRAMES[name]

    def press(self, x, y):
        for box, after in self.script.get(self._shown, []):
            if inside((x, y), box):
                self.presses.append((self._shown, (x, y)))
                self.queue = list(after)
                return
        raise AssertionError(f"unexpected press at {(x, y)} on {self._shown}")

    def sleep(self, s):
        pass

    def swipe(self, x1, y1, x2, y2, ms=500):
        """script[(frame, "swipe")] = frames shown after a drag (else unchanged)."""
        assert x1 == x2 == 700, "store drags must stay on the right edge"
        after = self.script.get((self._shown, "swipe"))
        if after:
            self.queue = list(after)

    def game_in_front(self):
        return self.in_front

    def launch_game(self):
        self.launches += 1
        self.in_front = True
        self.queue = list(self.after_launch)

    def go_home(self):
        self.homes += 1
        self.in_front = False


# ---------------------------------------------------------------- detection

def test_identify():
    expect = {
        "mining_bugs": screens.MINING, "crate_gone": screens.MINING, "mining_1bug": screens.MINING,
        "store_closed": screens.MINING, "after_offline": screens.MINING,
        "store_ready": screens.STORE, "store_empty": screens.STORE, "store_top": screens.STORE,
        "bug_popup": screens.LOOTBUG_POPUP, "grid": screens.GRID_MENU, "cloud": screens.CLOUD,
        "off_opts": screens.OFFLINE_OPTIONS, "off_res": screens.OFFLINE_RESULTS,
        "options": screens.UNKNOWN,
    }
    for k, v in expect.items():
        assert screens.identify(FRAMES[k]) == v, (k, screens.identify(FRAMES[k]))
    assert screens.identify(load("v1layout/13_store_closed.png")) == screens.UNKNOWN   # old layout


def test_freebie_states():
    st, m = screens.freebie_state(FRAMES["store_ready"])
    assert st == "ready" and inside(m.center, CLAIM)
    assert screens.freebie_state(FRAMES["store_toast"])[0] == "unknown"
    assert screens.freebie_state(FRAMES["store_empty"])[0] == "empty"
    assert screens.freebie_state(FRAMES["store_top"])[0] == "unknown"    # row not visible


def test_gem_button_in_claim_slot_is_never_ready():
    img = FRAMES["store_ready"].copy()
    x1, y1, x2, y2 = GEM_150
    img[CLAIM[1]:CLAIM[1] + (y2 - y1), CLAIM[0]:CLAIM[0] + (x2 - x1)] = FRAMES["store_ready"][y1:y2, x1:x2]
    assert screens.freebie_state(img)[0] == "unknown"


def test_missing_header_is_never_ready():
    img = FRAMES["store_ready"].copy()
    img[618:656, 130:300] = FRAMES["store_ready"][758:796, 130:300]     # "Resource Pack" header
    assert screens.freebie_state(img)[0] == "unknown"


def test_scrolled_store_follows_header():
    img = FRAMES["store_ready"].copy()
    img[140:1090] = FRAMES["store_ready"][220:1170]                       # content up 80 px
    st, m = screens.freebie_state(img)
    assert st == "ready" and abs(m.center[1] - (695 - 80)) <= 2, (st, m)


def test_store_gauge():
    assert screens.store_fill(FRAMES["mining_bugs"]) > 0.2                # 7/19 banked
    assert screens.store_fill(FRAMES["store_closed"]) == 0.0              # just claimed


def test_founder_crate():
    for k in ("mining_bugs", "mining_bugs2"):
        m = screens.founder_crate(FRAMES[k])
        assert m is not None and inside(m.center, CRATE), (k, m)
    for k in ("crate_gone", "mining_1bug", "store_closed", "after_offline", "store_ready"):
        assert screens.founder_crate(FRAMES[k]) is None, k


def test_lootbug_sprites_and_wiki_skins():
    for k in ("mining_bugs", "mining_bugs2", "crate_gone", "mining_1bug"):
        m = screens.lootbug_sprite(FRAMES[k])
        assert m is not None and inside(m.center, BUG_SLOTS), (k, m)
        w = screens.wiki_lootbug(FRAMES[k])
        assert w is not None and inside(w.center, BUG_SLOTS), (k, w)
    assert screens.lootbug_sprite(FRAMES["store_ready"]) is None
    assert screens.lootbug_sprite(FRAMES["no_bugs"]) is None
    assert screens.lootbug_sprite(FRAMES["bug_popup"]) is None


def test_read_gems():
    from obelisk.gems import read_gems
    assert read_gems(FRAMES["mining_bugs"]) == 632
    assert read_gems(FRAMES["crate_gone"]) == 658
    assert read_gems(FRAMES["cloud"]) == 768
    assert read_gems(load("n53_upload_armed.png")) == 768          # not a sample


def test_playtime_and_cloud_buttons():
    from obelisk.playtime import CLOUD_ROW, LOCAL_ROW, read_text
    assert read_text(FRAMES["cloud"], CLOUD_ROW) == "05d16h20m45s"
    assert read_text(FRAMES["cloud_uploaded"], CLOUD_ROW) == "05d16h25m41s"
    assert read_text(FRAMES["cloud_armed"], CLOUD_ROW) == "05d16h20m45s"    # not a sample
    assert screens.cloud_button(FRAMES["cloud"], screens.CLOUD_DOWNLOAD) == "disabled"
    assert screens.cloud_button(FRAMES["cloud"], screens.CLOUD_UPLOAD) == "enabled"
    assert screens.cloud_button(FRAMES["cloud_armed"], screens.CLOUD_UPLOAD) == "armed"
    assert screens.cloud_autosave_on(FRAMES["cloud"])
    assert not screens.cloud_autosave_on(FRAMES["cloud_autosave_off"])


# ---------------------------------------------------------------- job flows

def test_freebie_job_claims_and_closes():
    dev = FakeDevice("mining_bugs", {
        "mining_bugs": [(STORE_ICON, ["store_ready"])],
        "store_ready": [(CLAIM, ["store_toast", "store_empty"])],
        "store_empty": [(STORE_X, ["store_closed"])],
    })
    assert FreebieJob().run(dev) == "claimed"
    assert [p[0] for p in dev.presses] == ["mining_bugs", "store_ready", "store_empty"]


def test_freebie_job_scrolls_to_the_row():
    dev = FakeDevice("mining_bugs", {
        "mining_bugs": [(STORE_ICON, ["store_top"])],
        ("store_top", "swipe"): ["store_ready"],
        "store_ready": [(CLAIM, ["store_toast", "store_empty"])],
        "store_empty": [(STORE_X, ["store_closed"])],
    })
    assert FreebieJob().run(dev) == "claimed"


def test_freebie_job_aborts_when_row_not_visible():
    dev = FakeDevice("mining_bugs", {
        "mining_bugs": [(STORE_ICON, ["store_top"])],
        "store_top": [(STORE_X, ["store_closed"])],
    })
    try:
        FreebieJob().run(dev)
    except JobAborted as e:
        assert "not recognised" in str(e) and dev.presses[-1][0] == "store_top"
    else:
        raise AssertionError("expected abort")


def test_lootbug_job_presses_bug_then_only_the_x():
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(BUG_SLOTS, ["bug_popup"])],
        "bug_popup": [(BUG_POPUP_X, ["store_closed"])],
    })
    assert LootbugJob().run(dev) == "looted"
    assert [p[0] for p in dev.presses] == ["crate_gone", "bug_popup"]


def test_founder_job():
    st = _jobs.State(_TMP / "f.json")
    dev = FakeDevice("mining_bugs", {"mining_bugs": [(CRATE, ["crate_gone"])]})
    assert FounderJob(state=st).run(dev) == "claimed" and st.get("founder_last_claim")
    dev = FakeDevice("mining_bugs", {"mining_bugs": [(CRATE, ["mining_bugs"])]})
    try:
        FounderJob(state=_jobs.State(_TMP / "f2.json")).run(dev)
    except JobAborted as e:
        assert "still there" in str(e)
    else:
        raise AssertionError("expected abort")


def test_offline_interrupt_confirm_then_close():
    dev = FakeDevice("off_opts", {"off_opts": [(OFFLINE_BTN, ["off_res"])],
                                  "off_res": [(OFFLINE_BTN, ["after_offline"])]})
    i = OfflineGainsInterrupt()
    assert i.handle(dev, dev.capture())
    assert i.handle(dev, dev.capture())
    assert [p[0] for p in dev.presses] == ["off_opts", "off_res"]


def test_popup_interrupt_only_presses_x():
    dev = FakeDevice("bug_popup", {"bug_popup": [(BUG_POPUP_X, ["store_closed"])]})
    assert LootbugPopupInterrupt().handle(dev, dev.capture()) and len(dev.presses) == 1


def test_cloud_turns_autosave_off_and_uploads():
    st = _jobs.State(_TMP / "c1.json")
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud"])],
        "cloud": [(AUTOSAVE, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(UPLOAD_BTN, ["cloud_armed"])],
        "cloud_armed": [(UPLOAD_BTN, ["cloud_uploaded"])],
        "cloud_uploaded": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "uploaded"
    assert st.get("cloud_last_upload") == (5 * 24 + 16) * 3600 + 25 * 60 + 41
    assert [p[0] for p in dev.presses] == ["crate_gone", "grid", "cloud", "cloud_autosave_off",
                                           "cloud_armed", "cloud_uploaded"]


def test_cloud_conflict_never_uploads():
    st = _jobs.State(_TMP / "c2.json")
    st.set("cloud_last_upload", 12345)                       # cloud shows something else
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "conflict" and st.get("cloud_conflict")
    assert all(not inside(p[1], UPLOAD_BTN) for p in dev.presses)


# ---------------------------------------------------------------- scheduler / sessions

def test_session_opens_handles_offline_popups_runs_jobs_and_goes_home():
    clock = [3_000_000.0]
    st = _jobs.State(_TMP / "s.json")
    st.set("founder_last_claim", clock[0] - 3600 - 400)      # next drop far off
    founder = FounderJob(state=st, clock=lambda: clock[0])
    dev = FakeDevice("off_opts", {
        "off_opts": [(OFFLINE_BTN, ["off_res"])],
        "off_res": [(OFFLINE_BTN, ["crate_gone"])],
        "crate_gone": [(BUG_SLOTS, ["bug_popup"])],
        "bug_popup": [(BUG_POPUP_X, ["no_bugs"])],
    }, in_front=False, after_launch=("off_opts",))
    sched = Scheduler(dev, [founder, LootbugJob()], interrupts=[OfflineGainsInterrupt(),
                      LootbugPopupInterrupt()], poll=0,
                      planner=SessionPlanner(founder, clock=lambda: clock[0]))
    for _ in range(8):
        sched.session_step()
        if dev.homes:
            break
    assert dev.launches == 1 and dev.homes == 1, (dev.launches, dev.homes, dev.presses)
    assert [p[0] for p in dev.presses] == ["off_opts", "off_res", "crate_gone", "bug_popup"]


def test_planner_every_50_minutes_and_never_waits():
    clock = [1_000_000.0]
    p = SessionPlanner(clock=lambda: clock[0])
    assert p.next_start == clock[0]                        # first session right away
    p.start(); assert p.founder_done(clock[0])             # nothing to wait for
    clock[0] += 30; assert p.finish() == 30
    assert p.next_start == 1_000_000.0 + 50 * 60


def test_scheduler_never_presses_off_mining_screen():
    dev = FakeDevice("options", {})
    sched = Scheduler(dev, [FounderJob(state=_jobs.State(_TMP / "n.json")), LootbugJob(),
                            FreebieJob()], poll=0)
    for _ in range(3):
        sched.tick()
    assert not dev.presses and sched.unknown == 3


def test_gem_guard_stops_on_loss():
    class SpendJob:
        name = "spend"
        def run(self, dev):
            dev.queue = ["mining_bugs"]                       # 632 gems
            return "done"
    dev = FakeDevice("crate_gone", {})                       # 658 gems
    try:
        Scheduler(dev, [], poll=0).run_job(SpendJob())
    except SystemExit as e:
        assert e.code == 3
    else:
        raise AssertionError("gem guard did not trip")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except BaseException:          # incl. SystemExit from the gem guard
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
