# Templates

Drop cropped PNGs of UI elements here (buttons, icons, popup close-X, etc).

Easiest way to make one:

```
python3 scripts/capture_template.py --name collect_button
```

This grabs a live screenshot from the game, opens a crop window, and saves
your selection here as `collect_button.png`. Then reference that filename
in a `template:` field under `tasks:` in `config.yaml`.

Tips for reliable templates:
- Crop tightly around just the element, minimal surrounding background.
- Avoid cropping any part that has animated/particle effects on top of it.
- If a button has a numeric badge or changing label, crop the static part
  (icon/border) rather than the whole button.
- Recapture whenever the game updates its art or you notice a task's
  match confidence dropping (check the logs).
