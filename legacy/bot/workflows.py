"""
Multi-step scripted automations ("workflows"), distinct from the
per-cycle reactive `tasks` in tasks.py.

A task fires reactively whenever its template appears on screen, and
does one thing (usually one tap). A workflow is an explicit ordered
sequence of steps -- tap this, wait, tap that, type this -- run either
on a timer (trigger: interval) or on demand (trigger: manual, e.g. from
the "Run now" button in the web dashboard). Use workflows for anything
that's a fixed multi-step recipe: auto-prestige, redeeming a code,
navigating menus to toggle a setting, etc.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any

from .adb_client import AdbClient
from .matcher import find_template
from . import humanize

logger = logging.getLogger("idle_obelisk_bot.workflows")


@dataclass
class Workflow:
    name: str
    steps: List[Dict[str, Any]]
    trigger: str = "manual"          # "manual" | "interval"
    interval_seconds: float = 3600.0
    last_run: float = field(default=0.0, repr=False, compare=False)

    def due(self) -> bool:
        if self.trigger != "interval":
            return False
        return (time.time() - self.last_run) >= self.interval_seconds


def load_workflows(config: Dict[str, Any]) -> Dict[str, Workflow]:
    raw = config.get("workflows", [])
    workflows: Dict[str, Workflow] = {}
    for w in raw:
        wf = Workflow(
            name=w["name"],
            steps=w.get("steps", []),
            trigger=w.get("trigger", "manual"),
            interval_seconds=w.get("interval_seconds", 3600.0),
        )
        if wf.trigger == "interval":
            # Don't fire immediately on every bot restart -- start the
            # countdown from now, not from "never run".
            wf.last_run = time.time()
        workflows[wf.name] = wf
    logger.info("loaded %d workflows: %s", len(workflows), list(workflows.keys()))
    return workflows


class WorkflowRunner:
    def __init__(self, adb: AdbClient, templates: dict, matching_cfg: dict, humanization_cfg: dict, state=None):
        self.adb = adb
        self.templates = templates
        self.threshold = matching_cfg.get("default_threshold", 0.85)
        self.grayscale = matching_cfg.get("grayscale", True)
        self.humanization = humanization_cfg or {}
        self.state = state

    def run(self, workflow: Workflow) -> bool:
        logger.info("running workflow '%s' (%d steps)", workflow.name, len(workflow.steps))
        for i, step in enumerate(workflow.steps, 1):
            try:
                self._run_step(i, step)
            except Exception as e:
                logger.error("workflow '%s' step %d failed: %s", workflow.name, i, e)
                return False
        workflow.last_run = time.time()
        if self.state:
            self.state.record_workflow_run(workflow.name)
        logger.info("workflow '%s' completed", workflow.name)
        return True

    def _run_step(self, i: int, step: Dict[str, Any]) -> None:
        if "tap_template" in step:
            name = step["tap_template"]
            tmpl = self.templates.get(name)
            if tmpl is None:
                raise RuntimeError(f"missing template '{name}'")
            screen = self.adb.screenshot()
            match = find_template(
                screen, tmpl, threshold=step.get("threshold", self.threshold), grayscale=self.grayscale
            )
            if not match.found:
                raise RuntimeError(f"template '{name}' not found (confidence={match.confidence:.3f})")
            x, y = self._humanized_point(match)
            self.adb.tap(x, y)
            logger.info("workflow step %d: tapped %s at (%d,%d)", i, name, x, y)

        elif "type_text" in step:
            self.adb.input_text(step["type_text"])
            logger.info("workflow step %d: typed text", i)

        elif "wait" in step:
            time.sleep(float(step["wait"]))
            logger.info("workflow step %d: waited %ss", i, step["wait"])

        elif "press_back" in step:
            self.adb.press_back()
            logger.info("workflow step %d: pressed back", i)

        elif "swipe" in step:
            x1, y1, x2, y2 = step["swipe"]
            self.adb.swipe(x1, y1, x2, y2, step.get("duration_ms", 300))
            logger.info("workflow step %d: swiped", i)

        else:
            raise RuntimeError(f"unrecognized step {step}")

        if self.humanization.get("enabled", False):
            humanize.maybe_micro_pause(
                self.humanization.get("micro_pause_chance", 0.0),
                self.humanization.get("micro_pause_range", [0.3, 1.0]),
            )

    def _humanized_point(self, match):
        if not self.humanization.get("enabled", False):
            return match.center
        return humanize.jittered_point(
            match.center, match.top_left, match.size, self.humanization.get("tap_jitter_px", 0)
        )
