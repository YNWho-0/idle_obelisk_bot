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
                          AnrInterrupt, LootbugJob, LootbugPopupInterrupt, MenuCloseInterrupt,
                          OfflineGainsInterrupt)
from obelisk.scheduler import Scheduler           # noqa: E402
from obelisk.session import SessionPlanner        # noqa: E402

# keep test artefacts out of the real logs/
_TMP = Path(tempfile.mkdtemp(prefix="obelisk-tests-"))
_jobs.FAIL_DIR = _TMP / "failures"
_gems.UNREAD_DIR = _TMP / "gem_unread"
_pt.UNREAD_DIR = _TMP / "playtime_unread"
_jobs.State.PATH = _TMP / "state.json"
import obelisk.notify as _notify                  # noqa: E402
_notify.CONF = _TMP / "no-notify.conf"            # tests must never send real alerts

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

    def restart_game(self):
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


def test_state_never_writes_back_stale_values():
    a = _jobs.State(_TMP / "shared.json"); b = _jobs.State(_TMP / "shared.json")
    a.set("paused_until", "forever")
    b.set("paused_until", None)                  # e.g. the dashboard resumes
    a.set("cloud_last_upload", 1)                # a long-lived State writes another key
    assert b.get("paused_until") is None and b.get("cloud_last_upload") == 1


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
