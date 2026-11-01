"""
Task definitions loaded from config.yaml, plus priority ordering.

A "task" is one recognizable UI state (a button, an icon, a popup) paired
with an action to take when it's on screen. All game-specific knowledge
lives in config.yaml, not here.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger("idle_obelisk_bot.tasks")


@dataclass
class Task:
    name: str
    template: str
    action: str = "tap"                       # "tap" | "swipe" | "skip"
    threshold: Optional[float] = None         # falls back to matching.default_threshold
    priority: int = 0                          # higher runs first each cycle
    cooldown_seconds: float = 3.0
    tap_offset: Tuple[int, int] = (0, 0)
    region: Optional[Tuple[int, int, int, int]] = None
    swipe_to: Optional[Tuple[int, int]] = None
    enabled: bool = True

    last_run: float = field(default=0.0, repr=False, compare=False)

    def ready(self) -> bool:
        return self.enabled and (time.time() - self.last_run) >= self.cooldown_seconds

    def mark_run(self):
        self.last_run = time.time()


def load_tasks(config: Dict[str, Any]) -> List[Task]:
    default_threshold = config.get("matching", {}).get("default_threshold", 0.85)
    raw_tasks = config.get("tasks", [])
    tasks: List[Task] = []
    for t in raw_tasks:
        tasks.append(
            Task(
                name=t["name"],
                template=t["template"],
                action=t.get("action", "tap"),
                threshold=t.get("threshold", default_threshold),
                priority=t.get("priority", 0),
                cooldown_seconds=t.get("cooldown_seconds", 3.0),
                tap_offset=tuple(t.get("tap_offset", (0, 0))),
                region=tuple(t["region"]) if t.get("region") else None,
                swipe_to=tuple(t["swipe_to"]) if t.get("swipe_to") else None,
                enabled=t.get("enabled", True),
            )
        )
    tasks.sort(key=lambda t: t.priority, reverse=True)
    logger.info("loaded %d tasks: %s", len(tasks), [t.name for t in tasks])
    return tasks
