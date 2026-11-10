# Idle Obelisk Miner bot (v2)

Runs on a Raspberry Pi 5 against the Android game in Waydroid, via ADB +
OpenCV. It never presses anything that costs gems.

> **Use at your own risk.** This is an unofficial hobby project, not made or
> endorsed by the game's developer. Automating a game may break its terms of
> service. It was built and tested on one setup (Raspberry Pi 5, game v2.2.51);
> a game update can move buttons and stop it working until the anchors are
> re-cut. See [Install](#install) to set it up yourself.

| Job | What it does | Verified by |
|---|---|---|
| **founder** | Presses the "FOUNDER" parachute crate | crate disappears |
| **lootbug** | Double-taps the stored-lootbug row in the left-side list (Loot-All, which takes the bug on the field too); a lone bug with nothing stored has no row, so it presses that bug instead. Then closes the Lootbug popup (a gem shop) with its top-right X only | popup opens, then mining screen returns |
| **freebie** | Store → (scrolls to) Freebie Pack → Claim (only if green) → close | button turns dimmed red |
| **cloud** | Cloud screen: re-disables in-game Cloud Auto-Save, then uploads the save (two presses) unless another device has uploaded since | cloud play time changes |

## Install

What you need: a Raspberry Pi 5 (any arm64/x86 Linux box that runs Waydroid
should work, untested) with a Wayland desktop, Python 3.11+, and the game on a
Google account so it can sync with your phone.

1. **Waydroid with Google Play.** Install Waydroid
   ([docs](https://docs.waydro.id/)) and initialise it with the GAPPS image
   (`sudo waydroid init -s GAPPS`). Start it, sign in to Play, install
   Idle Obelisk Miner, open it once and log in to your Checkbox/Google account.
2. **adb.** `sudo apt install adb`. Find Android's IP with `waydroid status`
   and connect: `adb connect <ip>:5555`. If the IP isn't `192.168.240.112`, set
   it for the bot: `export OBELISK_ADB_SERIAL=<ip>:5555`, and add
   `Environment=OBELISK_ADB_SERIAL=<ip>:5555` to each unit in `systemd/`.
3. **Resolution.** The bot only understands the 720x1280 layout:
   `adb shell wm size 720x1280` (the bot re-applies it if it's lost).
4. **The code.** Clone into your home folder (the systemd units and the
   autostart entry expect `~/idle_obelisk_bot`):

   ```bash
   sudo apt install grim               # Pi desktop screenshots (health check)
   git clone https://github.com/YNWho-0/idle_obelisk_bot ~/idle_obelisk_bot
   cd ~/idle_obelisk_bot
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   venv/bin/python -I tests/test_offline.py     # should pass, no device needed
   venv/bin/python -m obelisk peek              # one live capture: is adb working?
   ```
5. **In-game settings.** In the game: grid menu -> Options. Then run
   `venv/bin/python -I scripts/set_options.py`, which turns off the visual
   effects the bot can't see through and turns the Lootbug gem confirmation
   back on (a safety net). Your current values are not saved, so note them
   first if you want them back (`docs/game_settings.md` lists the rows). Also turn
   off **Toggle Cloud Auto-Save** on the Cloud screen; the bot uploads itself.
   Turn off "Consume Buff Timers and Items Buffs offline" so buffs don't run
   down while the game is in the background.
6. **Android quiet.** Optional but recommended: turn off things that pop up
   over the game or sleep the screen (heads-up notifications, screen timeout).
   `docs/android_settings.md` lists the exact `adb shell settings` commands used.
7. **Phone alerts** (optional). Install the [ntfy](https://ntfy.sh) app,
   subscribe to a long random topic name, and put it in `notify.conf`:

   ```bash
   printf 'topic=%s\n' "<your-random-topic>" > notify.conf && chmod 600 notify.conf
   venv/bin/python -m obelisk notify-test
   ```

   Anyone who knows the topic can read your alerts, so make it hard to guess.
8. **Run it as services.**

   ```bash
   sudo loginctl enable-linger "$USER"          # keep running after logout
   for u in systemd/*.service systemd/*.timer; do systemctl --user link "$PWD/$u"; done
   systemctl --user enable --now obelisk-bot obelisk-dashboard
   systemctl --user enable --now obelisk-summary.timer obelisk-health.timer obelisk-bootcheck.timer
   cp systemd/waydroid-obelisk.desktop ~/.config/autostart/   # start Waydroid + game at login
   ```

   The dashboard listens on port 8765. Its first start writes a random token to
   `dashboard.conf`; open `http://<pi-ip>:8765/?t=<token>` once on your phone.
   Add `url=http://<pi-ip>:8765/` to `dashboard.conf` to open it when you tap an
   alert. Keep port 8765 off the internet.

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
are still claimed (freebies every 20 min: at game speed 2.10x the bank of 19
fills in ~63 min, and at once if the Store icon shows the bank full), the
watchdog brings the game back if it leaves the front, and the mode carries on
after a bot restart. Press the button again to change the
time, or Stop now to end early. Pausing the bot ends it too.

There are no cloud uploads during the mode, since the game only fetches cloud
info at startup. When it ends the game goes to the background, and 30 s later a
normal session restarts it, checks the cloud and uploads. A phone save with more
play time uploaded in the meantime wins as usual, so the Pi's progress from the
mode is then lost. A cloud conflict cancels the mode.

Every 5 min the mode keeps a full picture in `logs/keep_open/` (newest 200),
named `_bug`/`_nobug` by whether the lootbug finder sees a bug in it: lootbugs
stopped being found 33 min into a 6 h run on 2026-11-06.

If the game sits off the mining screen for 5 min during the mode, the bot restarts
it. After 3 restarts in a row that don't help, it ends the mode to save buffs and
alerts the phone.

## Cloud saves (policy: never overwrite another device's progress)

After each upload the bot remembers the cloud play time. If the cloud later
shows something else, another device (e.g. the iPhone) has uploaded: the bot
marks a **conflict**, skips sessions and alerts the phone until resolved:

```bash
venv/bin/python -m obelisk cloud --status
venv/bin/python -m obelisk cloud --keep-pi   # the Pi's save wins at the next session
```

The dashboard's Cloud row has the same **Keep the Pi save** button; it also runs a
session right away. An open conflict alerts again every 6 h.

To keep the phone's save instead, play long enough on the phone that its play
time passes the Pi's, upload, and download it on the Pi (by hand for now; the
game only enables Download when the cloud save has more play time). Loading a
save restores that save's settings, including Cloud Auto-Save, which the bot
turns off again.

**No record of the last upload** (e.g. `logs/state.json` lost): the bot can't run
this check, so it reports a conflict rather than risk overwriting anything.

**Cloud info not shown yet:** the bot waits up to 10 s for a readable, non-zero
cloud play time (the game can show 00d 00h 00m 00s before it has fetched the
cloud info, and toasts can cover the row). If it never shows, that session
doesn't upload; it is never treated as a conflict.

**Slow uploads:** an upload not confirmed within 45 s counts as a failure, but it
may still arrive. If the next session finds a cloud time within 2 min of the Pi's
play time at that attempt, it treats it as the Pi's own upload, not a conflict.

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
* **Gem guard**: the gem counter is read before and after every job and at the
  end of the session, so popups and cloud steps are covered too (strict digit
  matching; it retries for 6 s, then skips, never guesses); a drop stops the bot.
  A new session starts a new baseline, so spending on the phone doesn't count.
* **Stopping**: when the bot stops itself the game goes to the background first,
  so it doesn't burn buffs. A force-stop always follows HOME and a 30 s wait, since
  the game saves on HOME.
* **Game updates**: each session compares the installed game version with the last
  one seen and alerts the phone if it changed, since updates can move buttons.
* **Founder diagnostics**: a session with no crate saves its last frame to
  `logs/founder_empty/` (newest 100), to find crates the bot doesn't recognise.
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
game for 3 h until a VNC viewer connected. If screencap still hangs for 5 min
(Android's screen frozen), the bot restarts Waydroid and itself, as the
dashboard's Restart Waydroid + bot does, and alerts the phone; at most once
per 6 h, after which the normal outage alerts carry on. If the bot still gave
up, connect with VNC, then `systemctl --user restart obelisk-bot`.
Every crash/outage alert carries a health picture (`obelisk/health.py`,
also `venv/bin/python -m obelisk health` or the dashboard's "Check health
now"): a banner saying NO REBOOT NEEDED / RESTART WAYDROID + BOT / REBOOT
THE PI, the checks (Pi desktop, Waydroid, adb, Android screen, game), the
Android screen and the Pi desktop. A sixth check covers the bot itself: the
periodic check alerts if its service is down or there has been no session for 2 h
(unless paused), including after you stop it by hand. Latest in `logs/health.png`; alert copies
in `logs/failures/*_health_*.png`. `obelisk-health.timer` runs the
check every 50 min and alerts only if something is unhealthy (install:
`systemctl --user link` both `systemd/obelisk-health.*` files, then
`systemctl --user enable --now obelisk-health.timer`).
The dashboard updates itself every 5 s while it's open. Its Restart card does the
same from a phone: restart the bot,
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

The bot setup changed 13 in-game Options (noise off, Lootbug gem confirmation
back on, timers showing true time) plus Cloud Auto-Save off. Originals are in
`docs/game_settings.md`; `venv/bin/python -I scripts/set_options.py --check |
--restore` (start with the Options screen open).

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
