"""
Main automation loop.

Every cycle:
  1. grab a screenshot (also cached for the dashboard)
  2. run any manually-queued workflows, and any interval workflows that are due
  3. if tasks are enabled (not paused), walk tasks in priority order and
     run the first matching, off-cooldown one
  4. if something matched, loop again quickly to reassess the new state
  5. if nothing matched, sleep the (humanized) idle interval
  6. if nothing has matched for N cycles in a row, run the recovery action

Re-evaluating from scratch every cycle (rather than a linear tap-A-then-B
script) is what makes it tolerant of popups/ads/interruptions showing up
in an unpredictable order.
"""

from __future__ import annotations

import time
import queue
import logging
from typing import Dict, Any, Optional

import cv2

from .adb_client import AdbClient, AdbError
from .matcher import load_templates, find_template
from .tasks import load_tasks, Task
from .workflows import load_workflows, WorkflowRunner
from . import humanize

logger = logging.getLogger("idle_obelisk_bot.scheduler")


class Bot:
    def __init__(
        self,
        config: Dict[str, Any],
        template_dir: str,
        state=None,
        workflow_queue: Optional[queue.Queue] = None,
    ):
        self.config = config
        self.state = state
        self.workflow_queue = workflow_queue or queue.Queue()

        adb_cfg = config.get("adb", {})
        self.adb = AdbClient(serial=adb_cfg.get("serial"))

        cap_cfg = config.get("capture", {})
        self.idle_interval = cap_cfg.get("interval_idle_seconds", 15)
        self.active_interval = cap_cfg.get("interval_active_seconds", 2)

        match_cfg = config.get("matching", {})
        self.grayscale = match_cfg.get("grayscale", True)

        self.templates = load_templates(template_dir, grayscale=self.grayscale)
        self.tasks = load_tasks(config)

        self.humanization = config.get("humanization", {"enabled": False})

        self.workflows = load_workflows(config)
        self.workflow_runner = WorkflowRunner(
            adb=self.adb,
            templates=self.templates,
            matching_cfg=match_cfg,
            humanization_cfg=self.humanization,
            state=self.state,
        )

        recovery_cfg = config.get("recovery", {})
        self.max_unknown_cycles = recovery_cfg.get("max_unknown_cycles", 5)
        self.recovery_action = recovery_cfg.get("action", "press_back")

        self._unknown_streak = 0
        self._consecutive_errors = 0

    def ensure_connected(self):
        host = self.config.get("adb", {}).get("connect_host")
        port = self.config.get("adb", {}).get("connect_port", 5555)
        if host and not self.adb.is_connected():
            self.adb.connect_tcp(host, port)

    # -- controls exposed to the dashboard ---------------------------------

    def set_tasks_enabled(self, enabled: bool):
        if self.state:
            self.state.set_tasks_enabled(enabled)

    def request_workflow(self, name: str):
        if name not in self.workflows:
            raise KeyError(f"unknown workflow '{name}'")
        self.workflow_queue.put(name)

    # -- internals ------------------------------------------------------------

    def _tasks_enabled(self) -> bool:
        return self.state.tasks_enabled if self.state else True

    def _humanized_tap_pos(self, task: Task, match):
        x, y = match.center
        if self.humanization.get("enabled", False):
            x, y = humanize.jittered_point(
                match.center, match.top_left, match.size, self.humanization.get("tap_jitter_px", 0)
            )
        return x + task.tap_offset[0], y + task.tap_offset[1]

    def _perform(self, task: Task, match) -> None:
        if task.action == "tap":
            x, y = self._humanized_tap_pos(task, match)
            self.adb.tap(x, y)
            logger.info("task=%s action=tap pos=(%d,%d) confidence=%.3f", task.name, x, y, match.confidence)
        elif task.action == "swipe" and task.swipe_to:
            x1, y1 = match.center
            x2, y2 = task.swipe_to
            self.adb.swipe(x1, y1, x2, y2)
            logger.info("task=%s action=swipe from=(%d,%d) to=(%d,%d)", task.name, x1, y1, x2, y2)
        elif task.action == "skip":
            logger.debug("task=%s matched but action=skip, ignoring", task.name)
        else:
            logger.warning("task=%s has unknown action '%s'", task.name, task.action)

        if self.state:
            self.state.record_task_run(task.name)

        if self.humanization.get("enabled", False):
            humanize.maybe_micro_pause(
                self.humanization.get("micro_pause_chance", 0.0),
                self.humanization.get("micro_pause_range", [0.3, 1.0]),
            )

    def _run_recovery(self):
        logger.warning(
            "no known UI state matched for %d cycles in a row; running recovery action '%s'",
            self._unknown_streak,
            self.recovery_action,
        )
        if self.recovery_action == "press_back":
            self.adb.press_back()
        elif self.recovery_action == "restart_game":
            package = self.config.get("adb", {}).get("package")
            if package:
                self.adb.force_stop(package)
                time.sleep(2)
                self.adb.start_app(package)
            else:
                logger.warning("recovery action is restart_game but no adb.package configured")
        else:
            logger.warning("unknown recovery action '%s', doing nothing", self.recovery_action)
        self._unknown_streak = 0

    def _drain_manual_workflows(self):
        while True:
            try:
                name = self.workflow_queue.get_nowait()
            except queue.Empty:
                return
            wf = self.workflows.get(name)
            if wf:
                self.workflow_runner.run(wf)
            else:
                logger.warning("requested unknown workflow '%s'", name)

    def _run_due_interval_workflows(self):
        for wf in self.workflows.values():
            if wf.due():
                self.workflow_runner.run(wf)

    def _update_dashboard_screenshot(self, screen):
        if not self.state:
            return
        ok, buf = cv2.imencode(".jpg", screen, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            self.state.set_screenshot(buf.tobytes())

    def run_forever(self):
        logger.info("bot starting main loop")
        if self.state:
            self.state.set_status("running")
        self.ensure_connected()

        while True:
            self._drain_manual_workflows()
            self._run_due_interval_workflows()

            try:
                screen = self.adb.screenshot()
            except AdbError as e:
                self._consecutive_errors += 1
                if self.state:
                    self.state.set_error(str(e))
                logger.error("screenshot failed (%d in a row): %s", self._consecutive_errors, e)
                time.sleep(min(60, 2 ** self._consecutive_errors))
                continue

            self._consecutive_errors = 0
            if self.state:
                self.state.set_error(None)
            self._update_dashboard_screenshot(screen)

            matched_something = False

            if self._tasks_enabled():
                for task in self.tasks:
                    if not task.ready():
                        continue
                    template_img = self.templates.get(task.template)
                    if template_img is None:
                        logger.warning("task=%s references missing template '%s'", task.name, task.template)
                        continue

                    match = find_template(
                        screen,
                        template_img,
                        threshold=task.threshold,
                        grayscale=self.grayscale,
                        region=task.region,
                    )
                    if match.found:
                        task.mark_run()
                        self._perform(task, match)
                        matched_something = True
                        self._unknown_streak = 0
                        break  # re-capture and re-evaluate from the top next cycle

                if not matched_something:
                    self._unknown_streak += 1
                    if self.state:
                        self.state.set_unknown_streak(self._unknown_streak)
                    if self._unknown_streak >= self.max_unknown_cycles:
                        self._run_recovery()
                if self.state:
                    self.state.set_status("running")
            else:
                # paused: still capture screenshots for the dashboard, just don't act
                if self.state:
                    self.state.set_status("paused")
                    self.state.set_unknown_streak(0)
                self._unknown_streak = 0

            base_interval = self.active_interval if matched_something else self.idle_interval
            if self.humanization.get("enabled", False):
                humanize.jittered_sleep(base_interval, self.humanization.get("timing_jitter_fraction", 0.0))
            else:
                time.sleep(base_interval)
