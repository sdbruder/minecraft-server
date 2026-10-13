# Tasks

## Backlog

### Web dashboard for server metrics

Status: design proposal — see `dashboard/ARCHITECTURE.md`. Awaiting the two
open decisions (exposure, optional panels) before implementation.

Run a small, read-only web dashboard on a **non-80 port** (e.g. `8080`) so the
server metrics stay visible at a glance.

Show:
- Connected players: count and the list of names (Java and Bedrock).
- Rejected players: names and IPs of blocked (non-whitelisted) join attempts.
- Versions running: Minecraft/Paper, Java, and every plugin with its version.
- Optional: uptime, TPS/MSPT, world name, MOTD.

Data sources (read-only, no new credentials needed):
- Players: RCON `list` on `127.0.0.1:25575` (password in `server.properties`),
  or parse `joined the game` / `left the game` in `logs/latest.log`.
- Rejected players: `logs/blocked-joins.log` (written by
  `scripts/alert-nonwhitelist.sh`), plus the `You are not whitelisted` lines in
  the log.
- Versions: head of `logs/latest.log` (Paper, Minecraft, Java), the Geyser
  `Loading Geyser version` line, and `plugin.yml` inside each `plugins/*.jar`.
- MOTD / player count: the query protocol or Geyser ping passthrough.

Constraints:
- Listen on a non-80 port, on `0.0.0.0` or behind the existing nginx.
- Run as a systemd unit, same style as `systemd/autoplug.service`, user
  `steve`.
- Read-only. Do not expose the AutoPlug server key or the RCON password to the
  browser. Player names are shown, so add basic auth.
- Keep the code in this repo (e.g. `dashboard/`) and add a unit in `systemd/`.
