"""Screen definitions: each screen is a set of anchors that must (not) match."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import WIDTH, HEIGHT
from .vision import ANCHOR_DIR, Anchor, Box, Match, clamp, green_fraction

MINING, STORE, LOOTBUG_POPUP, UNKNOWN = "mining", "store", "lootbug_popup", "unknown"
OFFLINE_OPTIONS, OFFLINE_RESULTS = "offline_options", "offline_results"
GRID_MENU, CLOUD = "grid_menu", "cloud"
ANR = "android_not_responding"

# LAYOUT v2: the game's native 720x1280 layout (no black bars) used since the
# 2026-11-01 save reload. v1 values are in git history / screens/v1layout.

# Where the Store's "Freebie Pack" header may be (Store scroll position varies:
# it has opened both at the top and at the Resource Packs). The Store only scrolls
# up and down, so the header stays at x=137; keeping to that column rules out other
# "Freebie Pack" text, e.g. "+1% Freebie Pack Skill Bit Chance!" (x=194, score 0.92).
FREEBIE_BAND: Box = (131, 130, 303, 1170)
# Claim button relative to the header's top-left: header (137,624) -> claim (550,665).
CLAIM_OFFSET = (413, 41)
CLAIM_SIZE = (94, 61)

# Banked lootbugs line up at fixed spots (~(285,645), (393,645), (500,655)).
LOOTBUG_FIELD: Box = (180, 560, 600, 760)
LOOTBUG_HOMES = {
    "lootbug_red_1": (377, 605, 409, 690),
}

# Left-side timer list. While at least one lootbug is stored it has a row
# "[bug] N [bug All]" whose y depends on the buff rows above it (y=643 and y=780
# seen). Its icon scores 0.86-1.0 there and <= 0.53 on every frame without it.
# A DOUBLE tap on it opens Loot-All, which also loots the bug on the field
# (2026-11-10: one tap only shows "Tap again to loot all").
HUD_LIST: Box = (0, 560, 130, 1110)

# Playfield area where the swaying/falling Founder parachute crate is searched.
# Crates score 0.61-1.0 on reference frames; frames without a crate <= 0.33.
FOUNDER_AREA: Box = (40, 90, 690, 1170)

# The Store icon's background fills with green from the bottom as freebies
# bank up (14 of 49 rows at 7/19 banked). Measured on x=586.
STORE_FILL_X = 586
STORE_FILL_ROWS = (1184, 1233)   # interior, top..bottom (exclusive)


class A:
    """Lazily loaded anchors (so importing never needs the PNGs)."""

    @staticmethod
    @lru_cache(maxsize=None)
    def get(name: str) -> Anchor:
        spec = {
            "anvil_button":   dict(home=(58, 1188, 106, 1234)),
            "grid_button":    dict(home=(338, 1188, 386, 1234)),
            "store_icon":     dict(home=(598, 1195, 634, 1205)),
            "store_icon_full": dict(home=(594, 1192, 634, 1226), threshold=0.85),
            "store_close":    dict(home=(414, 1188, 466, 1234)),
            "loot_tables":    dict(home=(553, 89, 663, 124)),
            # 1.0 on even rows, 0.89 on odd ones (the text draws differently); all
            # four chances landing on odd rows lost the job 2 of 42 visits. Other
            # text in this column scores <= 0.59.
            "freebie_header": dict(home=(137, 624, 297, 651), search=FREEBIE_BAND, threshold=0.85),
            "claim_ready":    dict(home=(550, 665, 644, 726), threshold=0.85),
            "claim_empty":    dict(home=(550, 665, 644, 726), threshold=0.85),
            "lootbug_header": dict(home=(250, 128, 420, 154), margin=20, threshold=0.85),
            "lootbug_close":  dict(home=(575, 148, 625, 197)),
            "lootbug_close_2": dict(home=(575, 148, 625, 197)),
            **{n: dict(home=LOOTBUG_HOMES[n], threshold=0.75, search=LOOTBUG_FIELD)
               for n in LOOTBUG_HOMES},
            "lootbug_stored": dict(home=(10, 780, 36, 806), threshold=0.75, search=HUD_LIST),
            "offline_options_label": dict(home=(214, 704, 506, 763)),
            "offline_confirm":       dict(home=(74, 1168, 646, 1218)),
            "offline_results_label": dict(home=(284, 10, 436, 31)),
            "offline_close":         dict(home=(74, 1168, 646, 1218)),
            "anr_title":  dict(home=(117, 560, 452, 588), margin=40),
            "anr_wait":   dict(home=(110, 666, 260, 706), margin=40),
            "grid_cloud_button": dict(home=(194, 927, 260, 993)),
            "cloud_title":       dict(home=(178, 329, 450, 356)),
            "founder_crate_1": dict(home=(432, 582, 530, 632), threshold=0.6, search=FOUNDER_AREA),
            "founder_crate_2": dict(home=(435, 590, 533, 640), threshold=0.6, search=FOUNDER_AREA),
            "founder_crate_3": dict(home=(448, 590, 546, 640), threshold=0.6, search=FOUNDER_AREA),
        }[name]
        return Anchor(name, **spec)


def right_size(frame: np.ndarray) -> bool:
    return frame.shape[:2] == (HEIGHT, WIDTH)


def store_button(frame: np.ndarray) -> Optional[Match]:
    """The bottom-bar Store button: normal (awning) or 'freebie bank full' form."""
    return A.get("store_icon").find(frame) or A.get("store_icon_full").find(frame)


def lootbug_close(frame: np.ndarray) -> Optional[Match]:
    """The Lootbug popup's top-right X (drawn slightly differently since v2.2.52)."""
    return A.get("lootbug_close").find(frame) or A.get("lootbug_close_2").find(frame)


def identify(frame: np.ndarray) -> str:
    if not right_size(frame):
        return UNKNOWN
    if A.get("anr_title").find(frame) and A.get("anr_wait").find(frame):
        return ANR
    if A.get("lootbug_header").find(frame) and lootbug_close(frame):
        return LOOTBUG_POPUP
    if A.get("offline_options_label").find(frame) and A.get("offline_confirm").find(frame):
        return OFFLINE_OPTIONS
    if A.get("offline_results_label").find(frame) and A.get("offline_close").find(frame):
        return OFFLINE_RESULTS
    if A.get("store_close").find(frame) and A.get("loot_tables").find(frame):
        return STORE
    if A.get("store_close").find(frame) and A.get("cloud_title").find(frame):
        return CLOUD
    if A.get("store_close").find(frame) and A.get("grid_cloud_button").find(frame):
        return GRID_MENU
    if (A.get("anvil_button").find(frame) and A.get("grid_button").find(frame)
            and store_button(frame) and not A.get("store_close").find(frame)):
        return MINING
    return UNKNOWN


def store_fill(frame: np.ndarray) -> float:
    """0..1: how far the Store icon's green 'banked freebies' gauge has risen."""
    if right_size(frame) and A.get("store_icon_full").find(frame) is not None:
        return 1.0                       # bank full: the whole icon is green
    if not right_size(frame) or A.get("store_icon").find(frame) is None:
        return 0.0
    y1, y2 = STORE_FILL_ROWS
    x = STORE_FILL_X
    return green_fraction(frame, (x, y1, x + 1, y2))


def store_badge(frame: np.ndarray) -> bool:
    """True when at least one freebie seems to be banked (gauge >= 2 rows)."""
    return store_fill(frame) >= 2 / 42


def freebie_state(frame: np.ndarray) -> tuple[str, Optional[Match]]:
    """('ready' | 'empty' | 'unknown', claim button match).

    'ready' needs ALL of: Freebie header found, ready-Claim template at the
    fixed offset from it, and the button area mostly green. Anything else is
    never pressed.
    """
    if not right_size(frame):
        return "unknown", None
    header = A.get("freebie_header").find(frame)
    if header is None:
        return "unknown", None
    x = header.box[0] + CLAIM_OFFSET[0]
    y = header.box[1] + CLAIM_OFFSET[1]
    box = (x, y, x + CLAIM_SIZE[0], y + CLAIM_SIZE[1])
    search = (box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4)
    ready = A.get("claim_ready").find(frame, search)
    if ready and green_fraction(frame, ready.box) >= 0.5:
        return "ready", ready
    empty = A.get("claim_empty").find(frame, search)
    if empty and green_fraction(frame, empty.box) < 0.05:
        return "empty", empty
    return "unknown", None


WIKI_LOOTBUG_THRESHOLD = 0.70   # see tests: real bugs vs bug-free frames


@lru_cache(maxsize=None)
def _wiki_lootbugs() -> tuple:
    """(name, bgr, mask) for all 14 skins, from scripts/make_wiki_lootbugs.py."""
    out = []
    for path in sorted((ANCHOR_DIR / "lootbug_wiki").glob("*.png")):
        t = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        out.append((path.stem, np.ascontiguousarray(t[..., :3]),
                    np.ascontiguousarray((t[..., 3] > 0).astype(np.uint8))))
    return tuple(out)


def wiki_lootbug(frame: np.ndarray) -> Optional[Match]:
    """Best match over all 14 wiki skins (masked), or None."""
    x1, y1, x2, y2 = clamp(LOOTBUG_FIELD, frame)
    region = frame[y1:y2, x1:x2]
    best = None
    for name, tpl, mask in _wiki_lootbugs():
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, score, _, (bx, by) = cv2.minMaxLoc(res)
        if score >= WIKI_LOOTBUG_THRESHOLD and (best is None or score > best.score):
            th, tw = tpl.shape[:2]
            best = Match(f"wiki_{name}", (x1 + bx, y1 + by, x1 + bx + tw, y1 + by + th), float(score))
    return best


def lootbug_sprite(frame: np.ndarray) -> Optional[Match]:
    """Best-matching lootbug on the field (the press target), or None.

    Uses in-game crops (Default/red, Halloween/purple) plus masked templates of
    all 14 skins from the wiki (regular + golden), so any equipped skin is found.
    """
    if not right_size(frame):
        return None
    hits = [m for n in LOOTBUG_HOMES if (m := A.get(n).find(frame))]
    if hits:                       # fast path: in-game crops (~0.1 s)
        return max(hits, key=lambda m: m.score)
    return wiki_lootbug(frame)     # all 14 skins, masked (~1.3 s on the Pi)


def lootbug_stored(frame: np.ndarray) -> Optional[Match]:
    """The stored-lootbug row's icon in the left-side list (the double-tap target),
    or None when no lootbug is stored (a single bug on the field has no row)."""
    if not right_size(frame):
        return None
    return A.get("lootbug_stored").find(frame)


# Left-side notification box ("Elixir Drone is now Grade 6!" etc.): a flat dark
# box (BGR 24,20,16) over the timer list, reaching x~234 and ~95 px tall. It hid
# both the stored-lootbug row and the bug on the field in keep-open mode
# (2026-11-10 00:44). The timer rows use the same fill but end at x~122, so only
# x 130-225 is checked. On 64 saved mining frames (dark space floors included)
# it matched only that box; menus share the fill, so callers check MINING first.
NOTIF_FILL = np.array([24, 20, 16])
NOTIF_STRIP = (130, 226)
NOTIF_ROWS = (250, 1090)


def notification_box(frame: np.ndarray) -> Optional[tuple[int, int]]:
    """(y_top, y_bottom) of a left-side notification box, or None."""
    if not right_size(frame):
        return None
    strip = frame[NOTIF_ROWS[0]:NOTIF_ROWS[1], NOTIF_STRIP[0]:NOTIF_STRIP[1]].astype(int)
    frac = (np.abs(strip - NOTIF_FILL).max(axis=2) <= 8).mean(axis=1)
    rows = np.nonzero(frac >= 0.6)[0] + NOTIF_ROWS[0]
    spans: list[list[int]] = []
    for y in rows:                       # merge runs split by a line of text
        if spans and y - spans[-1][1] <= 15:
            spans[-1][1] = y
        else:
            spans.append([y, y])
    for a, b in spans:
        dark = int(((rows >= a) & (rows <= b)).sum())
        if b - a >= 50 and dark >= 50:
            return int(a), int(b)
    return None


def notification_crop(frame: np.ndarray, box: tuple[int, int]) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) framing a notification box found by notification_box,
    border and the icon tab on its right included (the Elixir one: x 0-297).
    The right edge is where the fill stops: walking right from the checked
    strip, the first run of GAP columns with little fill in the box's rows."""
    GAP = 10
    a, b = box
    band = frame[a:b + 1].astype(int)
    frac = (np.abs(band - NOTIF_FILL).max(axis=2) <= 8).mean(axis=0)
    right, miss = NOTIF_STRIP[1], 0
    for x in range(NOTIF_STRIP[1], min(frame.shape[1], 480)):
        if frac[x] >= 0.25:
            right, miss = x, 0
        else:
            miss += 1
            if miss >= GAP:
                break
    # the found rows/columns stop inside the light border; on the Elixir box
    # the dark outline is 4 px above, 2 below and 4 right of them (y 616-716, x <=296)
    return (0, max(0, a - 4), min(frame.shape[1], right + 5), min(frame.shape[0], b + 3))


def founder_crate(frame: np.ndarray) -> Optional[Match]:
    """Best match of the Founder drop crate (3 sway poses), or None."""
    if not right_size(frame):
        return None
    hits = [m for n in (1, 2, 3) if (m := A.get(f"founder_crate_{n}").find(frame))]
    return max(hits, key=lambda m: m.score) if hits else None


# Cloud screen buttons. Measured HSV means: disabled V~42; enabled red H~174
# V~168; armed (after the 1st Upload press) orange H~13 V~223.
CLOUD_DOWNLOAD: Box = (470, 402, 598, 455)
CLOUD_UPLOAD: Box = (470, 655, 598, 708)
CLOUD_TIME: Box = (165, 384, 350, 406)       # cloud save play time text
CLOUD_AUTOSAVE: Box = (168, 815, 204, 851)   # "Toggle Cloud Auto-Save" checkbox


def cloud_autosave_on(frame: np.ndarray) -> bool:
    """The auto-save checkbox shows a white tick when on (326 white px vs 0)."""
    x1, y1, x2, y2 = CLOUD_AUTOSAVE
    return int((frame[y1:y2, x1:x2].min(axis=2) > 200).sum()) > 60


def cloud_button(frame: np.ndarray, box: Box) -> str:
    """'disabled' | 'enabled' | 'armed'."""
    x1, y1, x2, y2 = box
    h, sat, v = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV).reshape(-1, 3).mean(axis=0)
    if v < 90:
        return "disabled"
    if 5 <= h <= 25 and v > 195:
        return "armed"
    return "enabled"
