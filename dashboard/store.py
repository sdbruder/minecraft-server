"""SQLite history store: player samples, join/leave sessions, rejected joins.

Single small file, no server. One connection guarded by a lock (the sampler
thread and request threads share it; the volume is tiny).
"""

import datetime
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts      INTEGER PRIMARY KEY,
    players INTEGER NOT NULL,
    tps     REAL,
    mspt    REAL,
    up      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    ts     INTEGER NOT NULL,
    player TEXT    NOT NULL,
    event  TEXT    NOT NULL CHECK (event IN ('join', 'leave'))
);
CREATE INDEX IF NOT EXISTS sessions_ts ON sessions (ts);
CREATE INDEX IF NOT EXISTS sessions_player ON sessions (player, ts);
CREATE TABLE IF NOT EXISTS rejects (
    ts     TEXT NOT NULL,
    name   TEXT NOT NULL,
    ip     TEXT NOT NULL,
    ts_iso TEXT,
    UNIQUE (ts, name, ip, ts_iso)
);
CREATE INDEX IF NOT EXISTS rejects_iso ON rejects (ts_iso);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


# Time windows the charts offer: key -> (label, hours, bucket seconds). The
# bucket sizes keep every span at 48-168 points, which is all a rendered SVG
# can usefully show. This table lives here (not in the renderer) so the query
# and the panel title can never disagree about the window.
SPANS = {
    "24h": ("last 24h", 24, 1800),
    "7d": ("last 7 days", 24 * 7, 3600),
    "30d": ("last 30 days", 24 * 30, 6 * 3600),
    "90d": ("last 90 days", 24 * 90, 86400),
}
DEFAULT_SPAN = "24h"


def span_spec(key):
    """Resolve a requested span key to {key, label, hours, bucket}."""
    key = key if key in SPANS else DEFAULT_SPAN
    label, hours, bucket = SPANS[key]
    return {"key": key, "label": label, "hours": hours, "bucket": bucket}


def _iso(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


_ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")


def parse_iso(value):
    """Epoch for a 'YYYY-MM-DDTHH:MM:SS+ZZZZ' stamp, or None.

    The offsets in blocked-joins.log vary (+0000 and -0300 both occur), so
    comparing those strings in SQL would sort them by local clock time; parse
    instead. Volume here is a handful of rows per day.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    for fmt in _ISO_FORMATS:
        try:
            return int(datetime.datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return None


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._migrate()

    def _migrate(self):
        """Columns a database created by an older version lacks.

        ``samples.up`` arrived on 2026-10-16. Defaulting it to 1 is correct for
        the rows written before it existed: back then a sample was only written
        when RCON answered, i.e. while the server was up.
        """
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(samples)")}
        if "up" not in columns:
            with self._lock:
                self._db.execute(
                    "ALTER TABLE samples ADD COLUMN up INTEGER NOT NULL DEFAULT 1"
                )
                self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------- writes ---

    def add_sample(self, ts, players, tps=None, mspt=None, up=True):
        """One sampler tick. ``up=False`` records a tick where RCON did not
        answer: ``players`` is then meaningless (0) and readers filter it out;
        the row exists so the outage is visible in the history."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO samples (ts, players, tps, mspt, up) "
                "VALUES (?,?,?,?,?)",
                (ts, players, tps, mspt, 1 if up else 0),
            )
            self._db.commit()

    def add_events(self, events):
        """events: iterable of (ts, player, event)."""
        rows = list(events)
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO sessions (ts, player, event) VALUES (?,?,?)", rows
            )
            self._db.commit()

    def ingest_rejects(self, path):
        """Tail blocked-joins.log by byte offset (idempotent). Returns rows added."""
        try:
            stat = os.stat(path)
        except OSError:
            return 0
        size, inode = stat.st_size, stat.st_ino
        with self._lock:
            saved = {
                row["key"]: row["value"]
                for row in self._db.execute(
                    "SELECT key, value FROM meta "
                    "WHERE key IN ('rejects_offset', 'rejects_inode')"
                )
            }
            offset = int(saved.get("rejects_offset", 0))
            # Replaced (different inode) or truncated file -> start over.
            if int(saved.get("rejects_inode", inode)) != inode or offset > size:
                offset = 0
            if offset == size:
                return 0
            try:
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    data = handle.read()
            except OSError:
                return 0

            text = data.decode("utf-8", "replace")
            if not text.endswith("\n"):
                cut = text.rfind("\n")  # keep a partial trailing line for next time
                if cut == -1:
                    return 0
                text = text[: cut + 1]
                new_offset = offset + len(text.encode("utf-8"))
            else:
                new_offset = offset + len(data)

            added = 0
            for line in text.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                added += self._db.execute(
                    "INSERT OR IGNORE INTO rejects (ts, name, ip, ts_iso) VALUES (?,?,?,?)",
                    (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else None),
                ).rowcount
            self._db.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [("rejects_offset", str(new_offset)), ("rejects_inode", str(inode))],
            )
            self._db.commit()
            return added

    def prune(self, retain_days):
        """Drop history older than retain_days. Returns rows removed."""
        if not retain_days or retain_days <= 0:
            return 0
        cutoff = int(time.time()) - retain_days * 86400
        cutoff_iso = _iso(cutoff)
        with self._lock:
            removed = 0
            for sql, arg in (
                ("DELETE FROM samples WHERE ts < ?", cutoff),
                ("DELETE FROM sessions WHERE ts < ?", cutoff),
                ("DELETE FROM rejects WHERE ts_iso < ?", cutoff_iso),
            ):
                removed += self._db.execute(sql, (arg,)).rowcount
            self._db.commit()
            return removed

    # -------------------------------------------------------------- reads ---

    def latest_sample(self):
        with self._lock:
            row = self._db.execute(
                "SELECT ts, players, tps, mspt, up FROM samples ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def samples_series(self, hours=24, bucket=1800):
        """Per-bucket history for the charts, oldest first.

        [(bucket_ts, max_players, avg_tps, down_ticks, ticks)]. Buckets with no
        samples at all are absent, so a gap in the series means "no data"
        (the dashboard was not sampling) while ``down_ticks`` around it mean
        "sampled, and the server was down". ``max_players``/``avg_tps`` are
        None when every tick in the bucket was a down tick.
        """
        since = int(time.time()) - hours * 3600
        with self._lock:
            rows = self._db.execute(
                "SELECT ts / ? * ? AS bucket,"
                "       MAX(CASE WHEN up = 1 THEN players END) AS players,"
                "       AVG(CASE WHEN up = 1 THEN tps END) AS tps,"
                "       SUM(CASE WHEN up = 0 THEN 1 ELSE 0 END) AS down_ticks,"
                "       COUNT(*) AS ticks"
                " FROM samples WHERE ts >= ? GROUP BY bucket ORDER BY bucket",
                (bucket, bucket, since),
            ).fetchall()
        return [
            (row["bucket"], row["players"], row["tps"], row["down_ticks"], row["ticks"])
            for row in rows
        ]

    def down_intervals(self, hours=24, merge=90):
        """Outages in the last ``hours`` as [(start, end)] epochs.

        Consecutive down ticks belong to the same outage; ``merge`` is the
        longest gap (seconds) still treated as one — three missed ticks at the
        30 s cadence, so two outages minutes apart stay separate.
        """
        since = int(time.time()) - hours * 3600
        with self._lock:
            rows = self._db.execute(
                "SELECT ts FROM samples WHERE up = 0 AND ts >= ? ORDER BY ts",
                (since,),
            ).fetchall()
        spans = []
        for row in rows:
            ts = row["ts"]
            if spans and ts - spans[-1][1] <= merge:
                spans[-1][1] = ts
            else:
                spans.append([ts, ts])
        return [tuple(item) for item in spans]

    def availability(self, hours=24):
        """How much of the sampled time the server was up, or None if nothing
        was sampled. ``outage_start`` is set only while an outage is running."""
        since = int(time.time()) - hours * 3600
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS ticks, SUM(up) AS up_ticks, MAX(ts) AS last_ts "
                "FROM samples WHERE ts >= ?",
                (since,),
            ).fetchone()
            if row is None or not row["ticks"]:
                return None
            last_up = self._db.execute(
                "SELECT MAX(ts) AS ts FROM samples WHERE up = 1"
            ).fetchone()["ts"]
            outage = self._db.execute(
                "SELECT MIN(ts) AS ts FROM samples WHERE up = 0 AND ts > ?",
                (last_up if last_up is not None else 0,),
            ).fetchone()["ts"]
        ticks = row["ticks"]
        up_ticks = row["up_ticks"] or 0
        return {
            "ticks": ticks,
            "up_ticks": up_ticks,
            "down_ticks": ticks - up_ticks,
            "up_pct": 100.0 * up_ticks / ticks,
            "last_ts": row["last_ts"],
            "last_up_ts": last_up,
            "outage_start": outage if row["last_ts"] != last_up else None,
        }

    def recent_sessions(self, limit=20):
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, player, event FROM sessions ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def playtime(self, days=7, limit=10):
        """Total connected seconds per player over the last N days."""
        since = int(time.time()) - days * 86400
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, player, event FROM sessions WHERE ts >= ? ORDER BY ts",
                (since,),
            ).fetchall()
        totals, opened = {}, {}
        for row in rows:
            if row["event"] == "join":
                opened[row["player"]] = row["ts"]
            else:
                start = opened.pop(row["player"], None)
                if start is not None:
                    totals[row["player"]] = totals.get(row["player"], 0) + (row["ts"] - start)
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]

    def recent_rejects(self, limit=20):
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, name, ip, ts_iso FROM rejects ORDER BY ts_iso DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def rejects_series(self, hours=24, bucket=1800):
        """Rejected joins per bucket in the last ``hours``: [(bucket_ts, n)]."""
        since = int(time.time()) - hours * 3600
        with self._lock:
            rows = self._db.execute("SELECT ts_iso FROM rejects").fetchall()
        counts = {}
        for row in rows:
            ts = parse_iso(row["ts_iso"])
            if ts is None or ts < since:
                continue
            slot = ts // bucket * bucket
            counts[slot] = counts.get(slot, 0) + 1
        return sorted(counts.items())

    def top_rejected(self, limit=10):
        with self._lock:
            rows = self._db.execute(
                "SELECT name, ip, COUNT(*) AS n FROM rejects "
                "GROUP BY name, ip ORDER BY n DESC, name LIMIT ?",
                (limit,),
            ).fetchall()
        return [(row["name"], row["ip"], row["n"]) for row in rows]
