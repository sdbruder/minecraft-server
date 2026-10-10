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
- World: `level-name=world` (default name). The world is at `~/minecraft/world`.

## Local backup

`minecraft-essentials-2026-10-10.tar.gz` (209 MB) — a restartable snapshot of
`~/minecraft`: `AutoPlug-Client.jar`, `paper.jar`, `start.sh`, the configs,
`world/`, `plugins/`, `logs/` and AutoPlug `.yml`. Enough to bring the server
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
