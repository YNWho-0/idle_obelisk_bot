"""
A logging.Handler that pushes formatted log records into a StateStore's
ring buffer, so the web dashboard can show a live log stream without
tailing bot.log from disk.
"""

import logging


class StateLogHandler(logging.Handler):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def emit(self, record):
        try:
            msg = self.format(record)
            self.state.append_log(msg)
        except Exception:
            self.handleError(record)
