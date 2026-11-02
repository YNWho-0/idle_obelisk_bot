# Android (Waydroid) settings changed for the bot

Changed 2026-11-02 so nothing in Android pops up over the game or puts the
display to sleep (the user asked for "everything that could disrupt the bot
off"). Trigger: a Calendar (Etar) reminder popped up over the game.

`A` below = `adb -s 192.168.240.112:5555 shell`.

## Global settings

| Setting | Original | Changed to | Restore with |
|---|---|---|---|
| Heads-up (pop-up) notifications | on (`global heads_up_notifications_enabled 1`) | off (0) | `A settings put global heads_up_notifications_enabled 1` |
| Do Not Disturb | off (`global zen_mode 0`) | total silence | `A cmd notification set_dnd off` |
| Screen timeout | 60 s (`system screen_off_timeout 60000`) | never (2147483647) | `A settings put system screen_off_timeout 60000` |
| Stay awake while plugged in | off (`global stay_on_while_plugged_in 0`) | always (7) | `A settings put global stay_on_while_plugged_in 0` |

Already fine, left alone: lock screen disabled (`locksettings get-disabled`
= true), auto-rotate off (`system accelerometer_rotation 0`).

## Notification permission revoked

All of these had `android.permission.POST_NOTIFICATIONS` granted. Restore
one with `A pm grant <package> android.permission.POST_NOTIFICATIONS`.

- `org.lineageos.etar`
- `com.checkbox.minershminer`
- `com.android.vending`
- `com.google.android.gms`
- `com.google.android.gsf`
- `com.google.android.googlequicksearchbox`
- `com.google.android.apps.messaging`
- `com.google.android.dialer`
- `com.android.deskclock`
- `com.stevesoltys.seedvault`
- `com.google.android.projection.gearhead`
- `com.google.android.as`
- `com.google.android.apps.restore`

Could not be revoked (system-fixed): `org.lineageos.waydroidupdater`,
`com.android.providers.downloads`, `com.android.mms.service`. For the last
one `appops set com.android.mms.service POST_NOTIFICATION ignore` worked
(restore: `... POST_NOTIFICATION allow`). These are covered by heads-up off
+ Do Not Disturb anyway: their notifications can't appear over the game.

Note: revoking the permission makes Android stop that app, so only do this
while the bot is between sessions.
