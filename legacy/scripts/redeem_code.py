#!/usr/bin/env python3
"""
Standalone code redemption helper.

The redeem flow is game-specific, so this script walks a *configurable*
sequence of steps rather than hardcoding taps. Define the sequence once
in config.yaml under `redeem_code.steps` (see the comment there), then run:

    python3 scripts/redeem_code.py SUMMER2026
"""
import argparse
import os
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.adb_client import AdbClient
from bot.matcher import load_templates, find_template


def run_redeem(code: str, config_path: str = "config.yaml", template_dir: str = "templates"):
    with open(config_path) as f:
        config = yaml.safe_load(f)

    steps = config.get("redeem_code", {}).get("steps", [])
    if not steps:
        raise SystemExit("no redeem_code.steps configured in config.yaml")

    adb = AdbClient(serial=config.get("adb", {}).get("serial"))
    grayscale = config.get("matching", {}).get("grayscale", True)
    threshold = config.get("matching", {}).get("default_threshold", 0.85)
    templates = load_templates(template_dir, grayscale=grayscale)

    for i, step in enumerate(steps, 1):
        if "tap_template" in step:
            name = step["tap_template"]
            tmpl = templates.get(name)
            if tmpl is None:
                raise SystemExit(f"step {i}: missing template '{name}'")
            screen = adb.screenshot()
            match = find_template(screen, tmpl, threshold=step.get("threshold", threshold), grayscale=grayscale)
            if not match.found:
                raise SystemExit(f"step {i}: could not find '{name}' on screen (confidence={match.confidence:.3f})")
            adb.tap(*match.center)
            print(f"step {i}: tapped {name} at {match.center}")

        elif "type_text" in step:
            text = step["type_text"].replace("{code}", code)
            adb.input_text(text)
            print(f"step {i}: typed text")

        elif "wait" in step:
            time.sleep(float(step["wait"]))
            print(f"step {i}: waited {step['wait']}s")

        else:
            raise SystemExit(f"step {i}: unrecognized step {step}")

        time.sleep(0.5)  # let the UI settle between steps

    print(f"code '{code}' redemption sequence complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("code")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--templates", default="templates")
    args = parser.parse_args()
    run_redeem(args.code, args.config, args.templates)
