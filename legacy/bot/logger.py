"""
Central logging setup. Writes to both console and a rotating log file so
you can watch the bot live during setup and still have history to check
after it's run unattended overnight.
"""

import logging
import logging.handlers
import os


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("idle_obelisk_bot")
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger  # already configured, avoid duplicate handlers on reimport

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "bot.log"), maxBytes=2_000_000, backupCount=5
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
