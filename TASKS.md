# Tasks

## Backlog

### PlaytimeLimit plugin

Status: installed in `plugins/`, waiting for the restart that activates it.

- Modrinth **PlaytimeLimit** 1.0.0 (`DMhofPzM`, Apache-2.0, Paper/Spigot/Bukkit,
  `api-version: 1.20`); sha512 verified, and audited before install (plain
  Bukkit API only — no networking, no `Runtime`, no reflection; it does call
  `Player#kickPlayer`).
- Its day boundary was hardcoded `Asia/Shanghai` with no config key, so
  `scripts/patch-jar-zone.py` rewrote that constant to `America/Sao_Paulo`
  (UTC-3): 3 classes, jar 20696 → 20707 bytes, pristine copy kept at
  `/tmp/plugstage/PlaytimeLimit-1.0.0.jar.orig`. The installed jar therefore
  does **not** match the upstream hash: re-apply the patch after any upstream
  update, and first re-check whether upstream added a `timezone` key.
- Limits as chosen: 60 min/day Mon–Fri, 120 Sat–Sun, 60-min sessions with a
  10-min forced break, warnings at 15/5/1 min, daily cookie reward kept on.
- A server-side watcher (`/tmp/playtimelimit-watch.sh`) waits for an empty
  server, then restarts, verifies the enable line + the generated `config.yml`
  + a `/playtimelimit reload` round trip, and removes the jar if it did not
  enable (no second restart needed: a disabled plugin changes nothing).

Shipped (see README "Changelog" for the details):

- **2026-10-13** — read-only web dashboard: <https://mc.example.com>
  (README "Dashboard", `dashboard/ARCHITECTURE.md`).
- **2026-10-16** — the dashboard shows server up/down (status card + measured
  availability, outage bands on the charts) and the charts follow a timespan
  (24 h / 7 / 30 / 90 days, plus a TPS panel). `mc-watchdog.timer` pushes an
  alert when the server goes down and another when it comes back.
