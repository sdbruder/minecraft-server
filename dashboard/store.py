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
    mspt    REAL
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


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------- writes ---

    def add_sample(self, ts, players, tps=None, mspt=None):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO samples (ts, players, tps, mspt) VALUES (?,?,?,?)",
                (ts, players, tps, mspt),
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
                "SELECT ts, players, tps, mspt FROM samples ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def players_series(self, hours=24, bucket=1800):
        """MAX players per bucket, newest last: [(bucket_ts, players)]."""
        since = int(time.time()) - hours * 3600
        with self._lock:
            rows = self._db.execute(
                "SELECT ts / ? * ? AS bucket, MAX(players) AS players "
                "FROM samples WHERE ts >= ? GROUP BY bucket ORDER BY bucket",
                (bucket, bucket, since),
            ).fetchall()
        return [(row["bucket"], row["players"]) for row in rows]

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

    def rejects_per_day(self, days=30):
        since = _iso(int(time.time()) - days * 86400)
        with self._lock:
            rows = self._db.execute(
                "SELECT substr(ts_iso, 1, 10) AS day, COUNT(*) AS n FROM rejects "
                "WHERE ts_iso >= ? GROUP BY day ORDER BY day",
                (since,),
            ).fetchall()
        return [(row["day"], row["n"]) for row in rows]

    def top_rejected(self, limit=10):
        with self._lock:
            rows = self._db.execute(
                "SELECT name, ip, COUNT(*) AS n FROM rejects "
                "GROUP BY name, ip ORDER BY n DESC, name LIMIT ?",
                (limit,),
            ).fetchall()
        return [(row["name"], row["ip"], row["n"]) for row in rows]
