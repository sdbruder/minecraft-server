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

## Backups

> Status: the local archive + pruning job are in place. The Storage Box upload
> below still needs the box's host/username and one forced backup to confirm
> (see `upload.*` in the table).

Two independent things keep the world safe.

**AutoPlug's daily archive** (05:00 server time) is written to
`~/minecraft/autoplug/backups` and kept for `backup.max-days: 3` (~1.1 GB). Its
`exclude` list drops everything re-downloadable (`cache`, `libraries`,
`versions`, `server.jar`, `spigot.jar`, `AutoPlug-Files.zip`, `dashboard`), so
an archive is ~360 MB instead of ~890 MB. Config: `~/minecraft/autoplug/backup.yml`.

**Offsite copy:** AutoPlug uploads each archive to a Hetzner **Storage Box**
over SFTP. Note it has **no S3 support** — its uploader speaks FTPS/SFTP or
Google Drive only (verified against AutoPlug 10.2.0: the config has no
provider/endpoint/bucket keys, and the jar bundles no AWS SDK). Key auth, so no
password is stored anywhere.

| setting | value |
|---|---|
| `upload.enable` | `true` |
| `upload.host` / `upload.port` / `upload.username` | the Storage Box, `22`, `uXXXXX` |
| `upload.path` | `minecraft` |
| `upload.rsa-key-path` | `/home/steve/.ssh/id_rsa_storagebox` |
| `upload.delete-on-complete` | `false` — the local 3-day window stays |

A non-empty `rsa-key-path` is what makes AutoPlug use SFTP instead of FTPS. The
key pair is `~/.ssh/id_rsa_storagebox` (RSA 4096 — the `jsch` bundled in the
client predates ed25519); the public half is registered on the box.

**Pruning the remote:** AutoPlug prunes its local directory but never the
remote one, so `mc-backup-prune.timer` runs `scripts/backup-prune.sh` daily at
06:30 and deletes archives older than `SB_RETAIN_DAYS`. Dates come from the
file names (`YYYY-MM-DD-HH.MM-BACKUP.zip`), and only names matching that pattern
are ever touched.

```sh
scripts/backup-prune.sh --dry-run            # list what would be deleted
systemctl list-timers mc-backup-prune.timer
journalctl -u mc-backup-prune
```

Credentials for it live in `~/minecraft/storagebox.env` (mode 600, git-ignored):

```
SB_HOST=uXXXXX.your-storagebox.de
SB_USER=uXXXXX
SB_KEY=/home/steve/.ssh/id_rsa_storagebox
SB_PATH=minecraft
SB_RETAIN_DAYS=30
```

## Services (systemd)

They all run as `steve`, start on boot, and are defined in `systemd/` in this
repo (installed to `/etc/systemd/system/`).

| unit | what |
|---|---|
| `autoplug.service` | Runs AutoPlug, the Minecraft server wrapper. Replaces the old `screen` wrapper. |
| `mc-alerts.service` | The Pushover join alerts (successful and rejected). |
| `mc-dashboard.service` | The read-only web dashboard (behind nginx at `mc.example.com`). |
| `mc-backup-prune.timer` | Daily prune of old backup archives on the Storage Box. |

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
`logs/latest.log` and pushes **Pushover** notifications to your phone for every
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

**Pushover setup (one-time).** Pushover delivers instantly through Apple's APNs,
so alerts arrive in real time on iOS (unlike ntfy, whose iOS app can only poll
and is delayed).

1. Install the *Pushover* app on the iPhone/iPad (one-time $4.99 per platform;
   30-day free trial).
2. Register an application at <https://pushover.net/apps/build> (name it e.g.
   *Minecraft*); copy its **API Token**.
3. Copy your **User Key** from the Pushover dashboard.
4. Put both in `~/minecraft/mc-alerts.env` on the server (mode `600`):

   ```
   PUSHOVER_TOKEN=<app API token>
   PUSHOVER_USER=<user key>
   ```

5. `sudo systemctl restart mc-alerts`.

- Source: `scripts/alert-nonwhitelist.sh` (installed at `~/minecraft/scripts/`
  on the server, unit at `/etc/systemd/system/mc-alerts.service`).
- Credentials live in `~/minecraft/mc-alerts.env` (read by the unit via
  `EnvironmentFile`), never in this repo. The backup's `INCLUDES` list is
  explicit, so this file is not captured by `scripts/backup.sh`.
- Blocked joins send at priority *high* (1), joins at normal (0).
- Same name within 300 s is coalesced into one alert.
- Every blocked attempt is also appended to `~/minecraft/logs/blocked-joins.log`.
- Manage it: `sudo systemctl status|stop|restart mc-alerts`.

## Dashboard

Read-only metrics at **<https://mc.example.com>**. Sign in with the usual
username/password **form** (`/login`), so a password manager can save it — no
browser Basic-auth prompt. One page, refreshed every 15 s, with no JavaScript:

- players online (Java and Bedrock) plus a 24 h chart;
- play sessions, playtime and recent joins/leaves;
- rejected (non-whitelisted) joins: recent list, per-day chart, most frequent;
- TPS/MSPT, server uptime, MOTD, world + size, versions, plugins.

`GET /state.json` returns the same data as JSON; `GET /healthz` is the check
endpoint (no auth). Nothing is written to the game — the only RCON commands used
are `list`, `tps` and `mspt`.

**Auth.** Both login credentials live in `DASH_USER` / `DASH_PASS`. A successful
login sets `mc_session`: an HMAC-signed cookie (key derived from the password),
`HttpOnly`, `SameSite=Lax`, 30-day expiry, and `Secure` whenever the request
arrived over TLS. Changing the password invalidates every session. Log in
failures are throttled (10 per IP per 15 min). Scripting uses a cookie jar:

```sh
curl -s -c /tmp/mc.jar -o /dev/null -d "username=$DASH_USER&password=$DASH_PASS" \
     https://mc.example.com/login
curl -s -b /tmp/mc.jar https://mc.example.com/state.json
```

- Layout: `dashboard/static/app.css` overrides Pico's oversized defaults
  (Pico scales the root font up to 131.25% on wide screens and caps the content
  at 1450px). Type is pinned at 100% and the panels flow into **1–5 columns**:
  2 on a laptop, 4 at 1920, 5 at 3440 — the whole dashboard fits one screen.
- Source: `dashboard/` (`server.py`, `collect.py`, `store.py`, `render.py`);
  design notes in `dashboard/ARCHITECTURE.md`.
- Python 3 standard library only — no venv, no dependencies, no build step.
- History lives in `~/minecraft/dashboard.sqlite` (30 s samples, 90-day
  retention) and is disposable: delete the file to start over.
- Settings and credentials come from `~/minecraft/dashboard.env` (mode 600,
  git-ignored): `DASH_USER`, `DASH_PASS`, `DASH_PORT`, … The RCON password is
  read from `server.properties` and never leaves the server.
- Serving: nginx vhost `nginx/mc.example.com.conf` proxies to
  `127.0.0.1:8080` and reuses the `*.example.com` certificate.
- Manage it: `sudo systemctl status|restart mc-dashboard`.

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

### 2026-10-15 - Disk cleanup and leaner AutoPlug backups

- Freed **~5 GB** on the server (`/` went 20G → 16G used, 56% → 44%). The
  dominant cost was `autoplug/backups`: 7 daily zips of ~890 MB each, of which
  ~60% was re-downloadable content (libraries 189 MB, cache 167 MB, versions
  107 MB, `server.jar` 59 MB, `spigot.jar` 11 MB).
- `autoplug/backup.yml`: those paths (plus `./AutoPlug-Files.zip`, `./dashboard`)
  added to `exclude`, so a new archive holds only the world, plugins and configs
  (~360 MB, measured composition — pending confirmation at the next 05:00 run).
  `max-days` 7 → 3. Old file kept as `autoplug/backup.yml.pre-disk-cleanup.bak`.
- Existing archives pruned to the 3 newest: `autoplug/backups` 6.5 GB → 2.7 GB.
- Deleted stale/unused files, all re-downloadable or superseded:
  `cache/mojang_{1.21.1,26.3}.jar` (110 MB — Geyser re-fetches on demand;
  `mojang_26.2.jar` kept), `versions/{26.3,1.21.1}` (80 MB; `versions/26.2`
  kept), `plugins/Geyser-Spigot.jar.pre-1251.bak` (45 MB, superseded by the
  live jar), `server.jar` (61 MB — the server runs `java -jar paper.jar`, and
  `shared-files.yml` is `enable: false`), `spigot.jar` (11 MB, unreferenced),
  and AutoPlug's verbose logs for two older days (97 MB).
- Not touched: `família/` (the world), `paper.jar`, `AutoPlug-Client.jar`,
  `libraries/`, `autoplug/system/jre`, the live plugin jars.
- `autoplug` was restarted to load the new config (~30 s downtime, 0 players
  online at the time); verified back up with `Done (31.866s)` and a working
  RCON `list`.

### 2026-10-13 - Dashboard: dense, multi-column layout

- The page read oversized because Pico scales the root font with the viewport
  (up to **131.25%** at ≥1536px) and caps content at **1450px**, so a wide
  screen was mostly wasted. `dashboard/static/app.css` pins the type at 100%,
  tightens spacing, removes the width cap and flows the panels into **1–5
  columns** (`columns: 5 26rem`): 2 on a laptop, 4 at 1920, 5 at 3440. The full
  dashboard fits one screen (`1280x900` still scrolls a little).
- Split the two tallest panels (Sessions, Rejected joins) into five smaller
  cards, so the columns balance instead of leaving an empty column at ultrawide.
- Fixed the **Sign out** button stretching the full page width — Pico styles
  `[type=submit]` and that attribute selector outweighed the override — and
  gave the stylesheet URL a content hash so a CSS edit is not hidden behind the
  one-day cache.

### 2026-10-13 - Dashboard: sign-in form instead of HTTP Basic

- The dashboard now has a real login page (`/login`): labelled username and
  password fields with `autocomplete="username"` / `"current-password"` and a
  submit button, so password managers can save the credentials. HTTP Basic was
  never saved by them.
- Successful login sets `mc_session`, an HMAC-signed cookie (key derived from
  `DASH_PASS`, so changing the password signs everyone out): `HttpOnly`,
  `SameSite=Lax`, `Secure` behind TLS, 30-day expiry. A **Sign out** button was
  added to the page footer.
- Added: 10-per-IP/15-min login throttle, an `Origin`/`Host` check on POSTs,
  and same-site-only redirect targets.
- Fixed a real bug found while testing: `Referrer-Policy: no-referrer` made
  Chromium send `Origin: null` on form POSTs, which the CSRF check rejected
  (`403`). The policy is now `same-origin` — no referrer reaches third parties,
  but the `Origin` stays usable.

### 2026-10-13 - Read-only web dashboard at mc.example.com

- New `mc-dashboard.service`: a read-only page with players, sessions/playtime,
  rejected joins, TPS/MSPT + uptime, world/MOTD, versions and plugins, plus
  `GET /state.json` and `GET /healthz`.
- Python 3 standard library only (`dashboard/`), no framework and no client
  JavaScript; charts are inline SVG, styling is vendored Pico CSS (classless).
- History in SQLite (`~/minecraft/dashboard.sqlite`): a 30 s sampler diffs RCON
  `list` into join/leave events and ingests `blocked-joins.log`; 90-day
  retention.
- HTTP Basic auth (credentials in `~/minecraft/dashboard.env`). Exposed only
  through nginx on `mc.example.com` (vhost in `nginx/`), proxying
  `127.0.0.1:8080`.
- Design notes: `dashboard/ARCHITECTURE.md`.

### 2026-10-11 - Alerts: switch from ntfy to Pushover

- ntfy did not deliver in real time on iOS: the app cannot hold a socket and
  only polls (documented delays up to ~20 min). The server was publishing fine
  (HTTP 200), so the fix was the transport, not the script.
- `scripts/alert-nonwhitelist.sh` now sends through the Pushover API
  (`api.pushover.net/1/messages.json`), which pushes via APNs instantly.
  Blocked joins use priority 1 (high), joins priority 0.
- Credentials (`PUSHOVER_TOKEN`, `PUSHOVER_USER`) live in
  `~/minecraft/mc-alerts.env`, loaded by the unit via `EnvironmentFile`;
  `PUSHOVER_URL` is overridable for tests.

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
