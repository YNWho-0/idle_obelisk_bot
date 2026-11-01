# Idle Obelisk Miner bot (v2)

Runs on a Raspberry Pi 5 against the Android game in Waydroid, via ADB +
OpenCV. It does three things and never presses anything that costs gems:

| Job | What it does | Verified by |
|---|---|---|
| **founder** | Presses the falling "FOUNDER" parachute crate | crate disappears |
| **lootbug** | Presses a lootbug on the field (one press loots all), then closes the Lootbug popup (a gem shop) with its top-right X only | popup opens, then mining screen returns |
| **freebie** | Store → Freebie Pack → Claim (only if green) → close | button turns dimmed red |

## Sessions (default): the game stays in the background

With the game's "Consume Buff Timers and Items Buffs offline" option off,
buffs only tick while the game is in front. Backgrounded (HOME) the game uses
~0% CPU and counts as offline; freebies and lootbugs bank up meanwhile. So the
bot keeps the game in the background and brings it to the front for one short
session per hour, 90 s before the learned Founder drop (hourly, e.g. :23):
settle (Offline Gains popups: Confirm / Close only), founder crate, lootbugs,
freebies, then HOME. Typical session: under a minute without a drop, a few
minutes with one. `python -m obelisk run --always-open` keeps the game open.

## How it works

* **Fixed resolution**: Android renders at 720x1280 (`adb shell wm size 720x1280`)
  inside the 1024x732 Waydroid window. The bot refuses to run at any other size.
* **Raw capture**: `adb exec-out screencap` (no PNG), decoded with numpy.
* **Held presses**: the game ignores `input tap`; every press is
  `input swipe X Y X Y 150`.
* **Screens = small anchors** (`obelisk/anchors/*.png`, cut from `screens/` by
  `scripts/make_anchors.py`). Every press targets an anchor matched on a fresh
  frame, and every press waits for the expected result; anything unexpected
  aborts and saves a screenshot to `logs/failures/`.
* **One scheduler** (`obelisk/scheduler.py`): popups are interrupts (the Lootbug
  popup is always closed via its X), then the first due job runs, in priority
  order founder → lootbug → freebie. It only acts on the mining screen, so it
  waits while you're in a menu.
* **Lootbug skins**: in-game crops for Default/Halloween plus masked templates for
  all 14 skins (7 regular + 7 golden) built from a wiki screenshot by
  `scripts/make_wiki_lootbugs.py`.

## Running

```bash
systemctl --user status obelisk-bot          # it's a user service (lingering on)
systemctl --user stop obelisk-bot            # pause while playing by hand
systemctl --user start obelisk-bot
tail -f logs/obelisk.log                     # every press is logged
```

A bot that stopped itself after 3 failed jobs exits with code 3 and is NOT
restarted; check `logs/failures/` first. Crashes (adb, Waydroid down) retry
every 60 s.

One-off commands (stop the service first):

```bash
venv/bin/python -m obelisk peek              # one live capture, no input
venv/bin/python -m obelisk identify screens/*.png
venv/bin/python -m obelisk freebie|lootbug|founder [--dry-run]
venv/bin/python -I tests/test_offline.py     # offline tests, no device needed
```

## Game settings

The bot setup changed 12 in-game Options (noise off, Lootbug gem confirmation
back on). Originals are in `docs/game_settings.md`;
`venv/bin/python -I scripts/set_options.py --restore` puts them back (start
with the Options screen open).

## Layout

```
obelisk/      bot package (device, vision, screens, jobs, scheduler, CLI)
obelisk/anchors/   templates (generated; see scripts/)
screens/      reference screenshots the anchors and tests are built from
scripts/      make_anchors.py, make_wiki_lootbugs.py, set_options.py
tests/        offline tests against screens/
systemd/      obelisk-bot.service (user unit)
docs/         game_settings.md
reference/    wiki screenshot of lootbug skins
legacy/       the August 2026 bot (never sent input to the game; kept for reference)
```
