# Minecraft server

## Basic facts

There is a minecraft server managed by autoplug with a couple of plugins, some facts:

server: host.example.com
path: ~/minecraft

server is acessible without password with a key thru ssh.

## Server

- Software: Paper, managed by AutoPlug under **systemd** (`autoplug.service`).
- Minecraft version: **26.2** (pinned).
- Plugins: Geyser-Spigot (Bedrock), floodgate, ViaVersion.
- World: `level-name=família`. The world is at `~/minecraft/família`.

## Local backup

`minecraft-essentials-2026-10-10.tar.gz` (224 MB) — a restartable snapshot of
`~/minecraft`: `AutoPlug-Client.jar`, `paper.jar`, `start.sh`, the configs,
`família/`, `plugins/`, `logs/` and AutoPlug `.yml`. Enough to bring the server
up again: AutoPlug re-fetches Java and Paper re-fetches `libraries/`/`cache/`
on first start. Excluded: `cache/`, `libraries/`, `versions/`,
`autoplug/system/jre/`, `autoplug/backups/` and `plugins/.paper-remapped/`.

Re-create it any time with one call:

```sh
scripts/backup.sh
```

It streams a `tar.gz` from the remote host into the repo root as
`minecraft-essentials-<date>.tar.gz`. Override with `REMOTE`, `REMOTE_DIR`,
`OUT_DIR` or `STAMP` env vars, or pass the host/dir as arguments.

## Services (systemd)

Both run as `steve`, start on boot, and are defined in `systemd/` in this repo
(installed to `/etc/systemd/system/`).

| unit | what |
|---|---|
| `autoplug.service` | Runs AutoPlug, the Minecraft server wrapper. Replaces the old `screen` wrapper. |
| `mc-alerts.service` | The ntfy join alerts (successful and rejected). |

```sh
sudo systemctl start|stop|restart autoplug
systemctl status autoplug
journalctl -u autoplug -f      # AutoPlug console + server output
```

Notes:
- `sudo systemctl stop autoplug` stops Paper gracefully (worlds saved) before
  AutoPlug exits.
- `KillMode=mixed` so only AutoPlug gets SIGTERM and it stops the server itself;
  `SuccessExitStatus=143` accepts AutoPlug's SIGTERM exit code.
- AutoPlug's self-update restart is handled by `Restart=always` (that is why
  `screen` was used before).
- `/etc/systemd/system/minecraft.service` (a direct-Paper unit that would race
  AutoPlug at boot) is **disabled**, as is the broken user unit
  `~/.config/systemd/user/AutoPlug.service`.
- Old screen launcher kept at `~/minecraft/start.sh.screen.bak`.

## Alerts

A systemd service on the server (`mc-alerts.service`) follows
`logs/latest.log` and pushes **ntfy** notifications to your phone for every
join: a successful one (`Joined: <name>`) and a rejected, non-whitelisted one
(`Blocked join: <name>`).

A successful join is taken from the entity line, which carries name and IP and
is written once per join for **Java and Bedrock** players alike:

```
[Server thread/INFO]: Steve[/203.0.113.11:12733] logged in with entity id 78 at (...)
```

This line is preferred over the other Java-side entries because they are
unreliable: `Steve joined the game` is sometimes rewritten to
`System chat: Steve joined the game` by a chat plugin (and has no IP), and
`[User Authenticator #N/INFO]: UUID of player Steve is ...` also fires for
auth attempts that the whitelist then rejects.

A rejected join comes from the whitelist rejection line Paper writes:

```
[20:20:10] [Server thread/INFO]: Disconnecting Alex (/203.0.113.10:55658): You are not whitelisted on this server!
```

Scanners like `FishNet` disconnect during the handshake, before the whitelist
check, so they never match and are ignored.

**Bedrock (Geyser) players** log differently. On a successful join Floodgate
prefixes the username with `.` (its `username-prefix`); the script strips the
dot and marks the alert `(bedrock)`. On a rejection the Java-side line has no
username, only the IP, and the username appears on a Geyser line. Both are
handled:

```
[Server thread/INFO]: Disconnecting /203.0.113.13:0: You are not whitelisted on this server!        # IP only
[Geyser-Spigot] Kai has disconnected from the Java server because of §rYou are not whitelisted on this server!   # username
```

The script pairs them (IP from the Java line, name from the Geyser line) and
reports it as e.g. `Blocked join: Kai` — `203.0.113.13 (bedrock)`.

**Subscribe on your phone:** install the *ntfy* app (iOS/Android) and add
topic **`mc-familia-REDACTED`** (or open `https://ntfy.sh/mc-familia-REDACTED`
in a browser). Anyone who knows a topic can read it, so keep it private.

- Source: `scripts/alert-nonwhitelist.sh` (installed at `~/minecraft/scripts/`
  on the server, unit at `/etc/systemd/system/mc-alerts.service`).
- Same name within 300 s is coalesced into one alert.
- Every blocked attempt is also appended to `~/minecraft/logs/blocked-joins.log`.
- Manage it: `sudo systemctl status|stop|restart mc-alerts`.

## Version pin (important)

AutoPlug updates the server jar automatically. The target version is set in
`autoplug/updater.yml`:

```yaml
updater:
  server-updater:
    version: 26.2
```

Keep this value at **26.2** or lower. Do not raise it to 26.3: Geyser-Spigot
2.11.3 fails on Paper 26.3 (`Couldn't find asBukkitCopy or asCraftMirror
method on CraftItemStack`). Raise it only after a Geyser build that supports
the newer version is confirmed.

`autoplug/updater.yml` also drives the plugin checks: `plugins-updater.version`
is empty, so it inherits `server-updater.version` (26.2).

## Backlog

See `TASKS.md`. Next up: a read-only web dashboard for server metrics
(connected players, rejected joins, versions, plugins) on a non-80 port.

## Changelog

### 2026-10-11 - Alerts: also notify on successful joins

- `scripts/alert-nonwhitelist.sh` now pushes an ntfy alert (`Joined: <name>`)
  when a player joins, next to the existing `Blocked join:` alert. The trigger
  is the `NAME[/IP:PORT] logged in with entity id ...` line, which covers Java
  and Bedrock players and carries the IP.
- Bedrock names (Floodgate `username-prefix: "."`) are shown without the dot and
  tagged `(bedrock)`.
- Join alerts use their own 300 s per-name cooldown, so they do not suppress
  blocked-join alerts for the same name.

### 2026-10-10 - Alerts: handle Bedrock (Geyser) rejections

- The non-whitelist alert now also matches Bedrock joins. The Java-side line
  has no username (`Disconnecting /<ip>:0: ...`), so the script reads the name
  from Geyser's line and the IP from the Java line. Previously it fired with a
  garbage name (`/203.0.113.13:0: You are not whitelisted...`) or missed it.
- Added a username sanity check so malformed lines cannot produce junk alerts.

### 2026-10-10 - Run AutoPlug under systemd (drop screen)

- Added `systemd/autoplug.service`; AutoPlug runs as a systemd service instead
  of inside `screen`. Self-updater restarts are handled by `Restart=always`.
- Disabled `/etc/systemd/system/minecraft.service` (direct-Paper unit that
  would race AutoPlug at boot) and the broken user unit
  `~/.config/systemd/user/AutoPlug.service`.
- `autoplug/general.yml` `start-on-boot: false` (boot is owned by systemd).
- Captured `systemd/mc-alerts.service` in the repo.
- `~/minecraft/start.sh` now just calls `systemctl start autoplug`.

### 2026-10-10 - Rename world to família

- Renamed the world folder `world` -> `família` and set
  `server.properties` `level-name=família`.
- Regenerated the local backup snapshot.

### 2026-10-10 - Downgrade to Minecraft 26.2

Reason: Geyser-Spigot 2.11.3 does not work on Paper 26.3.

- Removed the defined world: `level-name` changed from `Família` to `world`.
  A 26.3 world cannot be loaded by 26.2. The old world was renamed to
  `~/minecraft/Família-26.3-old` and later deleted.
- Downgraded the server jar: `paper.jar` is now Paper 26.2 build 133
  (sha256 `bf1dcf62...c9ff1`). Also stored at `versions/26.2/paper-26.2.jar`.
- Pinned AutoPlug: `autoplug/updater.yml` `server-updater.version: 26.2`
  (was `26.3`).
- Backups of the old files: `server.properties.pre-26.2-downgrade.bak` and
  `autoplug/updater.yml.pre-26.2-downgrade.bak`.

### 2026-10-10 - Geyser-Spigot update

- Updated `plugins/Geyser-Spigot.jar` from build `1247` to build `1251`
  (both version 2.11.3). Backup: `plugins/Geyser-Spigot.jar.pre-1251.bak`.
- Deleted the old world `Família-26.3-old` (44 MB).
