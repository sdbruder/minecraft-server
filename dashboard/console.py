"""RCON console: the one place in this app that sends an arbitrary command.

Everything else is read-only (the sampler's ``list``, ``tps`` and ``mspt``).
The console is deliberately not: it is the server console, behind the same
login. Two rules keep that honest:

* the RCON password never reaches the browser — the page posts the command
  text and nothing else, and the socket is opened here;
* the transcript lives in memory only. It is a console session, not a record,
  and it disappears with the process (the journal has the commands).

See ARCHITECTURE.md.
"""

import threading
import time
from collections import deque

# Long enough to scroll back through a session, small enough to stay tiny.
MAX_ENTRIES = 200
# The input is one line; a pasted newline must not become a second command.
MAX_COMMAND = 400


class Console:
    """Run commands through RCON and keep the last few exchanges."""

    def __init__(self, run, limit=MAX_ENTRIES):
        self._run = run                    # command -> response text
        self._entries = deque(maxlen=limit)
        self._lock = threading.Lock()

    def entries(self):
        with self._lock:
            return list(self._entries)

    def history(self, limit=60):
        """Distinct commands, newest first: feeds the input's <datalist>, which
        is the whole of this console's command history (no JavaScript)."""
        with self._lock:
            seen = []
            for entry in reversed(self._entries):
                if entry["command"] not in seen:
                    seen.append(entry["command"])
                    if len(seen) >= limit:
                        break
        return seen

    def submit(self, command):
        """Run one command and append the exchange; None for an empty command.

        A failed command is part of the transcript, not an error page: the
        console shows what the server said, including "connection refused".
        """
        command = " ".join((command or "").split())[:MAX_COMMAND]
        if not command:
            return None
        try:
            output, error = (self._run(command) or ""), ""
        except Exception as exc:           # noqa: BLE001 - server down, or a refused command
            output, error = "", f"{type(exc).__name__}: {exc}"
        entry = {"command": command, "output": output, "error": error, "at": time.time()}
        with self._lock:
            self._entries.append(entry)
        return entry
