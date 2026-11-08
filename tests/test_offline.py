#!/usr/bin/env python3
"""Offline tests against saved screenshots (layout v2). No device needed.

  venv/bin/python -I tests/test_offline.py

The layout-v1 suite is kept in legacy/tests_v1/ for reference.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
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
                          AnrInterrupt, LootbugJob, LootbugPopupInterrupt, MenuCloseInterrupt,
                          OfflineGainsInterrupt)
from obelisk.logfile import SharedDailyLog         # noqa: E402
from obelisk.scheduler import Scheduler           # noqa: E402
from obelisk.session import SessionPlanner        # noqa: E402

# keep test artefacts out of the real logs/
_TMP = Path(tempfile.mkdtemp(prefix="obelisk-tests-"))
import obelisk.scheduler as _sched                 # noqa: E402
_sched.LATEST = _TMP / "latest.jpg"
_sched.FOUNDER_EMPTY_DIR = _TMP / "founder_empty"
_jobs.FAIL_DIR = _TMP / "failures"
_gems.UNREAD_DIR = _TMP / "gem_unread"
_gems.STABLE_WAIT = 0.05                           # unreadable frames don't wait the real 6 s
_pt.UNREAD_DIR = _TMP / "playtime_unread"
_jobs.State.PATH = _TMP / "state.json"
import obelisk.notify as _notify                  # noqa: E402
_notify.CONF = _TMP / "no-notify.conf"            # tests must never send real alerts
import obelisk.health as _health                  # noqa: E402
_health.LOGS, _health.LATEST = _TMP, _TMP / "health.png"
# Scheduler.stop() attaches a health picture; never run the real (adb/grim) checks
# nor look at the real bot service
_real_bot_status, _health.bot_status = _health.bot_status, lambda: (True, "running", False)
_health.run = lambda dev=None, copy_to_failures=False, stopped=False: (None, _health.Health(stopped=stopped))

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
    "store_full": "n90_mining_store_full.png",      # Store button in its 'bank full' form
    "bug_popup_15": "n91_lootbug_popup_15.png",     # 2-digit count shifts the centred header
    "anr": "n93_anr_dialog.png",                    # Android "isn't responding" over the game
    "cloud_phone_newer": "n99_cloud_after_restart.png",   # Download enabled, cloud 05d20h27m13s
    "cloud_dl_armed": "n100_download_press1_1.png",
    "after_download": "n101_download_1.png",              # reloaded: Offline Gains
    "cloud_version_block": "n104_cloud_version_block.png",  # cloud v2.2.52, Pi v2.2.51: both greyed
    "cloud_same_version": "n105_cloud_same_version.png",    # both v2.2.52: Download enabled
    "bug_popup_v2252": "n106_lootbug_popup_v2252.png",      # X icon about 1 px off the older one
    "store_odd_y": "n107_store_row_odd_y.png",              # Freebie row on an odd y: header 0.89
    "store_false_header": "n108_store_top_false_header.png",  # "+1% Freebie Pack ..." text, no row
}
FRAMES = {k: load(v) for k, v in F.items()}
# every v2 screenshot shows one leftover red bug at ~(284,644); a copy with it
# painted over (snow colour) stands in for "no bugs left"
_nb = FRAMES["store_closed"].copy()
_nb[585:705, 235:335] = np.median(_nb[760:860, 600:700].reshape(-1, 3), axis=0).astype(np.uint8)
FRAMES["no_bugs"] = _nb
FRAMES["blank"] = np.zeros_like(_nb)                # gem counter unreadable

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
        self.version = "2.2.52"
        self.audio = False

    def audio_broken(self, since):
        return self.audio

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

    def restart_game(self):
        self.launches += 1
        self.in_front = True
        self.queue = list(self.after_launch)

    def go_home(self):
        self.homes += 1
        self.in_front = False

    def game_version(self):
        return self.version


# ---------------------------------------------------------------- detection

def test_identify():
    expect = {
        "mining_bugs": screens.MINING, "crate_gone": screens.MINING, "mining_1bug": screens.MINING,
        "store_closed": screens.MINING, "after_offline": screens.MINING,
        "store_ready": screens.STORE, "store_empty": screens.STORE, "store_top": screens.STORE,
        "bug_popup": screens.LOOTBUG_POPUP, "grid": screens.GRID_MENU, "cloud": screens.CLOUD,
        "off_opts": screens.OFFLINE_OPTIONS, "off_res": screens.OFFLINE_RESULTS,
        "options": screens.UNKNOWN, "store_full": screens.MINING,
        "bug_popup_15": screens.LOOTBUG_POPUP, "anr": screens.ANR,
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


def test_freebie_row_found_on_odd_rows_but_not_in_other_text():
    state, claim = screens.freebie_state(FRAMES["store_odd_y"])
    assert state == "ready" and claim.box[:2] == (550, 838)       # 41 below the header at y=797
    assert screens.freebie_state(FRAMES["store_false_header"]) == ("unknown", None)
    assert screens.A.get("freebie_header").find(FRAMES["store_false_header"]) is None


def test_freebie_job_finds_the_row_on_an_odd_row_after_scrolling():
    dev = FakeDevice("no_bugs", {
        "no_bugs": [(STORE_ICON, ["store_false_header"])],
        ("store_false_header", "swipe"): ["store_odd_y"],
        "store_odd_y": [((550, 838, 644, 905), ["store_empty"])],
        "store_empty": [(STORE_X, ["store_closed"])],
    })
    dev.script[("store_false_header", "swipe")] = ["store_odd_y"]
    assert FreebieJob().run(dev) == "claimed"
    assert [p[0] for p in dev.presses] == ["no_bugs", "store_odd_y", "store_empty"]


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


def test_freebie_job_from_full_bank_icon():
    dev = FakeDevice("store_full", {
        "store_full": [(STORE_ICON, ["store_ready"])],
        "store_ready": [(CLAIM, ["store_toast", "store_empty"])],
        "store_empty": [(STORE_X, ["store_closed"])],
    })
    assert FreebieJob().run(dev) == "claimed"
    assert screens.store_fill(FRAMES["store_full"]) == 1.0


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


def test_menu_close_interrupt_after_three_polls():
    dev = FakeDevice("cloud_uploaded", {"cloud_uploaded": [(STORE_X, ["store_closed"])]})
    i = MenuCloseInterrupt()
    assert not i.handle(dev, dev.capture()) and not i.handle(dev, dev.capture())
    assert i.handle(dev, dev.capture()) and len(dev.presses) == 1
    dev2 = FakeDevice("crate_gone", {})
    assert not any(i.handle(dev2, dev2.capture()) for _ in range(5))      # mining: never


def test_anr_interrupt_presses_wait_only():
    WAIT = (110, 666, 260, 706)
    dev = FakeDevice("anr", {"anr": [(WAIT, ["crate_gone"])]})
    assert AnrInterrupt().handle(dev, dev.capture()) and len(dev.presses) == 1
    assert not AnrInterrupt().handle(dev, FRAMES["crate_gone"])


def test_popup_interrupt_only_presses_x():
    dev = FakeDevice("bug_popup", {"bug_popup": [(BUG_POPUP_X, ["store_closed"])]})
    assert LootbugPopupInterrupt().handle(dev, dev.capture()) and len(dev.presses) == 1


def test_cloud_turns_autosave_off_and_uploads():
    st = _jobs.State(_TMP / "c1.json")
    st.set("cloud_last_upload", 490845)
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


def test_lost_state_never_overwrites_the_cloud_save():
    st = _jobs.State(_TMP / "lost.json")                     # no record of the last upload
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "conflict" and st.get("cloud_conflict")
    assert not any(inside(p[1], UPLOAD_BTN) for p in dev.presses)
    assert _jobs.keep_pi_save(st) is None                    # the user's explicit choice
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(UPLOAD_BTN, ["cloud_armed"])],
        "cloud_armed": [(UPLOAD_BTN, ["cloud_uploaded"])],
        "cloud_uploaded": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "uploaded"
    assert st.get("cloud_last_upload") == 491141 and st.get("cloud_upload_allowed") is None


def test_unreadable_time_after_an_upload_is_read_at_the_next_visit():
    st = _jobs.State(_TMP / "unread.json")
    st.set("cloud_last_upload", 490845)
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(UPLOAD_BTN, ["cloud_armed"])],
        "cloud_armed": [(UPLOAD_BTN, ["cloud_uploaded"])],
        "cloud_uploaded": [(STORE_X, ["store_closed"])],
    })
    real = _pt.read_seconds
    _pt.read_seconds = lambda f, row: None if f is FRAMES["cloud_uploaded"] and row == _pt.CLOUD_ROW else real(f, row)
    try:
        assert CloudSync(st).upload(dev) == "uploaded"
    finally:
        _pt.read_seconds = real
    assert st.get("cloud_last_upload") == 490845 and st.get("cloud_upload_unverified")
    assert CloudSync(st).check(_cloud_visit("cloud_uploaded")) != "conflict"
    assert st.get("cloud_last_upload") == 491141


def _cloud_visit(screen, **more):
    """Mining -> grid -> the given Cloud screen; the X closes it."""
    return FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, [screen])],
        screen: [(STORE_X, ["store_closed"])],
        **more,
    })


def test_late_upload_is_the_pis_own_not_a_conflict():
    st = _jobs.State(_TMP / "late.json")
    st.set("cloud_last_upload", 490845)
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(UPLOAD_BTN, ["cloud_armed"])],
        "cloud_armed": [(UPLOAD_BTN, ["cloud_armed"]), (STORE_X, ["store_closed"])],
    })
    sync = CloudSync(st); sync.UPLOAD_WAIT = 0.01
    try:
        sync.upload(dev)
        raise AssertionError("an unconfirmed upload must count as a failure")
    except JobAborted as e:
        assert "not verified" in str(e)
    assert st.get("cloud_last_upload") == 490845 and st.get("cloud_upload_unverified")
    # next session: the cloud shows 491141 (the Pi's local save read 491099 at the attempt)
    assert CloudSync(st).check(_cloud_visit("cloud_uploaded")) != "conflict"
    assert st.get("cloud_last_upload") == 491141
    assert st.get("cloud_upload_unverified") is None and not st.get("cloud_conflict")


def test_unverified_upload_never_hides_another_devices_save():
    st = _jobs.State(_TMP / "late2.json")
    st.set("cloud_last_upload", 490845)
    st.set("cloud_upload_unverified", [1000, 2000])       # 491141 is far outside the window
    dev = _cloud_visit("cloud_uploaded")
    assert CloudSync(st).check(dev) == "conflict" and st.get("cloud_conflict")
    assert st.get("cloud_last_upload") == 490845 and st.get("cloud_upload_unverified") is None
    assert not any(p[0] == "cloud_uploaded" and inside(p[1], UPLOAD_BTN) for p in dev.presses)


def test_upload_that_never_arrived_is_retried_normally():
    st = _jobs.State(_TMP / "late3.json")
    st.set("cloud_last_upload", 490845)
    st.set("cloud_upload_unverified", [490000, 492000])   # cloud time still the old one
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_autosave_off"])],
        "cloud_autosave_off": [(UPLOAD_BTN, ["cloud_armed"])],
        "cloud_armed": [(UPLOAD_BTN, ["cloud_uploaded"])],
        "cloud_uploaded": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "uploaded"
    assert st.get("cloud_last_upload") == 491141 and st.get("cloud_upload_unverified") is None


DOWNLOAD_BTN = (470, 402, 598, 455)


def test_cloud_downloads_newer_save_from_another_device():
    st = _jobs.State(_TMP / "dl.json")
    st.set("cloud_last_upload", 504982)                      # the Pi's last upload (05d20h16m22s)
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_phone_newer"])],
        "cloud_phone_newer": [(DOWNLOAD_BTN, ["cloud_dl_armed"])],
        "cloud_dl_armed": [(DOWNLOAD_BTN, ["after_download"])],
    })
    assert CloudSync(st).check(dev) == "downloaded"
    assert st.get("cloud_last_upload") == (5 * 24 + 20) * 3600 + 27 * 60 + 13
    assert not st.get("cloud_conflict")
    assert all(not inside(p[1], UPLOAD_BTN) for p in dev.presses)


def test_cloud_never_downloads_its_own_upload():
    st = _jobs.State(_TMP / "dl2.json")
    st.set("cloud_last_upload", (5 * 24 + 20) * 3600 + 27 * 60 + 13)    # == cloud shown
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_phone_newer"])],
        "cloud_phone_newer": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).check(dev) == "in_sync"
    assert all(not inside(p[1], DOWNLOAD_BTN) for p in dev.presses)


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


def test_lootbug_popup_v2252_closes_with_its_x_only():
    assert screens.identify(FRAMES["bug_popup_v2252"]) == screens.LOOTBUG_POPUP
    dev = FakeDevice("bug_popup_v2252", {"bug_popup_v2252": [(BUG_POPUP_X, ["no_bugs"])]})
    _jobs.close_lootbug_popup(dev)
    assert [p[0] for p in dev.presses] == ["bug_popup_v2252"]


def test_read_version():
    for name, cloud, local in (("cloud", (2, 2, 51), (2, 2, 51)),
                               ("cloud_version_block", (2, 2, 52), (2, 2, 51)),
                               ("cloud_same_version", (2, 2, 52), (2, 2, 52))):
        assert _pt.read_version(FRAMES[name], _pt.CLOUD_VERSION_ROW) == cloud, name
        assert _pt.read_version(FRAMES[name], _pt.LOCAL_VERSION_ROW) == local, name
    assert _pt.read_version(FRAMES["grid"], _pt.CLOUD_VERSION_ROW) is None


def test_cloud_version_block_never_presses_download_or_upload():
    st = _jobs.State(_TMP / "vb.json")
    st.set("cloud_last_upload", 517416)
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_version_block"])],
        "cloud_version_block": [(STORE_X, ["store_closed"])],
    })
    assert CloudSync(st).upload(dev) == "conflict"
    assert "v2.2.52" in st.get("cloud_version_block") and "v2.2.51" in st.get("cloud_version_block")
    assert st.get("cloud_conflict") == st.get("cloud_version_block")
    assert all(not inside(p[1], UPLOAD_BTN) and not inside(p[1], DOWNLOAD_BTN) for p in dev.presses)


def test_cloud_version_block_ends_with_a_download_after_the_update():
    st = _jobs.State(_TMP / "vb2.json")
    st.set("cloud_last_upload", 517416)
    st.set("cloud_version_block", "old")
    st.set("cloud_conflict", "old")
    dev = FakeDevice("crate_gone", {
        "crate_gone": [(GRID_BTN, ["grid"])],
        "grid": [(GRID_CLOUD, ["cloud_same_version"])],
        "cloud_same_version": [(DOWNLOAD_BTN, ["cloud_dl_armed"])],
        "cloud_dl_armed": [(DOWNLOAD_BTN, ["after_download"])],
    })
    assert CloudSync(st).check(dev) == "downloaded"
    assert not st.get("cloud_version_block") and not st.get("cloud_conflict")
    assert st.get("cloud_last_upload") == (5 * 24 + 23) * 3600 + 49 * 60 + 51


def test_keep_pi_refuses_during_a_version_block():
    import obelisk.__main__ as cli
    st = _jobs.State()
    st.set("cloud_version_block", "versions differ")
    st.set("cloud_conflict", "versions differ")
    st.set("cloud_last_upload", 517416)
    argv, sys.argv = sys.argv, ["obelisk", "cloud", "--keep-pi"]
    try:
        cli.main()
        raise AssertionError("--keep-pi ran")
    except SystemExit as e:
        assert e.code == 1
    finally:
        sys.argv = argv
    assert st.get("cloud_conflict") and st.get("cloud_last_upload") == 517416
    st.set("cloud_version_block", None)
    st.set("cloud_conflict", None)
    st.set("cloud_last_upload", None)


# ---------------------------------------------------------------- shared log file

def _log_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)


def _age_file(path: Path, days: int) -> dt.date:
    when = dt.datetime.now() - dt.timedelta(days=days)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return when.date()


def test_log_rotated_by_another_process_does_not_strand_the_bot():
    folder = Path(tempfile.mkdtemp(dir=_TMP))
    path = folder / "obelisk.log"
    bot, command = SharedDailyLog(path), SharedDailyLog(path)
    bot.emit(_log_record("bot yesterday"))
    day = _age_file(path, 1)
    command.emit(_log_record("command today"))               # rotates
    bot.emit(_log_record("bot today"))
    bot.close(), command.close()
    assert (folder / f"obelisk.log.{day}").read_text().split() == ["bot", "yesterday"]
    assert path.read_text().splitlines() == ["command today", "bot today"]


def test_log_rotation_keeps_an_existing_file_for_that_day():
    folder = Path(tempfile.mkdtemp(dir=_TMP))
    path = folder / "obelisk.log"
    log = SharedDailyLog(path)
    log.emit(_log_record("later"))
    day = _age_file(path, 1)
    (folder / f"obelisk.log.{day}").write_text("earlier\n")
    log.emit(_log_record("today"))
    log.close()
    assert (folder / f"obelisk.log.{day}").read_text().splitlines() == ["earlier", "later"]
    assert path.read_text().splitlines() == ["today"]


def test_log_rotation_removes_only_files_past_the_limit():
    folder = Path(tempfile.mkdtemp(dir=_TMP))
    path = folder / "obelisk.log"
    today = dt.date.today()
    expired = folder / f"obelisk.log.{today - dt.timedelta(days=61)}"
    kept = folder / f"obelisk.log.{today - dt.timedelta(days=60)}"
    expired.write_text("old\n"), kept.write_text("kept\n")
    log = SharedDailyLog(path)
    log.emit(_log_record("yesterday"))
    _age_file(path, 1)
    log.emit(_log_record("today"))
    log.close()
    assert not expired.exists() and kept.exists()


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
                      planner=SessionPlanner(clock=lambda: clock[0]))
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
    p.start()
    clock[0] += 30; assert p.finish() == 30
    assert p.next_start == 1_000_000.0 + 50 * 60


def test_state_never_writes_back_stale_values():
    a = _jobs.State(_TMP / "shared.json"); b = _jobs.State(_TMP / "shared.json")
    a.set("paused_until", "forever")
    b.set("paused_until", None)                  # e.g. the dashboard resumes
    a.set("cloud_last_upload", 1)                # a long-lived State writes another key
    assert b.get("paused_until") is None and b.get("cloud_last_upload") == 1


def test_state_writers_do_not_undo_each_other():
    import threading
    path = _TMP / "busy.json"
    def writer(n):
        st = _jobs.State(path)
        for i in range(10):
            st.set(f"k{n}_{i}", i)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_jobs.State(path).data) == 60                 # none lost
    assert not path.with_suffix(".tmp").exists()


def test_corrupt_state_is_survivable():
    path = _TMP / "corrupt.json"
    path.write_text('{"cloud_last_up')
    st = _jobs.State(path)
    assert st.get("cloud_last_upload") is None
    st.set("a", 1)
    assert st.get("a") == 1


def test_game_update_alerts_once_per_change():
    import obelisk.scheduler as sc
    sent = []
    orig = sc.alert
    sc.alert = lambda key, title, *a, **k: sent.append(title) or True
    st = _jobs.State(); st.set("game_version", None)
    try:
        dev = FakeDevice("no_bugs", {})
        s = Scheduler(dev, [], poll=0)
        s.note_game_version()
        assert st.get("game_version") == "2.2.52" and sent == []   # first look: nothing to compare
        s.note_game_version()
        assert sent == []
        dev.version = "2.2.53"
        s.note_game_version()
        s.note_game_version()
        assert len(sent) == 1 and "2.2.53" in sent[0] and st.get("game_version") == "2.2.53"
        dev.version = None                                         # unreadable: not a change
        s.note_game_version()
        assert len(sent) == 1 and st.get("game_version") == "2.2.53"
    finally:
        sc.alert = orig
        st.set("game_version", None)


def test_device_reads_the_game_version():
    import obelisk.device as d
    dev = d.Device()
    dev._adb = lambda *a, **k: b"    versionCode=2002052 minSdk=23\n    versionName=2.2.52\n"
    assert dev.game_version() == "2.2.52"
    dev._adb = lambda *a, **k: b"nothing useful"
    assert dev.game_version() is None
    dev._adb = lambda *a, **k: (_ for _ in ()).throw(d.DeviceError("offline"))
    assert dev.game_version() is None


def test_dashboard_failure_tile_counts_today_only():
    import obelisk.dashboard as D
    logs, D.LOGS = D.LOGS, _TMP / "tile"
    try:
        (D.LOGS / "failures").mkdir(parents=True)
        for n in range(14):
            (D.LOGS / "failures" / f"20200101-0000{n:02d}_old.png").write_bytes(b"x")
        today = dt.date.today().strftime("%Y%m%d")
        for n in range(2):
            (D.LOGS / "failures" / f"{today}-0000{n:02d}_new.png").write_bytes(b"x")
        d = D.collect()
        assert d["failures_today"] == 2 and len(d["failures"]) == 12
        assert d["failures"][0].startswith(today)                  # gallery: newest first
    finally:
        D.LOGS = logs


def test_alerts_are_deduplicated_and_never_raise():
    sent = []
    st = _jobs.State(_TMP / "alerts.json")
    send = lambda *a: sent.append(a)
    assert _notify.alert("k", "t", "m", state=st, sender=send)
    assert not _notify.alert("k", "t", "m", state=st, sender=send)       # within 6 h
    assert _notify.alert("k", "t", "m", state=st, sender=send, force=True)
    def boom(*a):
        raise OSError("network down")
    assert not _notify.alert("other", "t", "m", state=st, sender=boom)   # logged, no raise
    assert len(sent) == 2


def test_pause_and_session_now_controls():
    clock = [5_000_000.0]
    st = _jobs.State()                                 # same file the scheduler reads
    st.set("paused_until", "forever")
    dev = FakeDevice("crate_gone", {}, in_front=False)
    planner = SessionPlanner(clock=lambda: clock[0])
    sched = Scheduler(dev, [], poll=0, planner=planner)
    sched.session_step()
    assert dev.launches == 0 and planner.started is None       # paused: no session
    st = _jobs.State(); st.set("paused_until", None); st.set("session_now", True)
    planner.next_start = clock[0] + 9999
    assert sched.control(clock[0]) is None and planner.next_start == clock[0]
    assert _jobs.State().get("session_now") is None
    st = _jobs.State(); st.set("paused_until", clock[0] - 1)    # expired timed pause
    assert sched.control(clock[0]) is None and _jobs.State().get("paused_until") is None


def test_set_options_rows_match_their_reference_screens():
    import importlib.util
    spec = importlib.util.spec_from_file_location("set_options", ROOT / "scripts" / "set_options.py")
    so = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(so)
    labels = [o[1] for o in so.OPTIONS]
    assert len(labels) == len(set(labels))
    for tab, label, src, y, original, bot in so.OPTIONS:
        frame = cv2.imread(str(so.SCREENS / src))
        assert so.find_label(frame, so.label_template(src, y)) == y, label
        assert so.state_at(frame, y) is not None, label
        assert original != bot, label
    true_time = next(o for o in so.OPTIONS if o[1].startswith("Timers Show True Time"))
    assert true_time[4:] == (False, True)                 # your original: off


def test_dashboard_updates_itself():
    import obelisk.dashboard as D
    D.CONF = _TMP / "dash.conf"
    logs, D.LOGS = D.LOGS, _TMP
    try:
        app = D.create_app(); tok = D.ensure_conf()["token"]
        c = app.test_client(); c.get("/?t=" + tok)
        page = c.get("/").get_data(as_text=True)
        ids = re.findall(r'id="([\w-]+)" data-live', page)
        assert len(ids) == len(set(ids)) == 10 and "card-keep" in ids and "updated" in ids
        assert "window.dashRefresh" in page and page.count('http-equiv="refresh"') == 1
        assert '<noscript><meta http-equiv="refresh"' in page  # only without JavaScript
    finally:
        D.LOGS = logs


def test_dashboard_controls_need_token():
    import obelisk.dashboard as D
    D.CONF = _TMP / "dash.conf"
    app = D.create_app(); tok = D.ensure_conf()["token"]
    c = app.test_client()
    assert c.post("/control", data={"a": "pause"}).status_code == 403
    c.get("/?t=" + tok)
    assert c.post("/control", data={"a": "pause1"}).status_code == 303
    assert isinstance(_jobs.State().get("paused_until"), float)
    assert c.post("/control", data={"a": "resume"}).status_code == 303
    assert _jobs.State().get("paused_until") is None
    assert c.get("/").status_code == 200


def _keep_open_sched(clock, cloud=None):
    """Scheduler on the no-bugs mining screen, between sessions (next one far off)."""
    st = _jobs.State()
    st.set("keep_open_until", None); st.set("paused_until", None)
    dev = FakeDevice("no_bugs", {}, in_front=False, after_launch=("no_bugs",))
    planner = SessionPlanner(clock=lambda: clock[0])
    planner.next_start = clock[0] + 40 * 60
    return dev, planner, Scheduler(dev, [], poll=0, planner=planner, cloud=cloud), st


def test_keep_open_holds_the_session_past_its_limit_then_returns_to_normal():
    clock = [6_000_000.0]
    dev, planner, sched, st = _keep_open_sched(clock)
    sched.session_step()
    assert dev.launches == 0 and planner.started is None       # mode off: waits for its time
    until = clock[0] + 2 * 3600
    st.set("keep_open_until", until)
    sched.session_step()
    assert dev.launches == 1 and planner.started is not None   # starts without waiting
    for _ in range(5):                                         # far past MAX_SESSION
        clock[0] += 20 * 60
        sched.session_step()
        assert dev.homes == 0 and dev.in_front and sched.session_failed is False
    assert sched.kept_until == until
    clock[0] = until                                           # time is up
    sched.session_step()
    assert dev.homes == 1 and planner.started is None and sched.kept_until is None
    assert _jobs.State().get("keep_open_until") is None
    assert planner.next_start == clock[0] + planner.SAVE_WAIT  # game saves on HOME first
    sched.session_step()
    assert dev.launches == 1                                   # not force-stopped straight away
    clock[0] += planner.SAVE_WAIT
    sched.session_step()
    assert dev.launches == 2 and planner.started is not None   # the normal closing session
    sched.session_step()
    assert dev.homes == 2                                      # and it ends by itself
    assert planner.next_start == clock[0] + planner.EVERY      # back on the 50 min rhythm


def test_keep_open_end_does_not_upload_on_stale_cloud_info():
    class Cloud:
        calls = []
        def check(self, dev):
            self.calls.append("check"); return "in_sync"
        def upload(self, dev):
            self.calls.append("upload"); return "uploaded"
    clock = [6_100_000.0]
    dev, planner, sched, st = _keep_open_sched(clock, Cloud())
    st.set("keep_open_until", clock[0] + 3600)
    sched.session_step(); sched.session_step()
    st.set("keep_open_until", None)                            # Stop now on the dashboard
    sched.session_step()
    assert dev.homes == 1 and Cloud.calls == ["check"]         # HOME, no upload yet
    clock[0] += planner.SAVE_WAIT
    sched.session_step(); sched.session_step()
    assert dev.homes == 2 and Cloud.calls == ["check", "check", "upload"]
    assert dev.launches == 2                                   # upload follows a fresh restart


def test_pause_ends_keep_open():
    clock = [6_200_000.0]
    dev, planner, sched, st = _keep_open_sched(clock)
    st.set("keep_open_until", clock[0] + 3600)
    sched.session_step(); sched.session_step()
    assert sched.kept_until and dev.homes == 0
    st.set("paused_until", "forever")
    sched.session_step()
    assert dev.homes == 1 and _jobs.State().get("keep_open_until") is None
    clock[0] += 3600
    sched.session_step()
    assert dev.launches == 1                                   # paused: no new session
    st.set("paused_until", None)


def test_cloud_conflict_cancels_keep_open_without_a_restart_loop():
    class Cloud:
        def check(self, dev):
            return "conflict"
        def upload(self, dev):
            raise AssertionError("uploaded during a conflict")
    clock = [6_300_000.0]
    dev, planner, sched, st = _keep_open_sched(clock, Cloud())
    st.set("keep_open_until", clock[0] + 3600)
    sched.session_step()
    assert dev.homes == 1 and _jobs.State().get("keep_open_until") is None
    clock[0] += planner.SAVE_WAIT
    sched.session_step()
    assert dev.launches == 1 and planner.started is None


def test_keep_open_comes_back_after_a_bot_restart():
    clock = [6_400_000.0]
    dev, planner, sched, st = _keep_open_sched(clock)
    st.set("keep_open_until", clock[0] + 3600)
    sched.session_step(); sched.session_step()
    # a new process: no memory of the mode except state.json
    fresh = Scheduler(dev, [], poll=0, planner=SessionPlanner(clock=lambda: clock[0]))
    fresh.session_step(); fresh.session_step()
    assert fresh.kept_until and fresh.planner.started is not None
    st.set("keep_open_until", None)


def test_keep_open_restarts_a_stuck_game_then_gives_up_to_save_buffs():
    clock = [6_500_000.0]
    dev, planner, sched, st = _keep_open_sched(clock)
    sched.LAUNCH_WAIT = sched.SETTLE_TIMEOUT = 0
    st.set("keep_open_until", clock[0] + 12 * 3600)
    sched.session_step(); sched.session_step()
    assert dev.launches == 1 and sched.last_screen == screens.MINING
    dev.queue = ["options"]                                    # a screen the bot doesn't know
    sched.session_step()
    clock[0] += 4 * 60
    sched.session_step()
    assert dev.launches == 1                                   # not yet
    clock[0] += 60
    sched.session_step()
    assert dev.launches == 2 and dev.homes == 0                # recovered, mode still on
    sched.session_step()
    assert sched.off_mining_since is None and sched.stuck_restarts == 0   # mining again
    dev.after_launch = ("options",)                            # now restarts don't help
    dev.queue = ["options"]
    for _ in range(sched.KEEP_OPEN_MAX_RESTARTS + 1):
        sched.session_step()                                   # lost mining
        clock[0] += 5 * 60
        sched.session_step()                                   # restart, or give up
    assert dev.launches == 2 + sched.KEEP_OPEN_MAX_RESTARTS
    assert _jobs.State().get("keep_open_until") is None
    sched.session_step()
    assert dev.homes == 1 and sched.kept_until is None         # game away, buffs saved


def test_stopping_sends_the_game_away_except_in_always_open():
    e = JobAborted("x", FRAMES["options"])
    clock = [6_600_000.0]
    dev, planner, sched, st = _keep_open_sched(clock)
    st.set("keep_open_until", clock[0] + 3600)
    dev.in_front = True
    sched.job_failed("a", e); sched.job_failed("b", e)
    try:
        sched.job_failed("c", e)
        raise AssertionError("did not stop")
    except SystemExit as ex:
        assert ex.code == 3
    assert dev.homes == 1 and _jobs.State().get("keep_open_until") is None
    class Broken(FakeDevice):                                  # HOME failing must not block the alert
        def go_home(self):
            raise OSError("adb gone")
    sched = Scheduler(Broken("no_bugs", {}), [], poll=0, planner=SessionPlanner())
    try:
        sched.stop("k", "t", "m")
    except SystemExit as ex:
        assert ex.code == 3
    dev = FakeDevice("no_bugs", {})
    try:
        Scheduler(dev, [], poll=0).stop("k", "t", "m")        # planner None = --always-open
    except SystemExit:
        pass
    assert dev.homes == 0


def test_restart_game_saves_first_if_the_game_is_in_front():
    import obelisk.device as d
    def run(front):
        calls = []
        dev = d.Device()
        def adb(*a, **k):
            calls.append(" ".join(a))
            return f"topResumedActivity=x {d.PACKAGE if front else 'launcher'}/.A".encode()
        dev._adb = adb
        orig, d.time.sleep = d.time.sleep, lambda s: calls.append(f"sleep {s}")
        try:
            dev.restart_game()
        finally:
            d.time.sleep = orig
        return [c for c in calls if "dumpsys" not in c]
    front = run(True)
    assert "HOME" in front[0] and front[1] == f"sleep {d.SAVE_WAIT}" and "force-stop" in front[2]
    assert "force-stop" in run(False)[0]


def test_founder_empty_session_keeps_a_frame():
    import shutil
    shutil.rmtree(_sched.FOUNDER_EMPTY_DIR, ignore_errors=True)
    clock = [6_700_000.0]
    st = _jobs.State(); st.set("keep_open_until", None); st.set("paused_until", None)
    founder = FounderJob(state=_jobs.State(_TMP / "fe.json"), clock=lambda: clock[0])
    dev = FakeDevice("no_bugs", {}, in_front=False, after_launch=("no_bugs",))
    sched = Scheduler(dev, [founder], poll=0, planner=SessionPlanner(clock=lambda: clock[0]))
    sched.session_step(); sched.session_step()
    assert dev.homes == 1 and len(list(_sched.FOUNDER_EMPTY_DIR.glob("*.jpg"))) == 1
    assert _jobs.State().get("last_session_at") == clock[0]    # heartbeat for the health check
    keep, _sched.FOUNDER_EMPTY_KEEP = _sched.FOUNDER_EMPTY_KEEP, 2
    try:
        for n in ("20200101-000000", "20200101-000001", "20200101-000002"):
            (_sched.FOUNDER_EMPTY_DIR / f"{n}.jpg").write_bytes(b"x")
        sched.founder_claimed = True
        sched.note_no_founder()                                # a claim: nothing saved
        assert len(list(_sched.FOUNDER_EMPTY_DIR.glob("*.jpg"))) == 4
        sched.founder_claimed = False
        sched.note_no_founder()
        names = sorted(p.name for p in _sched.FOUNDER_EMPTY_DIR.glob("*.jpg"))
        assert len(names) == 2 and names[0] == "20200101-000002.jpg"   # oldest pruned
    finally:
        _sched.FOUNDER_EMPTY_KEEP = keep


def test_gems_wait_out_an_overlay_and_give_up_cleanly():
    wait, _gems.STABLE_WAIT = _gems.STABLE_WAIT, 30.0
    try:
        dev = FakeDevice("blank", {})
        dev.queue = ["blank", "blank", "crate_gone", "crate_gone"]
        assert _gems.stable_gems(dev) == 658
        dev.queue = ["mining_bugs", "crate_gone", "crate_gone"]    # must agree twice
        assert _gems.stable_gems(dev) == 658
    finally:
        _gems.STABLE_WAIT = wait
    assert _gems.stable_gems(FakeDevice("blank", {})) is None


def test_gem_guard_uses_the_last_reading_when_it_cannot_read():
    class SpendJob:
        name = "spend"
        def run(self, dev):
            dev.queue = ["mining_bugs"]                       # 632 gems
            return "done"
    dev = FakeDevice("blank", {})
    s = Scheduler(dev, [], poll=0)
    s.last_gems = 658
    try:
        s.run_job(SpendJob())
        raise AssertionError("gem guard did not trip")
    except SystemExit as e:
        assert e.code == 3


def test_gem_guard_covers_what_happens_between_jobs():
    class Never:
        name = "never"
        def run(self, dev):
            raise AssertionError("job ran after a gem drop")
    s = Scheduler(FakeDevice("crate_gone", {}), [], poll=0)    # 658 gems
    s.last_gems = 700
    try:
        s.run_job(Never())
        raise AssertionError("gem guard did not trip")
    except SystemExit as e:
        assert e.code == 3
    clock = [6_800_000.0]                                      # a new session: new baseline
    st = _jobs.State(); st.set("keep_open_until", None); st.set("paused_until", None)
    dev = FakeDevice("no_bugs", {}, in_front=False, after_launch=("no_bugs",))
    s = Scheduler(dev, [], poll=0, planner=SessionPlanner(clock=lambda: clock[0]))
    s.last_gems = 999_999                                      # spent on the phone since
    s.session_step()
    s.session_step()
    assert dev.homes == 1                                      # finished, no false stop


def test_open_conflict_alerts_again_through_the_6_hour_limit():
    import obelisk.notify as nt
    sent = []
    orig = nt.alert
    nt.alert = lambda key, *a, force=False, **k: sent.append((key, force)) or True
    try:
        st = _jobs.State(_TMP / "remind.json")
        st.set("cloud_last_upload", 490845)
        assert CloudSync(st).check(_cloud_visit("cloud_uploaded")) == "conflict"
        assert CloudSync(st).check(_cloud_visit("cloud_uploaded")) == "conflict"
    finally:
        nt.alert = orig
    # first alert always goes out; repeats are limited by the shared key
    assert sent == [("cloud_conflict", True), ("cloud_conflict", False)]


def test_dashboard_keep_pi_button():
    import obelisk.dashboard as D
    D.CONF = _TMP / "dash.conf"
    logs, D.LOGS = D.LOGS, _TMP
    try:
        app = D.create_app(); tok = D.ensure_conf()["token"]
        c = app.test_client()
        assert c.post("/control", data={"a": "keep_pi"}).status_code == 403
        c.get("/?t=" + tok)
        st = _jobs.State()
        st.set("cloud_conflict", None); st.set("cloud_last_upload", 5); st.set("session_now", None)
        assert 'value="keep_pi"' not in c.get("/").get_data(as_text=True)
        assert c.post("/control", data={"a": "keep_pi"}).status_code == 303
        assert st.get("cloud_last_upload") == 5 and not st.get("session_now")   # no conflict: no-op
        st.set("cloud_version_block", "versions differ"); st.set("cloud_conflict", "versions differ")
        page = c.get("/").get_data(as_text=True)
        assert 'value="keep_pi"' not in page and "Update the game" in page
        assert c.post("/control", data={"a": "keep_pi"}).status_code == 303
        assert st.get("cloud_conflict") and st.get("cloud_last_upload") == 5    # refused
        st.set("cloud_version_block", None); st.set("cloud_conflict", "phone uploaded")
        assert 'value="keep_pi"' in c.get("/").get_data(as_text=True)
        assert c.post("/control", data={"a": "keep_pi"}).status_code == 303
        assert st.get("cloud_conflict") is None and st.get("cloud_last_upload") is None
        assert st.get("session_now") is True and st.get("cloud_upload_allowed") is True
        st.set("session_now", None); st.set("cloud_upload_allowed", None)
    finally:
        D.LOGS = logs


def test_bot_status_and_its_alert():
    import obelisk.bootcheck as bc
    import obelisk.notify as nt
    st = _jobs.State(_TMP / "bot_state.json")
    now = 7_000_000.0
    up, down = (lambda: "active"), (lambda: "inactive")
    assert _real_bot_status(st, up, now) == (True, "running", False)   # no session yet: can't judge
    st.set("last_session_at", now - 3600)
    assert _real_bot_status(st, up, now)[0]
    assert _real_bot_status(st, down, now) == (False, "service inactive", True)
    st.set("last_session_at", now - 3 * 3600)
    assert _real_bot_status(st, up, now) == (False, "no session for 180 min", False)
    st.set("paused_until", "forever")
    assert _real_bot_status(st, up, now)[0]                            # paused on purpose
    assert not _real_bot_status(st, down, now)[0]                      # but a dead service still counts
    st.set("paused_until", None)
    sent = []
    orig_alert, orig_up = nt.alert, bc.uptime_s
    nt.alert = lambda key, *a, **k: sent.append(key) or True
    bc.uptime_s = lambda: 3600
    try:
        assert _health.alert_if_unhealthy(None, _health.Health(bot_ok=False), st)
        st.set("outage", {"since": 1})                                 # the bot is reporting it
        assert not _health.alert_if_unhealthy(None, _health.Health(bot_ok=False), st)
        assert _health.alert_if_unhealthy(None, _health.Health(bot_ok=False, bot_service_down=True), st)
        st.set("outage", None)
    finally:
        nt.alert, bc.uptime_s = orig_alert, orig_up
    assert sent == ["health_bot", "health_bot"]
    h = _health.Health(checks=[("Bot", False, "service inactive")], bot_ok=False)
    assert "Pi and Android OK" in h.summary() and "Bot: service inactive" in h.summary()


def test_dashboard_keep_open_controls():
    import obelisk.dashboard as D
    D.CONF = _TMP / "dash.conf"
    logs, D.LOGS = D.LOGS, _TMP                               # state.json = the tests' State file
    try:
        app = D.create_app(); tok = D.ensure_conf()["token"]
        c = app.test_client()
        assert c.post("/control", data={"a": "keep_open", "minutes": 90}).status_code == 403
        c.get("/?t=" + tok)
        st = _jobs.State(); st.set("keep_open_until", None); st.set("paused_until", None)
        st.set("cloud_conflict", None)
        page = c.get("/").get_data(as_text=True)
        assert 'name="minutes"' in page and "Keep open for" in page
        assert 'value="keep_stop" disabled' in page           # always shown, usable only when on
        before = dt.datetime.now().timestamp()
        assert c.post("/control", data={"a": "keep_open", "minutes": 90}).status_code == 303
        assert before + 5400 <= _jobs.State().get("keep_open_until") <= before + 5410
        page = c.get("/").get_data(as_text=True)
        assert 'value="keep_stop" disabled' not in page
        assert "game kept open until" in page and "1 h 30 min left" in page
        for bad in ("", "abc", "5", "9999"):                  # slider range only
            assert c.post("/control", data={"a": "keep_open", "minutes": bad}).status_code == 400
        assert c.post("/control", data={"a": "keep_stop"}).status_code == 303
        assert _jobs.State().get("keep_open_until") is None
        st.set("keep_open_until", dt.datetime.now().timestamp() + 600)   # pausing ends it
        assert c.post("/control", data={"a": "pause1"}).status_code == 303
        assert _jobs.State().get("keep_open_until") is None
        assert c.post("/control", data={"a": "keep_open", "minutes": 60}).status_code == 303
        assert _jobs.State().get("keep_open_until") is None   # refused while paused
        assert c.post("/control", data={"a": "resume"}).status_code == 303
        st.set("cloud_conflict", "x")
        assert c.post("/control", data={"a": "keep_open", "minutes": 60}).status_code == 303
        assert _jobs.State().get("keep_open_until") is None   # refused during a conflict
        st.set("cloud_conflict", None)
        st.set("keep_open_until", dt.datetime.now().timestamp() - 5)     # ran out, bot not looked yet
        assert 'value="keep_stop" disabled' in c.get("/").get_data(as_text=True)
        st.set("keep_open_until", None)
        assert D.fmt_minutes(15) == "15 min" and D.fmt_minutes(60) == "1 h"
        assert D.fmt_minutes(135) == "2 h 15 min"
    finally:
        D.LOGS = logs


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


def test_job_waits_out_a_brief_overlay():
    # 2026-11-02: a "Free bomb" toast hid the anchors and the freebie job aborted
    dev = FakeDevice("mining_bugs", {
        "mining_bugs": [(STORE_ICON, ["store_ready"])],
        "store_ready": [(CLAIM, ["store_toast", "store_empty"])],
        "store_empty": [(STORE_X, ["store_closed"])],
    })
    dev.queue = ["options", "options", "mining_bugs"]        # overlay for two polls
    assert FreebieJob().run(dev) == "claimed"


def test_failure_policy_per_session():
    def stops(sched):
        try:
            sched.record_session_result()
        except SystemExit as e:
            assert e.code == 3
            return True
        return False
    s = Scheduler(FakeDevice("mining_bugs", {}), [], poll=0)
    for failed in (True, True, False, True, True, False):    # never 3 failing sessions in a row
        s.session_failed = failed
        assert not stops(s)
    for n in range(3):
        s.session_failed = True
        assert stops(s) == (n == 2)
    # within one session: 3 failures in a row stop it
    s = Scheduler(FakeDevice("mining_bugs", {}), [], poll=0)
    e = JobAborted("x", FRAMES["options"])
    s.job_failed("freebie", e); s.job_failed("lootbug", e)
    try:
        s.job_failed("cloud upload", e)
    except SystemExit as ex:
        assert ex.code == 3
    else:
        raise AssertionError("3 failures in a session did not stop")


def test_failing_interrupt_never_crashes_the_bot():
    class Broken:
        def handle(self, dev, frame):
            raise JobAborted("button 'offline_close' not found", frame)
    sched = Scheduler(FakeDevice("mining_bugs", {}), [], interrupts=[Broken()], poll=0)
    sched.tick()                                             # logged, not raised
    assert sched.handle_interrupts(FRAMES["mining_bugs"]) is True


def test_press_anchor_aborts_instead_of_crashing():
    try:
        _jobs.press_anchor(FakeDevice("options", {}), "store_close", FRAMES["mining_bugs"])
    except JobAborted as e:
        assert "store_close" in str(e)
    else:
        raise AssertionError("no abort")


def test_periodic_health_alerts_only_when_unhealthy():
    import obelisk.bootcheck as bc
    import obelisk.notify as nt
    st = _jobs.State(_TMP / "health_state.json")
    sent = []
    orig_alert, orig_up = nt.alert, bc.uptime_s
    nt.alert = lambda key, *a, **k: sent.append(key) or True
    bc.uptime_s = lambda: 3600
    try:
        assert not _health.alert_if_unhealthy(None, _health.Health(level=_health.OK), st)
        assert _health.alert_if_unhealthy(None, _health.Health(level=_health.RESTART), st)
        st.set("outage", {"since": 1})                       # the bot is already reporting
        assert not _health.alert_if_unhealthy(None, _health.Health(level=_health.REBOOT), st)
        st.set("outage", None)
        bc.uptime_s = lambda: 300                            # booting: bootcheck reports
        assert not _health.alert_if_unhealthy(None, _health.Health(level=_health.REBOOT), st)
    finally:
        nt.alert, bc.uptime_s = orig_alert, orig_up
    assert sent == ["health_restart"]


# ---------------------------------------------------------------- outages

def test_outage_alerts_remind_give_up_and_recover():
    import obelisk.outage as out
    st = _jobs.State(_TMP / "outage_state.json")
    sent = []
    snd = lambda title, msg, prio, tags: sent.append(title)
    t0 = 1_000_000.0
    assert not out.report_down("crashed", "E1", st, t0, snd)
    assert sent == ["crashed"]
    for m in range(1, 30):                                # retries each minute: quiet
        assert not out.report_down("can't start", "E2", st, t0 + 60 * m, snd)
    assert len(sent) == 1
    t = t0
    for m in range(30, 120):
        t = t0 + 60 * m
        assert not out.report_down("can't start", "E2", st, t, snd)
    assert [x.startswith("Obelisk bot still down") for x in sent[1:]] == [True, True]
    assert out.report_down("can't start", "E2", st, t0 + 7200, snd)   # give up
    assert sent[-1].startswith("Obelisk bot STOPPED")
    assert out.report_up(st, t0 + 9000, snd) and sent[-1] == "Obelisk bot OK again"
    assert not out.report_up(st, t0 + 9100, snd)          # only once
    # a failure long after the last one starts a new outage (no instant give-up)
    out.report_down("crashed", "E3", st, t0 + 9000, snd)
    assert not out.report_down("crashed", "E3", st, t0 + 99_000, snd)
    assert sent[-1] == "crashed"


def test_capture_timeout_nudges_host_and_retries():
    import obelisk.device as d
    calls = {"adb": 0, "nudge": 0}
    raw = np.zeros(16 + 4 * 4, np.uint8)
    raw[:16] = np.frombuffer(np.array([2, 2, 1, 0], "<u4").tobytes(), np.uint8)
    def fake_adb(*a, **k):
        calls["adb"] += 1
        if calls["adb"] == 1:
            raise d.DeviceError("timeout: adb exec-out screencap")
        return raw.tobytes()
    orig_nudge, orig_sleep = d.nudge_host_display, d.time.sleep
    d.nudge_host_display = lambda: calls.__setitem__("nudge", calls["nudge"] + 1) or True
    d.time.sleep = lambda s: None
    try:
        dev = d.Device()
        dev._adb = fake_adb
        assert dev.capture().shape[:2] == (2, 2)
        assert calls == {"adb": 2, "nudge": 1}
        dev._adb = lambda *a, **k: (_ for _ in ()).throw(d.DeviceError("adb -> offline"))
        try:
            dev.capture()
        except d.DeviceError:
            pass
        assert calls["nudge"] == 1                       # only timeouts nudge
    finally:
        d.nudge_host_display, d.time.sleep = orig_nudge, orig_sleep


def test_health_verdicts():
    import obelisk.device as d
    import obelisk.health as hl
    desk = np.zeros((839, 1706, 3), np.uint8)
    running = lambda: {"Session": "RUNNING", "Container": "RUNNING"}

    class Dev:
        serial = "x"
        def __init__(self, frozen=False):
            self.frozen = frozen
        def capture(self):
            if self.frozen:
                raise d.DeviceError("timeout: adb exec-out screencap")
            return FRAMES["mining_bugs"]
        def game_in_front(self):
            return True

    adb_ok = lambda: (True, "device")
    cases = [   # (dev, grab, waydroid, adb) -> level
        (Dev(), lambda: desk, running, adb_ok, hl.OK),
        (Dev(frozen=True), lambda: desk, running, adb_ok, hl.RESTART),     # 2026-11-02
        (Dev(), lambda: desk, lambda: {"Session": "STOPPED", "Container": "RUNNING"}, adb_ok, hl.RESTART),
        (Dev(), lambda: desk, running, lambda: (False, "offline"), hl.RESTART),
        (Dev(), lambda: None, running, adb_ok, hl.REBOOT),
    ]
    for dev, grab, wd, adb, level in cases:
        h = hl.check(dev, grab=grab, wd_status=wd, adb_state=adb)
        assert h.level == level, (h.level, level, h.checks)
        img = hl.render(h)                       # renders with or without pictures
        assert img.shape[1] == 720


def test_outage_alert_carries_health_picture():
    import obelisk.outage as out
    st = _jobs.State(_TMP / "outage_state2.json")
    sent = []
    snd = lambda title, msg, prio, tags, image=None: sent.append((title, msg, image))
    calls = []
    fn = lambda: calls.append(1) or ("/tmp/h.png", "REBOOT THE PI - x.")
    out.report_down("crashed", "E", st, 1000.0, snd, image_fn=fn)
    out.report_down("crashed", "E", st, 1060.0, snd, image_fn=fn)   # quiet retry: no picture taken
    assert len(calls) == 1 and sent[0][2] == "/tmp/h.png" and sent[0][1].startswith("REBOOT THE PI")


# real lines from the 2026-11-07 outage (Android sound out of memory)
AUDIO_LOG = b"""\
  1794088782.237    89 30430 E AudioFlinger: createTrack_l() initCheck failed -12; no control block?
  1794088782.238 126004 60521 E AudioTrack: createTrack_l(0): AudioFlinger could not create track, status: -12 output 0
  1794088782.238 126004 60521 E libOpenSLES: AudioTrack::initCheck status 4294967284
  1794088782.367 126004 60521 F DEBUG   :       #03 pc 00000000011e9c14  /data/app/x/split_config.arm64_v8a.apk!libyoyo.so (offset 0x134000) (Audio_Init()+640)
"""


def test_device_spots_broken_android_audio():
    import obelisk.device as d
    dev = d.Device()
    calls = []
    dev._adb = lambda *a, **k: calls.append(a) or AUDIO_LOG
    assert dev.audio_broken(1794088700.0)
    assert calls[0] == ("logcat", "-d", "-v", "epoch", "-t", "1794088700.000")
    dev._adb = lambda *a, **k: AUDIO_LOG.splitlines()[1]           # sound refused, no crash
    assert not dev.audio_broken(0)
    dev._adb = lambda *a, **k: b"I yoyo    : Audio_Init()\n"          # normal start
    assert not dev.audio_broken(0)
    dev._adb = lambda *a, **k: (_ for _ in ()).throw(d.DeviceError("offline"))
    assert not dev.audio_broken(0)


def test_broken_audio_restarts_waydroid_once_then_stops():
    import obelisk.scheduler as sc
    sent, restarts = [], []
    orig_alert, orig_wait = sc.alert, sc.Scheduler.wait_for_game
    sc.alert = lambda key, title, *a, **k: sent.append(title) or True
    st = _jobs.State(); st.set("audio_restart_at", None)
    try:
        dev = FakeDevice("blank", {}, after_launch=("blank",))         # home screen: not the game
        s = Scheduler(dev, [], poll=0)
        s.LAUNCH_WAIT = 0
        assert s.wait_for_game() is False and restarts == []        # unknown screen, sound fine
        dev.audio = True
        s.restart_for_audio = lambda: restarts.append(1)
        assert s.wait_for_game() is False and restarts == [1]       # wired to the launch check
        del s.restart_for_audio
        go = lambda what: restarts.append(what)
        try:
            s.restart_for_audio(now=1000.0, start_restart=go)
            raise AssertionError("must exit")
        except SystemExit as e:
            assert e.code == 0
        assert restarts[-1] == "all" and st.get("audio_restart_at") == 1000.0
        assert sent == ["Obelisk: restarting Waydroid"]
        try:                                                        # back within 6 h: give up
            s.restart_for_audio(now=1000.0 + 3600, start_restart=go)
            raise AssertionError("must exit")
        except SystemExit as e:
            assert e.code == sc.EXIT_TOO_MANY_FAILURES
        assert restarts.count("all") == 1 and "STOPPED" in sent[-1]
        st.set("audio_restart_at", None)                            # restart can't start: stop
        try:
            s.restart_for_audio(now=50000.0, start_restart=lambda w: "a restart is already running")
            raise AssertionError("must exit")
        except SystemExit as e:
            assert e.code == sc.EXIT_TOO_MANY_FAILURES
        assert st.get("audio_restart_at") is None
    finally:
        sc.alert, sc.Scheduler.wait_for_game = orig_alert, orig_wait
        st.set("audio_restart_at", None)


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
