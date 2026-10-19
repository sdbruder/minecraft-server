# Tasks

## Backlog

### PlaytimeLimit plugin

Status: enabled and verified (2026-10-17 22:11). Its limits live in
`plugins/PlaytimeLimit/config.yml` on the server and have been tuned since
(120 min/day, 120-min sessions) — that file is the source of truth, not this
list.

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
- Installed with the plugin's shipped defaults (60 min/day Mon–Fri, 120 Sat–Sun,
  60-min sessions, 10-min forced breaks, warnings at 15/5/1 min, the daily
  cookie reward kept on); tuned afterwards on the server.
- Verified by the watcher on 2026-10-17 22:11: `Enabling PlaytimeLimit v1.0.0`,
  `PlaytimeLimit enabled!`, a generated `config.yml`, `/playtimelimit reload` →
  `Configuration reloaded!`, and a real `Executed daily first-login command for
  Alex` at 10:38 the next morning.

Shipped (see README "Changelog" for the details):

- **2026-10-20** — an RCON console at `/console` (linked from the footer row):
  one command, one answer, monospace, Paper's §-colour codes rendered. It is the
  only place the dashboard is not read-only.
- **2026-10-13** — read-only web dashboard: <https://mc.example.com>
  (README "Dashboard", `dashboard/ARCHITECTURE.md`).
- **2026-10-19** — a Restart button on the dashboard, executed through
  `mc-restart.path` so the web app needs no privilege.
- **2026-10-19** — "Restart when empty": queues the restart until the last
  player leaves, with a cancel button (`mc-restart-when-empty.path` +
  `scripts/restart-when-empty.sh` + `scripts/rcon-list.py`). Both restart
  buttons sit in the footer row next to Sign out (red = now, orange = when
  empty), and the confirmation card is gone.
- **2026-10-16** — the dashboard shows server up/down (status card + measured
  availability, outage bands on the charts) and the charts follow a timespan
  (24 h / 7 / 30 / 90 days, plus a TPS panel). `mc-watchdog.timer` pushes an
  alert when the server goes down and another when it comes back.
