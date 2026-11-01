#!/bin/sh
# Run at desktop login (via ~/.config/autostart/waydroid-obelisk.desktop).
# Starts the Waydroid session + game for the obelisk bot. On 2026-11-01 a
# reboot left Android without its IPv4 address (DHCP race), so adb and the
# bot couldn't reach it; if that happens, restart the session once.

LOG="$HOME/idle_obelisk_bot/logs/autostart.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

android_ip() {
    waydroid status 2>/dev/null | awk -F'\t' '/IP address/ {print $2}'
}

launch() {
    setsid waydroid app launch com.checkbox.minershminer >/dev/null 2>&1 &
}

wait_for_ip() {     # $1 = seconds
    i=0
    while [ "$i" -lt "$1" ]; do
        ip=$(android_ip)
        if [ -n "$ip" ] && [ "$ip" != "UNKNOWN" ]; then
            log "Android IP $ip after ${i}s"
            return 0
        fi
        sleep 5
        i=$((i + 5))
    done
    return 1
}

sleep 20
log "login: launching Waydroid + game"
launch
if wait_for_ip 120; then
    exit 0
fi
log "no Android IP after 120s: restarting the Waydroid session once"
waydroid session stop >/dev/null 2>&1
sleep 5
launch
if wait_for_ip 120; then
    exit 0
fi
log "still no Android IP; giving up (the bot's boot check will alert)"
exit 1
