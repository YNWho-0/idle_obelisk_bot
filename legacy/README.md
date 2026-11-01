# Idle Obelisk Miner Bot

UI-level automation for Idle Obelisk Miner running under Waydroid on a
Raspberry Pi 5, built on ADB (capture + input) and OpenCV (template
matching) — no PyAutoGUI, no fixed-coordinate clicking.

## How it works

- **Capture:** `adb exec-out screencap -p` streams a screenshot straight
  into memory every polling cycle. No PyAutoGUI, no screen-recording of
  your physical display.
- **Detection:** OpenCV template matching finds known buttons/icons/popups
  by comparing small reference images (`templates/*.png`) against the
  current screenshot.
- **Action:** `adb shell input tap/swipe/text` sends input directly into
  the Android container.
- **Structure:** every cycle, all known UI states are checked in priority
  order (blocking popups first) and the first match is acted on, then the
  loop restarts and re-evaluates from scratch. This makes it tolerant of
  ads/popups/interruptions showing up unpredictably, rather than being a
  brittle linear "click A, then B" script.
- **All game-specific knowledge lives in `config.yaml` + `templates/`.**
  The Python code is generic; when the game's UI changes you edit config
  and recapture an image, you don't touch `bot/*.py`.
- **Humanization:** tap position and timing are randomized within bounds
  you set, instead of always landing on the exact same pixel on a fixed
  cadence.
- **Workflows:** explicit multi-step sequences (tap, wait, tap, type) for
  things like auto-prestige, separate from the reactive per-cycle tasks.
- **Web dashboard:** optional Flask UI showing live status, last
  screenshot, and logs, with pause/resume and "run workflow now" buttons.

## 1. One-time setup

### On the Pi, install dependencies

```bash
sudo apt install adb
cd idle_obelisk_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` installs `opencv-python-headless` by default (lighter,
no GUI deps — good for the bot running as a background service). If you
also want to use `scripts/capture_template.py` (which opens an interactive
crop window), swap it for the full GUI build:

```bash
pip uninstall opencv-python-headless
pip install opencv-python
```

### Connect ADB to Waydroid

Waydroid exposes an Android IP on your Pi's network (you noted
`192.168.240.112`). Enable ADB over TCP on the guest once:

```bash
waydroid shell
setprop service.adb.tcp.port 5555
stop adbd && start adbd
exit

adb connect 192.168.240.112:5555
adb devices    # should show it as "device"
```

Set `adb.serial` and `adb.connect_host` in `config.yaml` to match. The bot
also calls `adb connect` itself on startup, so this manual step is mainly
to confirm it works before automating.

### Find the game's package name

```bash
adb shell pm list packages | grep -i obelisk
```

Put the result in `config.yaml` under `adb.package` (only needed for the
"restart_game" recovery option).

### Check whether the game exposes a real view hierarchy

Worth doing once, since it changes how reliable detection can be:

```bash
adb shell uiautomator dump
adb pull /sdcard/window_dump.xml
```

If this shows real buttons with `resource-id`/`text` attributes, those
particular elements can be found more robustly than by image matching
(happy to add a UIAutomator-based task type if this applies to you). If it
just shows one big `SurfaceView`/`GLSurfaceView`, that confirms the game
draws its own UI internally and template matching is the way to go — which
is what this project uses.

## 2. Capture template images

With the game open on the screen you want to automate:

```bash
python3 scripts/capture_template.py --name collect_button
python3 scripts/capture_template.py --name popup_close_x
python3 scripts/capture_template.py --name claim_button
```

Each command opens a crop window on a live screenshot — drag a tight box
around the element and press Enter. Repeat for every UI state you want
handled. See `templates/README.md` for cropping tips.

## 3. Configure tasks

Edit `config.yaml`: each entry under `tasks:` names a template, an action
(`tap` / `swipe` / `skip`), a priority (higher = checked first — keep
popups/dialogs highest), and a cooldown so it doesn't spam-tap the same
button every single cycle.

## 4. Run it

```bash
python3 scripts/run_bot.py
```

Watch `logs/bot.log` (or the console) to confirm it's recognizing states
and tapping correctly. Tune `threshold` per-task in `config.yaml` if a
button isn't being detected (lower it) or something else is being
mis-detected as it (raise it).

## 4a. Humanization (tap/timing jitter)

`humanization:` in `config.yaml` randomizes exactly where within a
matched button it taps (`tap_jitter_px`, always clamped so it can never
miss the button) and how long it waits between cycles
(`timing_jitter_fraction`), plus an occasional extra pause
(`micro_pause_chance`). It's on by default; set `enabled: false` to get
perfectly deterministic dead-center taps on a fixed cadence instead.

## 4b. Workflows (multi-step sequences)

Tasks in `tasks:` are reactive — "whenever X appears, tap it." Workflows
in `workflows:` are explicit multi-step recipes — tap this, wait, tap
that, type this — for things like auto-prestige or navigating a menu.
Each has a `trigger`:

- `manual` — only runs when you trigger it from the dashboard's "Run
  now" button (or `bot.request_workflow("name")` in code).
- `interval` — runs automatically every `interval_seconds`, starting the
  countdown from bot startup (it won't fire immediately on restart).

Step types: `tap_template`, `type_text`, `wait`, `press_back`, `swipe`.
See the `redeem_code` and `auto_prestige` examples already in
`config.yaml`.

## 4c. Web dashboard

If `dashboard.enabled: true`, starting the bot also starts a small Flask
dashboard (default `http://127.0.0.1:6001`) showing live status, the
last screenshot, a live log stream, task-trigger counts, pause/resume
buttons, and "Run now" buttons for each workflow.

```
python3 scripts/run_bot.py
# then open http://<pi-ip>:6001 in a browser
```

**Security note:** there's no login screen. `host: "127.0.0.1"` (the
default) means it's only reachable from the Pi itself. If you set
`host: "0.0.0.0"` to check it from your phone on the same WiFi, also set
`dashboard.token` to a random string — otherwise anything on your
network can pause the bot or trigger workflows. With a token set, open
`http://<pi-ip>:6001/?token=<your-token>`.

## 5. Redeem a code on demand

Fill in `redeem_code.steps` in `config.yaml` once (the tap path to your
game's redeem screen), then:

```bash
python3 scripts/redeem_code.py SUMMER2026
```

## 6. Run unattended via systemd

```bash
sudo cp -r idle_obelisk_bot /home/pi/
sudo cp systemd/idle-obelisk-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now idle-obelisk-bot
sudo systemctl status idle-obelisk-bot
journalctl -u idle-obelisk-bot -f
```

The unit runs at low CPU/IO priority (`Nice=10`, `IOSchedulingClass=idle`)
so it doesn't compete with anything else on the Pi, and restarts itself on
failure.

## Tuning resource usage further

- `capture.interval_idle_seconds` / `interval_active_seconds` in
  `config.yaml` are the main lever — idle games rarely need sub-second
  polling. 10–20s idle / 1–2s active is a reasonable starting point.
- Keep `opencv-python-headless` installed for the service (no GUI deps
  loaded).
- If matching ever gets CPU-heavy (many templates, or a busy screen),
  narrow individual tasks with a `region: [x, y, w, h]` so OpenCV only
  searches part of the screen instead of the full frame.

## When the game's UI changes

1. Recapture the affected template(s) with `scripts/capture_template.py`.
2. If match confidence in the logs looks consistently low for a task,
   nudge its `threshold` down slightly, or recapture a tighter crop.
3. No code changes needed for ordinary button/icon changes — it's all in
   `config.yaml` and `templates/`.
