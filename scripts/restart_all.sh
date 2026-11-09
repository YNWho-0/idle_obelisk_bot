#!/bin/sh
# Restart the Waydroid session, the game and the bot (dashboard "Restart
# Waydroid + bot" button). The dashboard runs this as its own transient unit
# (systemd-run --user --unit=obelisk-restart) so it isn't killed half-way.
# Usage: restart_all.sh [bot]     "bot" = only restart the bot service.
# Progress goes to logs/restart.log (shown on the dashboard).

BOT=$(cd "$(dirname "$0")/.." && pwd)     # the repo, wherever it was cloned
LOG="$BOT/logs/restart.log"
ADB_SERIAL=$(cd "$BOT" && venv/bin/python -c 'from obelisk import ADB_SERIAL; print(ADB_SERIAL)')
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

if [ "$1" = "bot" ]; then
    log "restart bot: requested from dashboard"
    systemctl --user restart obelisk-bot && log "restart bot: done" || log "restart bot: FAILED"
    exit 0
fi

android_ip() {
    waydroid status 2>/dev/null | awk -F'\t' '/IP address/ {print $2}'
}

launch() {          # own unit: processes left in this (transient) unit die with it
    systemd-run --user --collect --quiet --unit="waydroid-session-$(date +%s)" \
        waydroid app launch com.checkbox.minershminer >/dev/null 2>&1
}

wait_for_ip() {     # $1 = seconds
    i=0
    while [ "$i" -lt "$1" ]; do
        ip=$(android_ip)
        if [ -n "$ip" ] && [ "$ip" != "UNKNOWN" ]; then
            log "restart all: Android IP $ip after ${i}s"
            return 0
        fi
        sleep 5
        i=$((i + 5))
    done
    return 1
}

log "restart all: requested from dashboard; stopping the bot"
systemctl --user stop obelisk-bot
log "restart all: stopping the Waydroid session"
waydroid session stop >/dev/null 2>&1
sleep 5
log "restart all: starting Waydroid + game"
launch
if ! wait_for_ip 120; then
    log "restart all: no Android IP after 120s; trying the session once more"
    waydroid session stop >/dev/null 2>&1
    sleep 5
    launch
    wait_for_ip 120 || log "restart all: still no Android IP (starting the bot anyway; it retries)"
fi
sleep 15            # let Android finish booting before adb + the bot
adb connect "$ADB_SERIAL" >/dev/null 2>&1
log "restart all: starting the bot"
systemctl --user start obelisk-bot && log "restart all: done" || log "restart all: bot start FAILED"
