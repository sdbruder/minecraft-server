# Tasks

## Backlog

### PlaytimeLimit plugin

Status: jar staged, waiting on the limit values.

- Modrinth **PlaytimeLimit** 1.0.0 (`DMhofPzM`, Apache-2.0, Paper/Spigot/Bukkit,
  api-version 1.20) is downloaded and checksum-verified on the server
  (`/tmp/plugstage/PlaytimeLimit-1.0.0.jar`). Audited before install: plain
  Bukkit API only — no networking, no `Runtime`, no reflection; it does call
  `Player#kickPlayer`.
- Two open decisions: the daily/session minutes per weekday, and its
  **hardcoded `Asia/Shanghai` day boundary** (no `timezone` key in `config.yml`,
  so the daily reset lands at 13:00 São Paulo).
- Also ships `daily-first-login: give {player} minecraft:cookie 1`, which is on
  by default and was not asked for.

Shipped (see README "Changelog" for the details):

- **2026-10-13** — read-only web dashboard: <https://mc.example.com>
  (README "Dashboard", `dashboard/ARCHITECTURE.md`).
- **2026-10-16** — the dashboard shows server up/down (status card + measured
  availability, outage bands on the charts) and the charts follow a timespan
  (24 h / 7 / 30 / 90 days, plus a TPS panel). `mc-watchdog.timer` pushes an
  alert when the server goes down and another when it comes back.
