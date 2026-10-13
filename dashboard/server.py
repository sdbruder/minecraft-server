#!/usr/bin/env python3
"""Minecraft dashboard: read-only server metrics over a small HTTP port.

One process: an HTTP server for browsers plus a sampler thread that keeps the
SQLite history current. Read-only — the only RCON commands issued are
``list``, ``tps`` and ``mspt``. See ARCHITECTURE.md.
"""

import base64
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect            # noqa: E402
import render             # noqa: E402
from store import Store   # noqa: E402

# ----------------------------------------------------------------- config ---

BASE = Path(__file__).resolve().parent
MC_DIR = Path(os.environ.get("MC_DIR") or Path.home() / "minecraft")
BIND = os.environ.get("DASH_BIND", "127.0.0.1")
PORT = int(os.environ.get("DASH_PORT", "8080"))
USER = os.environ.get("DASH_USER", "")
PASSWORD = os.environ.get("DASH_PASS", "")
REFRESH = int(os.environ.get("REFRESH", "15"))
SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "30"))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "90"))
RCON_HOST = os.environ.get("RCON_HOST", "127.0.0.1")
DB_PATH = os.environ.get("DASH_DB", str(MC_DIR / "dashboard.sqlite"))

PROPERTIES = MC_DIR / "server.properties"
LATEST_LOG = MC_DIR / "logs" / "latest.log"
BLOCKED_LOG = MC_DIR / "logs" / "blocked-joins.log"
PLUGINS_DIR = MC_DIR / "plugins"

LIVE_TTL = 5.0          # seconds a live snapshot is reused
WORLD_TTL = 600.0       # seconds the world size is reused (walking is slow)
STATIC_OK = {"pico.classless.min.css"}

properties = collect.read_properties(PROPERTIES)
RCON_PORT = int(properties.get("rcon.port") or 25575)
RCON_PASSWORD = properties.get("rcon.password", "")

store = Store(DB_PATH)
_live_lock = threading.Lock()
_live_cache = {"t": 0.0, "v": None}
_world_lock = threading.Lock()
_world_cache = {"t": 0.0, "v": None}


# ----------------------------------------------------------- collection -----

def rcon(command):
    return collect.rcon_command(RCON_HOST, RCON_PORT, RCON_PASSWORD, command)


_uptime_cache = {"t": 0.0, "v": None}


def server_uptime():
    """Scanning /proc is cheap but not free — cache it for a minute."""
    if _uptime_cache["v"] is not None and time.time() - _uptime_cache["t"] < 60:
        return _uptime_cache["v"]
    value = collect.server_uptime_seconds()
    _uptime_cache.update(t=time.time(), v=value)
    return value


def world_size():
    world = properties.get("level-name")
    if not world:
        return None
    with _world_lock:
        if _world_cache["v"] is not None and time.time() - _world_cache["t"] < WORLD_TTL:
            return _world_cache["v"]
    size = collect.dir_size(MC_DIR / world)
    with _world_lock:
        _world_cache.update(t=time.time(), v=size)
    return size


def gather_live():
    errors = []
    players = {"count": None, "max": None, "names": [], "error": None}
    try:
        parsed = collect.parse_players(rcon("list"))
        if parsed:
            players.update(count=parsed[0], max=parsed[1], names=parsed[2])
        else:
            players["error"] = "unexpected response to 'list'"
    except Exception as exc:                       # noqa: BLE001 - report, don't crash
        players["error"] = str(exc)
        errors.append(f"players: {exc}")
        last = store.latest_sample()
        if last:                                   # show the last known count
            players["count"] = last["players"]

    perf = {"tps": None, "mspt": None, "uptime": server_uptime()}
    for key, command, parser in (
        ("tps", "tps", collect.parse_tps),
        ("mspt", "mspt", collect.parse_mspt),
    ):
        try:
            perf[key] = parser(rcon(command))
        except Exception as exc:                   # noqa: BLE001
            errors.append(f"{key}: {exc}")

    server = {
        "motd": properties.get("motd"),
        "world": properties.get("level-name"),
        "max_players": int(properties["max-players"])
        if (properties.get("max-players") or "").isdigit()
        else None,
        "online_mode": properties.get("online-mode") == "true",
    }
    return {
        "players": players,
        "perf": perf,
        "versions": collect.parse_versions(collect.read_log_head(LATEST_LOG)),
        "plugins": collect.read_plugins(PLUGINS_DIR),
        "world_size": world_size(),
        "server": server,
        "errors": errors,
    }


def live_state():
    with _live_lock:
        if _live_cache["v"] is not None and time.time() - _live_cache["t"] < LIVE_TTL:
            return _live_cache["v"]
        value = gather_live()
        _live_cache.update(t=time.time(), v=value)
        return value


def full_state():
    state = dict(live_state())
    state["generated"] = time.time()
    state["history"] = {
        "series": store.players_series(hours=24, bucket=1800),
        "per_day": store.rejects_per_day(days=30),
        "top": store.top_rejected(10),
        "recent_rejects": store.recent_rejects(20),
        "sessions": store.recent_sessions(20),
        "playtime": store.playtime(days=7, limit=10),
    }
    return state


# -------------------------------------------------------------- sampler -----

def sampler():
    """Sample `list` every SAMPLE_SECONDS; diff names into join/leave events."""
    previous = None            # None on the first tick: baseline, no events
    last_prune = time.time()
    while True:
        try:
            parsed = collect.parse_players(rcon("list"))
        except Exception:      # noqa: BLE001 - server down, try again later
            parsed = None
        if parsed:
            now = int(time.time())
            current = set(parsed[2])
            tps = mspt = None
            try:
                tps = (collect.parse_tps(rcon("tps")) or [None])[0]
            except Exception:  # noqa: BLE001
                pass
            try:
                mspt = (collect.parse_mspt(rcon("mspt")) or [None])[0]
            except Exception:  # noqa: BLE001
                pass
            store.add_sample(now, parsed[0], tps, mspt)
            if previous is not None:
                events = [(now, name, "join") for name in sorted(current - previous)]
                events += [(now, name, "leave") for name in sorted(previous - current)]
                store.add_events(events)
            previous = current
        try:
            store.ingest_rejects(BLOCKED_LOG)
        except Exception:      # noqa: BLE001
            pass
        if time.time() - last_prune > 86400:
            try:
                store.prune(RETAIN_DAYS)
            except Exception:  # noqa: BLE001
                pass
            last_prune = time.time()
        time.sleep(SAMPLE_SECONDS)


# ---------------------------------------------------------------- HTTP ------

class Handler(BaseHTTPRequestHandler):
    server_version = "mc-dashboard"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body, content_type, cache="no-store", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8", "replace")
        except Exception:      # noqa: BLE001
            return False
        user, _, password = decoded.partition(":")
        return hmac.compare_digest(user, USER) and hmac.compare_digest(password, PASSWORD)

    def _static(self, name):
        if name not in STATIC_OK:
            self._send(404, "not found\n", "text/plain; charset=utf-8")
            return
        try:
            data = (BASE / "static" / name).read_bytes()
        except OSError:
            self._send(404, "not found\n", "text/plain; charset=utf-8")
            return
        self._send(200, data, "text/css; charset=utf-8", cache="public, max-age=86400")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, "ok\n", "text/plain; charset=utf-8")
            return
        if path.startswith("/static/"):        # stylesheet is not sensitive
            self._static(path[len("/static/"):])
            return
        if not USER or not PASSWORD:
            self._send(503, "dashboard not configured: set DASH_USER and DASH_PASS\n",
                       "text/plain; charset=utf-8")
            return
        if not self._authorized():
            self._send(401, "authentication required\n", "text/plain; charset=utf-8",
                       headers={"WWW-Authenticate": 'Basic realm="minecraft dashboard"'})
            return
        if path == "/":
            self._send(200, render.page(full_state(), REFRESH), "text/html; charset=utf-8")
        elif path == "/state.json":
            self._send(200, json.dumps(full_state(), indent=2, sort_keys=True, default=str),
                       "application/json; charset=utf-8")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()


def main():
    if not USER or not PASSWORD:
        print("warning: DASH_USER/DASH_PASS not set — the page returns 503",
              file=sys.stderr)
    print(f"mc-dashboard listening on {BIND}:{PORT} "
          f"(mc_dir={MC_DIR}, db={DB_PATH})", file=sys.stderr)
    threading.Thread(target=sampler, name="sampler", daemon=True).start()
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        store.close()


if __name__ == "__main__":
    main()
