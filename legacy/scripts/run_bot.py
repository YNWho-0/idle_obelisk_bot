#!/usr/bin/env python3
"""
Entry point.

Starts the automation loop, and -- if dashboard.enabled is true in
config.yaml -- a Flask web dashboard in a background thread so you can
watch/pause/trigger workflows from a browser on your LAN.

Usage:
    python3 scripts/run_bot.py [--config config.yaml] [--templates templates] [--log-dir logs]
"""
import argparse
import os
import sys
import queue
import threading

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.logger import setup_logging
from bot.log_stream_handler import StateLogHandler
from bot.state import StateStore
from bot.scheduler import Bot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--templates", default="templates")
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()

    logger = setup_logging(log_dir=args.log_dir)

    with open(args.config) as f:
        config = yaml.safe_load(f)

    state = StateStore()
    logger.addHandler(StateLogHandler(state))

    workflow_queue: "queue.Queue" = queue.Queue()
    bot = Bot(config=config, template_dir=args.templates, state=state, workflow_queue=workflow_queue)

    dashboard_cfg = config.get("dashboard", {})
    if dashboard_cfg.get("enabled", False):
        from web.app import run_dashboard  # imported lazily: Flask only needed if dashboard is on

        thread = threading.Thread(target=run_dashboard, args=(state, bot, dashboard_cfg), daemon=True)
        thread.start()

    bot.run_forever()


if __name__ == "__main__":
    main()
