# Idle Obelisk Miner bot (v2)

Runs on a Raspberry Pi 5 against the Android game in Waydroid, via ADB +
OpenCV. It never presses anything that costs gems.

| Job | What it does | Verified by |
|---|---|---|
| **founder** | Presses the "FOUNDER" parachute crate | crate disappears |
| **lootbug** | Presses a lootbug on the field (one press loots all), then closes the Lootbug popup (a gem shop) with its top-right X only | popup opens, then mining screen returns |
| **freebie** | Store → (scrolls to) Freebie Pack → Claim (only if green) → close | button turns dimmed red |
| **cloud** | Cloud screen: re-disables in-game Cloud Auto-Save, then uploads the save (two presses) unless another device has uploaded since | cloud play time changes |

## Sessions: the game stays in the background

With the game's "Consume Buff Timers and Items Buffs offline" option off,
buffs only tick while the game is in front. In the background (HOME) the game
uses ~0% CPU and counts as offline; freebies and lootbugs bank up meanwhile,
and the Founder crate drops on its own hourly cycle and waits on the field.

So the bot keeps the game in the background and brings it to the front for
**one short session every 50 minutes** (< the 60 min drop cycle, so no drop
is ever missed):

1. settle: "Offline Gains" popups → Confirm, then Close (never the checkboxes)
2. cloud check (see below)
3. founder crate, lootbugs, freebies (whatever is there)
4. cloud upload, then HOME

A session takes ~30 s. `python -m obelisk run --always-open` keeps the game
open instead (burns buff timers).

### Keep-open mode (dashboard)

To make use of buffs, the dashboard's **Keep game open** card holds the game on
screen for a chosen time: set the slider (15 min to 12 h), press the button.
Between sessions that starts one at once. Founder crates, lootbugs and freebies
are still claimed, the watchdog brings the game back if it leaves the front, and
the mode carries on after a bot restart. Press the button again to change the
time, or Stop now to end early. Pausing the bot ends it too.

There are no cloud uploads during the mode, since the game only fetches cloud
info at startup. When it ends the game goes to the background, and 30 s later a
normal session restarts it, checks the cloud and uploads. A phone save with more
play time uploaded in the meantime wins as usual, so the Pi's progress from the
mode is then lost. A cloud conflict cancels the mode.

## Cloud saves (policy: never overwrite another device's progress)

After each upload the bot remembers the cloud play time. If the cloud later
shows something else, another device (e.g. the iPhone) has uploaded: the bot
marks a **conflict**, skips sessions and alerts the phone until resolved:

```bash
venv/bin/python -m obelisk cloud --status
venv/bin/python -m obelisk cloud --keep-pi   # the Pi's save wins at the next session
```

To keep the phone's save instead, play long enough on the phone that its play
time passes the Pi's, upload, and download it on the Pi (by hand for now; the
game only enables Download when the cloud save has more play time). Loading a
save restores that save's settings, including Cloud Auto-Save, which the bot
turns off again.

**Game versions:** if the cloud save has more play time but comes from a
different game version, the game greys out Download. The bot then alerts "update
the game" and skips sessions. Update the older device (Pi: Play Store); the next
session loads the cloud save by itself. `--keep-pi` refuses to run in this state,
because the Pi could later overwrite the newer save.

**Stop the bot before pressing anything on the Pi yourself**
(it can't tell your presses from its own).

## Phone alerts (ntfy)

The ntfy app subscribed to the topic in `notify.conf` (gitignored; keep it
private) gets: gem guard / failure stops (urgent), cloud conflicts and crashes
(high), stuck screens and session timeouts, and the nightly summary (quiet
unless it needs a look). Each kind is sent at most once per 6 h, except
outages (`obelisk/outage.py`): one alert when the bot goes down, reminders
at 30 min and 1 h, a STOPPED alert at 2 h, and "OK again" after the next
good session.
`venv/bin/python -m obelisk notify-test` sends a test alert.

## How it works

* **Fixed resolution**: Android renders at 720x1280 (`adb shell wm size 720x1280`)
  inside the 1024x732 Waydroid window; the bot re-applies it if lost.
  Since 2026-11-01 the game uses its native layout for that size ("layout v2",
  no black bars); old-layout material is in `screens/v1layout/` and `legacy/`.
* **Raw capture**: `adb exec-out screencap` (no PNG), decoded with numpy.
* **Held presses**: the game ignores `input tap`; every press is
  `input swipe X Y X Y 150`.
* **Screens = small anchors** (`obelisk/anchors/*.png`, cut from `screens/` by
  `scripts/make_anchors.py`). Every press targets an anchor matched on a fresh
  frame and waits for the expected result; anything unexpected aborts and
  saves a screenshot to `logs/failures/`.
* **One scheduler** (`obelisk/scheduler.py`): popups are interrupts, then the
  first due job runs (founder → lootbug → freebie), only from the mining
  screen; a watchdog relaunches the game if it isn't in front during a session.
* **Gem guard**: the gem counter is read before and after every job (strict
  digit matching; unknown digits → skip, never guess); a drop stops the bot.
* **Lootbug skins**: in-game crops plus masked templates of all 14 skins
  (7 regular + 7 golden) from a wiki screenshot (`scripts/make_wiki_lootbugs.py`).

## Running

```bash
systemctl --user status obelisk-bot          # user service (lingering on)
systemctl --user stop obelisk-bot            # pause, e.g. while playing on the Pi
systemctl --user start obelisk-bot
tail -f logs/obelisk.log                     # every press is logged
venv/bin/python -m obelisk summary           # today so far; nightly reports in logs/daily/
```

Jobs wait up to 3 s for the mining screen (the game's own toasts hide it
briefly) before counting a failure. A bot that stopped itself (3 failures in
a row within one session, a failure in 3 sessions in a row, or the gem
guard) exits with code 3
and is NOT restarted; check `logs/failures/` first. Crashes (adb, Waydroid
down) retry every 60 s; after 2 h of failing it also exits with code 3.
If screencap hangs, the bot first screenshots the Pi's own desktop (`grim`)
to wake it: with no monitor attached, the desktop once stopped showing the
game for 3 h until a VNC viewer connected. If the bot still gave up, connect
with VNC, then `systemctl --user restart obelisk-bot`.
Every crash/outage alert carries a health picture (`obelisk/health.py`,
also `venv/bin/python -m obelisk health` or the dashboard's "Check health
now"): a banner saying NO REBOOT NEEDED / RESTART WAYDROID + BOT / REBOOT
THE PI, five checks (Pi desktop, Waydroid, adb, Android screen, game), the
Android screen and the Pi desktop. Latest in `logs/health.png`; alert copies
in `logs/failures/*_health_*.png`. `obelisk-health.timer` runs the
check every 50 min and alerts only if something is unhealthy (install:
`systemctl --user link` both `systemd/obelisk-health.*` files, then
`systemctl --user enable --now obelisk-health.timer`).
The dashboard's Restart card does the same from a phone: restart the bot,
restart Waydroid + game + bot (`scripts/restart_all.sh`, progress in
`logs/restart.log`), or reboot the Pi.

One-off commands (stop the service first):

```bash
venv/bin/python -m obelisk peek              # one live capture, no input
venv/bin/python -m obelisk identify screens/*.png
venv/bin/python -m obelisk freebie|lootbug|founder [--dry-run]
venv/bin/python -I tests/test_offline.py     # offline tests, no device needed
```

## Game settings

The bot setup changed 12 in-game Options (noise off, Lootbug gem confirmation
back on) plus Cloud Auto-Save off. Originals are in `docs/game_settings.md`;
`venv/bin/python -I scripts/set_options.py --check | --restore` (start with the
Options screen open).

## Layout

```
obelisk/      bot package (device, vision, screens, jobs, scheduler, session,
              gems, playtime, notify, summary, logfile, CLI)
obelisk/anchors/   templates and glyph samples (generated; see scripts/)
screens/      layout-v2 reference screenshots (anchors and tests are cut from them)
scripts/      make_anchors, make_wiki_lootbugs, make_gem_digits,
              make_playtime_glyphs, set_options
tests/        offline tests against screens/
systemd/      obelisk-bot.service, obelisk-summary.{service,timer} (user units)
docs/         game_settings.md
reference/    wiki screenshot of lootbug skins
legacy/       the August 2026 bot, layout-v1 tests and anchors
```
