#!/usr/bin/env python3
"""Minecraft dashboard: read-only server metrics over a small HTTP port.

One process: an HTTP server for browsers plus a sampler thread that keeps the
SQLite history current. Read-only — the only RCON commands issued are
``list``, ``tps`` and ``mspt``. See ARCHITECTURE.md.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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
STATIC_OK = {"pico.classless.min.css", "app.css"}

# Stylesheets are served with a long max-age, so the URL carries a content
# hash: editing app.css changes the URL and busts the browser cache.
try:
    ASSET_VERSION = hashlib.sha256((BASE / "static" / "app.css").read_bytes()).hexdigest()[:8]
except OSError:
    ASSET_VERSION = "0"

SESSION_TTL = int(os.environ.get("DASH_SESSION_TTL", 30 * 24 * 3600))
SESSION_COOKIE = "mc_session"
LOGIN_URL = "/login"
LOGIN_WINDOW = 900      # seconds
LOGIN_MAX_FAILURES = 10

properties = collect.read_properties(PROPERTIES)
RCON_PORT = int(properties.get("rcon.port") or 25575)
RCON_PASSWORD = properties.get("rcon.password", "")

store = Store(DB_PATH)
_live_lock = threading.Lock()
_live_cache = {"t": 0.0, "v": None}
_world_lock = threading.Lock()
_world_cache = {"t": 0.0, "v": None}
_failures = {}          # client ip -> (failure count, window start)


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


# ------------------------------------------------------------- sessions -----

def _session_key():
    """Sessions are signed with a key derived from the password, so changing
    the password invalidates every existing session."""
    return hashlib.sha256(("mc-dashboard|" + PASSWORD).encode()).digest()


def make_session(user):
    payload = f"{user}|{int(time.time()) + SESSION_TTL}"
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = hmac.new(_session_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{signature}"


def verify_session(value):
    """Return the user name for a valid, unexpired cookie value, else None."""
    if not value or "." not in value:
        return None
    body, signature = value.rsplit(".", 1)
    expected = hmac.new(_session_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        user, expiry = payload.rsplit("|", 1)
        if int(expiry) < time.time():
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    return user


def safe_next(value):
    """Only ever redirect to a same-site path."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


# ---------------------------------------------------------------- HTTP ------

class Handler(BaseHTTPRequestHandler):
    server_version = "mc-dashboard"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def handle_one_request(self):
        # A bug must show up in the journal as a 500, not as a dropped socket.
        try:
            super().handle_one_request()
        except Exception as exc:                   # noqa: BLE001
            sys.stderr.write(f"handler error: {exc!r}\n")
            try:
                self._send(500, "internal error\n", "text/plain; charset=utf-8")
            except Exception:                      # noqa: BLE001 - already broken
                pass

    def _send(self, status, body, content_type, cache="no-store", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        # "same-origin" (not "no-referrer"): no referrer ever reaches a
        # third party, but Chromium still sends a real Origin on form POSTs.
        # With "no-referrer" it sends "Origin: null" and the CSRF check below
        # cannot tell same-site from cross-site submissions.
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ------------------------------------------------------- auth plumbing --

    def _cookies(self):
        jar = {}
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                jar[name] = value
        return jar

    def _session_user(self):
        return verify_session(self._cookies().get(SESSION_COOKIE, ""))

    def _cookie_header(self, value, max_age):
        parts = [f"{SESSION_COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax",
                 f"Max-Age={max_age}"]
        if self.headers.get("X-Forwarded-Proto") == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _client_ip(self):
        # Safe to trust: the app listens on loopback only, so nginx sets this.
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def _throttled(self):
        record = _failures.get(self._client_ip())
        if not record:
            return False
        count, first = record
        if time.time() - first > LOGIN_WINDOW:
            _failures.pop(self._client_ip(), None)
            return False
        return count >= LOGIN_MAX_FAILURES

    def _note_failure(self):
        ip = self._client_ip()
        count, first = _failures.get(ip, (0, time.time()))
        if time.time() - first > LOGIN_WINDOW:
            count, first = 0, time.time()
        _failures[ip] = (count + 1, first)

    def _same_origin(self):
        """CSRF guard for POSTs: reject a *verifiable* cross-site origin.

        SameSite=Lax on the session cookie is the primary defence; this is a
        second layer. An opaque origin ("null") cannot be checked, so it is
        accepted rather than breaking the login form in privacy-hardened
        browsers (the value is logged when a real mismatch is rejected).
        """
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return True
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def _form(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 8192:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def _query(self, name, default=""):
        return parse_qs(urlparse(self.path).query).get(name, [default])[0]

    def _redirect(self, location, headers=None):
        merged = {"Location": location}
        merged.update(headers or {})
        self._send(302, "", "text/plain; charset=utf-8", headers=merged)

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
        if path == LOGIN_URL:
            if self._session_user():
                self._redirect("/")
            else:
                self._send(200, render.login_page(next_path=safe_next(self._query("next")),
                                                  asset_v=ASSET_VERSION),
                           "text/html; charset=utf-8")
            return
        if not self._session_user():
            self._redirect(f"{LOGIN_URL}?next={quote(path, safe='/')}")
            return
        if path == "/":
            self._send(200, render.page(full_state(), REFRESH, asset_v=ASSET_VERSION),
                       "text/html; charset=utf-8")
        elif path == "/state.json":
            self._send(200, json.dumps(full_state(), indent=2, sort_keys=True, default=str),
                       "application/json; charset=utf-8")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def _login(self):
        form = self._form()
        target = safe_next(form.get("next", "/"))
        if self._throttled():
            self._send(429, "too many attempts; try again later\n", "text/plain; charset=utf-8",
                       headers={"Retry-After": str(LOGIN_WINDOW)})
            return
        user = form.get("username", "")
        password = form.get("password", "")
        if hmac.compare_digest(user, USER) and hmac.compare_digest(password, PASSWORD):
            _failures.pop(self._client_ip(), None)
            self._redirect(target,
                           {"Set-Cookie": self._cookie_header(make_session(user), SESSION_TTL)})
            return
        self._note_failure()
        self._send(401, render.login_page(error="Wrong username or password.", next_path=target),
                   "text/html; charset=utf-8")

    def do_POST(self):
        if not self._same_origin():
            sys.stderr.write(
                f"rejected cross-origin POST: origin={self.headers.get('Origin')!r} "
                f"host={self.headers.get('Host')!r}\n"
            )
            self._send(403, "cross-origin request rejected\n", "text/plain; charset=utf-8")
            return
        if not USER or not PASSWORD:
            self._send(503, "dashboard not configured: set DASH_USER and DASH_PASS\n",
                       "text/plain; charset=utf-8")
            return
        path = self.path.split("?", 1)[0]
        if path == LOGIN_URL:
            self._login()
        elif path == "/logout":
            self._redirect(LOGIN_URL, {"Set-Cookie": self._cookie_header("", 0)})
        else:
            self._send(405, "method not allowed\n", "text/plain; charset=utf-8")

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
