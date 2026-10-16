# Tasks

## Backlog

_Empty._

Shipped (see README "Changelog" for the details):

- **2026-10-13** — read-only web dashboard: <https://mc.example.com>
  (README "Dashboard", `dashboard/ARCHITECTURE.md`).
- **2026-10-16** — the dashboard shows server up/down (status card + measured
  availability, outage bands on the charts) and the charts follow a timespan
  (24 h / 7 / 30 / 90 days, plus a TPS panel). `mc-watchdog.timer` pushes an
  alert when the server goes down and another when it comes back.
