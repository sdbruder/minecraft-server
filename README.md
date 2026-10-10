# Minecraft server

## Basic facts

There is a minecraft server managed by autoplug with a couple of plugins, some facts:

server: host.example.com
path: ~/minecraft

server is acessible without password with a key thru ssh.

## Server

- Software: Paper, managed by AutoPlug (screen session `AUTOPLUG`, start with `./start.sh`).
- Minecraft version: **26.2** (pinned).
- Plugins: Geyser-Spigot (Bedrock), floodgate, ViaVersion.
- World: `level-name=família`. The world is at `~/minecraft/família`.

## Local backup

`minecraft-essentials-2026-10-10.tar.gz` (208 MB) — a restartable snapshot of
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

## Alerts

A systemd service on the server (`mc-alerts.service`) follows
`logs/latest.log` and pushes an **ntfy** notification to your phone whenever a
non-whitelisted player tries to join. Paper writes the trigger line:

```
[20:20:10] [Server thread/INFO]: Disconnecting Alex (/203.0.113.10:55658): You are not whitelisted on this server!
```

Scanners like `FishNet` disconnect during the handshake, before the whitelist
check, so they never match and are ignored.

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

## Changelog

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
