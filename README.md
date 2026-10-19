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

Two independent things keep the world safe.

**AutoPlug's daily archive** (05:00 server time) is written to
`~/minecraft/autoplug/backups` and kept for `backup.max-days: 3` (~1.1 GB). Its
`exclude` list drops everything re-downloadable (`cache`, `libraries`,
`versions`, `server.jar`, `spigot.jar`, `AutoPlug-Files.zip`, `dashboard`), so
an archive is ~314 MB instead of ~890 MB (measured: `2026-10-16-15.10-BACKUP.zip`
= 328,763,224 bytes). Config: `~/minecraft/autoplug/backup.yml`.

**Offsite copy:** AutoPlug uploads each archive to a Hetzner **Storage Box**
over SFTP. Note it has **no S3 support** — its uploader speaks FTPS/SFTP or
Google Drive only (verified against AutoPlug 10.2.0: the config has no
provider/endpoint/bucket keys, and the jar bundles no AWS SDK). Key auth, so no
password is stored anywhere.

| setting | value |
|---|---|
| `upload.enable` | `true` |
| `upload.host` | `u123456.your-storagebox.de` (`198.51.100.9`) |
| `upload.port` / `upload.username` | `22` / `u123456` |
| `upload.path` | `minecraft` |
| `upload.rsa-key-path` | `/home/steve/.ssh/id_rsa_storagebox` |
| `upload.delete-on-complete` | `false` — the local 3-day window stays |

A non-empty `rsa-key-path` is what makes AutoPlug use SFTP instead of FTPS. The
key pair is `~/.ssh/id_rsa_storagebox` (RSA 4096 — the `jsch` bundled in the
client predates ed25519); the public half is registered on the box. AutoPlug
will not create the remote folder, so `minecraft/` was made once with `sftp`.

AutoPlug watches `backup.yml` for changes (`[BackupConfig] Listening for
changes…`), but it also rewrites the file on load, so comments added by hand
there do not survive — this README is the documentation.

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
SB_HOST=u123456.your-storagebox.de
SB_USER=u123456
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
| `mc-watchdog.timer` | Probes the server once a minute; alerts when it goes down, and again when it comes back. |
| `mc-restart.path` | Turns the dashboard's Restart button into `systemctl restart autoplug` (the web app has no privileges of its own). |
| `mc-restart-when-empty.path` | Turns "Restart when empty" into a queue that restarts once nobody is playing (`scripts/restart-when-empty.sh`). |
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
join and leave: `Joined: <name>`, `Left: <name>`, and a rejected,
non-whitelisted attempt (`Blocked join: <name>`).

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

A **leave** comes from Paper's `left the game` line, in either shape:

```
[Server thread/INFO]: Steve left the game
[Server thread/INFO]: System chat: Steve left the game     # chat-plugin rewrite
```

`NAME lost connection: Disconnected` is deliberately *not* used: scanners that
never joined (FishNet and friends — 113 such lines in the current logs) emit it
too, and a real quit usually logs both lines, so it would alert on bots and
double-alert on players.

The leave alert reports the session length when the matching join was seen:
`Left: Steve` — `Steve (java) left the server at 20:22:00 UTC after 1h 12m`.
That is per-process memory, so a leave for somebody who joined before a restart
just omits the duration.

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
- Blocked joins send at priority *high* (1); joins and leaves at normal (0).
- Repeated events for the same name within 300 s are coalesced — separately for
  joins and for leaves, so a leave right after a join still alerts.
- Every blocked attempt is also appended to `~/minecraft/logs/blocked-joins.log`.
- Manage it: `sudo systemctl status|stop|restart mc-alerts`.

### Server down / back up

`mc-watchdog.timer` runs `scripts/watch-server.sh` once a minute. A probe counts
as **up** only when both parts pass: `systemctl is-active autoplug` **and** a TCP
connect to the RCON port (read from `server.properties`), so a crash, a failed
start and a hung-but-listening server are all caught.

- Three consecutive failures are required before `Server down` (priority 1), so
  the daily 05:00 restart and AutoPlug self-updates (30-60 s) stay silent.
- Alerts fire on **transitions only**: one `Server down`, then one `Server up`
  with the outage length — `Minecraft server is up at 15:47:43 UTC after 1h 12m
  (down since 14:35:43 UTC)`.
- The message names what failed: `unit autoplug not active`, `RCON port 25575
  unreachable`, or both.
- State lives in `~/minecraft/server-watch.state`. The script always exits 0,
  also while the server is down, so a *failed* run of the unit means the script
  itself broke. Credentials come from the same `mc-alerts.env`.
- Verified with a synthetic matrix: 3 failures → exactly one alert; more
  failures → silence; recovery → one alert with the duration; a 500 from
  Pushover, or missing credentials → a warning on stderr and exit 0.

```sh
systemctl list-timers mc-watchdog.timer
sudo systemctl start mc-watchdog.service    # one probe now
journalctl -u mc-watchdog -n 20
```

## Dashboard

Read-only metrics at **<https://mc.example.com>**. Sign in with the usual
username/password **form** (`/login`), so a password manager can save it — no
browser Basic-auth prompt. One page, refreshed every 15 s, with no JavaScript:

- **is the server up?** — a full-width status card first: up / starting / not
  responding / down, with the `autoplug` unit state, the Paper pid, the uptime,
  the player count and the share of the window that was up;
- **two restart buttons in the footer row**, next to Sign out, and the page's
  only write actions: **Restart now** (red — disconnects everyone) and
  **Restart when empty** (orange — waits for the last player to leave, up to
  12 h, so nobody is kicked). While a queue is waiting the orange button becomes
  **Cancel restart** and the red one is disabled. Each only drops a trigger file
  (`restart.request` / `restart-when-empty.request`) which the matching
  `mc-restart*.path` unit turns into `systemctl restart autoplug` as root: the
  web app keeps `NoNewPrivileges`, no sudo and no systemd access;
- players online (Java and Bedrock) and charts of peak players and TPS;
- charts follow a timespan — **24 h / 7 / 30 / 90 days** (`?span=…` links, kept
  across the 15 s refresh); outages are drawn as red bands, and a gap means "no
  samples" (the dashboard was not running);
- play sessions, playtime and recent joins/leaves (all following the span);
- rejected (non-whitelisted) joins: recent list, per-bucket chart, most frequent;
- TPS/MSPT, server uptime, MOTD, world + size, versions, plugins.

Availability is *measured*, not guessed: the 30 s sampler writes a row on every
tick and marks `up = 0` when RCON does not answer. That flag only exists from
2026-10-16 onwards — older outages show as gaps rather than bands.

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

### Java (java-updater is off, on purpose)

```yaml
updater:
  java-updater:
    enable: false
    version: 25     # informational only while the updater is disabled
```

While `java-updater` is enabled, AutoPlug refuses to start the server unless it
finds *its own* managed JRE (one it installed under `autoplug/system/jre/`)
matching the configured major version:

```
[WARN] Failed to start server: Java-Updater is enabled, but Java-Installation was not found!
       com.osiris.autoplug.client.Server.createProcess(Server.java:234)
```

On 2026-10-16 the updater ran during a restart with `version: 21` while the
managed JRE is Temurin **25** (`jdk-25.0.4+7`). After that run the start check
could not be satisfied at all, so the server stayed down through two restarts
(Paper's last log line was a shutdown at 15:10:21). With the updater disabled the
check does not apply, and `general.yml`'s
`start-command: java -jar "/home/steve/minecraft/paper.jar"` runs the **system**
JDK (`/usr/lib/jvm/java-25-openjdk-arm64`, OpenJDK 25.0.4.1) that apt maintains.

Do not re-enable it without first making `version` agree with what is installed
in `autoplug/system/jre/`; `autoplug/system/jre/` (303 MB) is unused while it is
off. Config backup: `autoplug/updater.yml.pre-java-version-fix.bak`.

## Backlog

See `TASKS.md` (empty as of 2026-10-16 — the dashboard shipped).

## Changelog

### 2026-10-19 - Dashboard: the restart buttons move to the footer row

- Two buttons instead of one: **Restart now** (red) and **Restart when empty**
  (orange), each acting on a single click. The confirmation card added earlier
  today is gone — it hid the choice behind an extra step, and the colours plus
  the explicit labels carry the warning instead.
- They share the footer row with **Sign out** (caption left, buttons right,
  wrapping on a narrow screen). While a "when empty" is queued the orange button
  becomes **Cancel restart** and the red one is disabled, so the page never
  offers an action the backend would refuse; the banner keeps the state text
  (`restart queued for when the server empties — 2 online, queued … UTC`).
- Colours join the shared palette as `--mc-restart-now` / `--mc-restart-wait`,
  with light and dark values chosen so white button text stays readable.
- `POST /restart` without a valid `mode` now answers a plain `400` (nothing
  renders that card any more) and the `409` cases answer in plain text.
- Verified in a browser against a copy of the live database: three buttons on one
  row (`sameRow`), red `rgb(218,54,51)` / orange `rgb(189,86,29)` / Sign out, no
  buttons left in the status card; clicking *when empty* flips to
  `Restart now [disabled] + Cancel restart` with the queue note; cancel returns
  to idle. Then on the server: the footer markup as served, `400` for a
  missing/bogus `mode`, and `mode=now` → `302` with `mc-restart.service`
  reporting `Result=success`.
- Fix found on the first look at the page: the two restart buttons were glued
  together. The `gap` sat on the wrapper `<div>`, which only separates the two
  *forms*; the buttons inside one form stayed flush. Each form is now its own
  flex row with the gap — measured 6.3 px and 6.4 px between all three buttons.

### 2026-10-19 - Dashboard: "Restart when empty", a queue you can cancel

- The confirmation card now offers two plain submit buttons (still no
  JavaScript; `mode` travels in the POST body):
  - **Restart when empty** queues the restart and waits for the last player to
    leave, so nobody is kicked — the option to use while the kids are playing;
  - **Restart now** is the previous behaviour, offered alone when nobody is on.
- Same no-privilege pattern as the immediate restart: the dashboard writes
  `~/minecraft/restart-when-empty.request`, `mc-restart-when-empty.path` fires
  `mc-restart-when-empty.service` (root), and `scripts/restart-when-empty.sh`
  polls RCON `list` every 30 s. It also acts when the server has been *dead* for
  three polls in a row (nobody can be playing) and gives up after `MAX_WAIT`
  (12 h) without touching anything.
- The queue is the script's state file, so the banner shows
  `restart queued for when the server empties — 2 online, queued 19:55:14 UTC`
  with a **Cancel restart** button; cancelling deletes the state file *and* the
  not-yet-picked-up request file, so a cancelled queue cannot fire later.
- Added `scripts/rcon-list.py`, the shell-usable RCON client the repo lacked:
  prints the count, or `unknown` with exit 2, so callers can tell "nobody is
  playing" from "no idea".
- Bugs found and fixed while testing, each of which would have shipped broken:
  the "already queued" check ignored the request file before the waiter picked
  it up (so a `now` restart could be queued next to it); **cancel left the
  request file behind**; and the service unit had no `HOME`, so the script's
  `$HOME/minecraft/…` defaults became `/minecraft/…` — every run failed with
  exit 1, the request file stayed, and the path unit retriggered until systemd's
  start limit marked it failed. That last one is why the route then answered
  `409`: the leftover file made the guard believe a queue was pending.
- Evidence: against a fake RCON — waits while 2 players, restarts when they
  leave, cancels on demand, gives up after `MAX_WAIT`, acts on a dead unit,
  refuses a second queue with `409`. Then end to end on the live server with
  nobody online: `POST mode=empty` → `302`, the request was consumed, the
  service logged `empty -> restarting` / `restart command finished`, and Paper
  came back (`Done (32.700s)`) with PlaytimeLimit, Geyser b1254 and ViaVersion
  5.12.1 enabled and no queue files left behind.

### 2026-10-19 - Dashboard: Restart button, without privilege in the web app

- The page was read-only by design; a restart is now its single write action.
  `POST /restart` renders a confirmation card that names the players who would
  be disconnected (the page has no JavaScript, so no dialog), and
  `POST /restart?confirm=1` writes `~/minecraft/restart.request` — the only file
  the app writes outside its SQLite database.
- Privilege stays out of the web app: `systemd/mc-restart.path`
  (`PathExists=`) fires `mc-restart.service`, which removes the trigger and runs
  `systemctl restart autoplug` as root — exactly what an operator would type.
  The dashboard keeps `NoNewPrivileges=yes`, no sudo and no bus access. RCON
  `stop` was rejected: it depends on AutoPlug exiting by itself, and if that
  assumption were wrong the restart button would become a shutdown button.
- Guards: session cookie required (`302` to `/login?next=/` otherwise), the
  usual `Origin`/`Host` check (`403` cross-site), `409` while a request is
  queued or the server is starting, and the banner swaps the button for
  `restart requested …` / `a restart is in progress…`.
- Two bugs caught while testing: the "already queued" check *and* the banner
  both read the 5 s live cache, so a double-click slipped past the check and the
  button stayed visible. The check now stats the trigger file itself, and a
  queued request drops the cache so the redirect already shows the pending line.
- Evidence: unauthenticated POST → `302 /login?next=/`; confirm card lists the
  players; cross-origin POST → `403`; confirm → `302 /` + the file; second
  confirm → `409`; banner shows `restart requested … UTC`. Then end to end
  through the real trigger: the path unit consumed the file in under 3 s,
  `mc-restart.service` finished `Result=success`, and `autoplug` came back with
  a new Paper pid (`ActiveEnterTimestamp` moved to 19:42:46), the whole plugin
  set re-enabled (PlaytimeLimit 1.0.0, Geyser b1254, ViaVersion 5.12.1) and RCON
  answering — about 60 s from the click.

### 2026-10-17 - PlaytimeLimit installed (day boundary patched to local time)

- Modrinth **PlaytimeLimit** 1.0.0 (`DMhofPzM`, Apache-2.0, bukkit/paper/spigot,
  `api-version: 1.20`, sha512 `b6d48f351fb4…005d`) downloaded,
  checksum-verified and audited before it went near the live server: plain
  Bukkit API only (join/quit listeners, `BukkitRunnable`,
  `YamlConfiguration`, `Player#kickPlayer`) — no networking, no `Runtime`, no
  reflection; Java 17 bytecode, fine on Java 25.
- Limits as shipped and as chosen: **60 min/day Mon–Fri, 120 Sat–Sun, 60-min
  sessions with a 10-min forced break**, warnings at 15/5/1 min, and the daily
  first-login `give {player} minecraft:cookie 1` kept on.
- Bug found by inspection: its day boundary is a hardcoded
  `ZoneId.of("Asia/Shanghai")` in three classes, with no `timezone` key in the
  config — so the daily reset would land at **13:00 in Sao Paulo** and an
  evening would count into the next local day. `America/Rio_de_Janeiro` is not
  in this JDK's tzdb (`ZoneRulesException`; no such file in
  `/usr/share/zoneinfo` either), so the constant was patched to
  **`America/Sao_Paulo`** (UTC-3, no DST — verified accepted by the very JDK the
  server runs: offset `-03:00`, local date `2026-10-17` while Shanghai said
  `2026-10-18`).
- `scripts/patch-jar-zone.py` rewrites the `CONSTANT_Utf8` entry in place: 3
  constants, jar 20696 → 20707 bytes, `javap` parses all four classes
  afterwards, zip integrity OK, and no `Asia/Shanghai` remains. The pristine
  jar is kept as `/tmp/plugstage/PlaytimeLimit-1.0.0.jar.orig` (sha256
  `da8ee95e…`), so the change stays reproducible and reviewable.
  **Consequence: the installed jar no longer matches the upstream hash.**
- Installation waits for an empty server (3 players were on at 21:18). The
  watcher then restarts and verifies the enable line, the generated
  `plugins/PlaytimeLimit/config.yml` and a `/playtimelimit reload` round trip
  over RCON; if the plugin did not enable it removes the jar and reports —
  Paper disables a broken plugin rather than crashing, and a disabled plugin
  changes nothing at runtime, so no second restart is needed.

### 2026-10-16 - Dashboard: the player chart now says "so far"

- The chart plots the **peak per bucket**, so its newest point is the busiest
  moment of the current 30-min bucket — not the live count. At 16:06 the panel
  read "1 online" while the last point read 2, because two players had been on
  until 16:06:10 (30 ticks: fourteen at 2, sixteen at 1 → peak 2, average 1.43).
  Both numbers were right, and the peak is what preserves a short visit (an
  average flattens it, "the last value" hides a 20-minute session entirely).
- Made explicit rather than changing the data: the caption leads with the live
  figure (`1 online now · line: peak 2, average 0.7 per 30 min`), and the
  first/last bucket tooltips say `(so far)` / `(partial)` since those buckets
  are only partly inside the window.

### 2026-10-16 - Dashboard: timespan switch moved to the title band

- The 24 h / 7 / 30 / 90 d switch sat inside the server-status card, where it
  read as part of the uptime story. It now sits on the right of the title band
  (`body.dash > header` became a wrapping flex row) and drops to its own line on
  a phone. Same `?span=` links, no behaviour change.

### 2026-10-16 - Dashboard: up/down status, outage-aware charts, timespans

- The page now answers "is the server up?" first: a full-width card with the
  state (up / starting / not responding / down), the `autoplug` unit state, the
  Paper pid, the uptime, players, and how much of the selected window was up.
  A working RCON is the only proof of *up*; the unit state, the pid and the
  process age explain why not. Tones come from `data-status` in the CSS.
- The 30 s sampler now writes a row on **every** tick, `up = 0` when RCON does
  not answer (it used to skip the tick). Outages are therefore in the history:
  they draw as red bands on the charts and count against the availability
  figure. `samples.up` was added by a migration (`DEFAULT 1` — correct for the
  rows written before it, which only existed while up). Outages predating the
  column show as gaps, not bands.
- The bar-per-index charts were replaced by one time-axis engine: points are
  placed by timestamp, so a missing bucket is a gap and never a fake zero;
  y gridlines use 1/2/5×10ⁿ steps (the TPS chart no longer labels 18.69); four
  x labels adapt to the window; hovering reads values through SVG `<title>`.
  Still no JavaScript, no CDN, no build step.
- Every chart follows a timespan: **24 h / 7 / 30 / 90 days** via `?span=`
  links (kept by the meta refresh), with buckets from 30 min to 1 day
  (48-168 points) and a ~20 s per-span cache. `/state.json?span=30d` exposes the
  same window to scripts. Added a **TPS chart** (sampled since day one, never
  plotted) and "Most played" now follows the span instead of being fixed at 7 d.
- `scripts/watch-server.sh` + `mc-watchdog.timer`: a once-a-minute probe that
  pushes `Server down` (priority 1) after three consecutive failures, and
  `Server up` with the outage length on recovery. See "Alerts".
- Verified in a browser against a copy of the live database: up and down states,
  both outage bands, all four spans and the axis/tooltip output; then deployed
  and re-checked on the server (`/healthz`, the migrated column, a fresh sample,
  all four spans rendering from the real history).

### 2026-10-16 - Plugin update staged (ViaVersion 5.12.1, Geyser build 1254)

- Downloaded and checksum-verified, staged in `/tmp/plugstage` on the server:
  Geyser-Spigot 2.11.3 build **1254** (sha256 matches the GeyserMC API) and
  ViaVersion **5.12.1** (sha512 matches Modrinth), replacing build 1251 and
  5.12.0. Both are drop-in swaps; the old jars are kept as
  `plugins/Geyser-Spigot.jar.pre-1251.bak` and
  `plugins/ViaVersion-LATEST-[5.11.0].jar.pre-5.12.0.bak`.
- The swap waits for an **empty server**. The first attempt (15:42, server had
  just emptied) rolled both jars back on a false failure: its "is it up?" check
  read the *previous* session's log — Paper rotates `latest.log` ~19 s into a
  boot, so `grep 'Done ('` matched the old boot — and the plugin check then ran
  3 s into the fresh session, before Geyser had logged its version. The server
  itself was never broken; it cost two extra restarts and left `plugins/` on
  1251 / 5.12.0.
- The replacement watcher waits for a **new log session** *and* a working RCON
  before judging anything, then requires the Geyser and ViaVersion lines in the
  fresh log, and rolls back only on a real failure.
- **Applied** the same evening: the server emptied at 18:02:40, the watcher
  installed both jars, restarted, and verified in the fresh session
  (`geyser: Loading Geyser version 2.11.3-b1254`, `via: ViaVersion (5.12.1)`,
  RCON answering, no load errors) at 18:03:37. The 05:00 restart then confirmed
  it under AutoPlug's own start. Geyser rewrote its `config.yml` on first start
  (its 1254 changelog mentions config cleanup, and new keys such as
  `validate-bedrock-login` / `use-waterdogpe-forwarding` appear) — the watcher
  diffed it against the pre-upgrade file.
- Note also that AutoPlug moved `paper.jar` to **26.2 build 134** at the 05:00
  restart; the Geyser pin in "Version pin" still applies to 26.3.

### 2026-10-16 - Server outage: AutoPlug's java-updater vs the installed JRE

- The server went down at 15:10:21 and did not come back through two restarts.
  Paper logged its shutdown, then AutoPlug refused to start it:
  `Failed to start server: Java-Updater is enabled, but Java-Installation was
  not found!` (`Server.createProcess:234`).
- Cause: `updater.yml` had `java-updater.version: 21` while the managed JRE is
  Temurin **25** (`autoplug/system/jre/jdk-25.0.4+7`). The updater ran during the
  15:09 restart and left the managed installation in a state its own start-check
  rejected. The JRE itself was intact (`bin/java` present, runs `java -version`);
  nothing was deleted by the disk cleanup.
- Fix: `java-updater.enable: false`. AutoPlug then started the server through
  `start-command: java -jar "/home/steve/minecraft/paper.jar"` using the system
  JDK (`/usr/lib/jvm/java-25-openjdk-arm64`, OpenJDK 25.0.4.1). Up in ~25 s,
  RCON answering, all services active. Config backup:
  `autoplug/updater.yml.pre-java-version-fix.bak`.
- This mattered beyond the outage: with the updater enabled the same check runs
  after the daily 05:00 restart, so the server could have stayed down overnight.
  The 05:00 backup that morning followed by a manual start at 06:28 looks like
  this bug firing once already.

### 2026-10-16 - Offsite backups to a Hetzner Storage Box

- AutoPlug now uploads every archive to `u123456.your-storagebox.de` over SFTP
  (port 22, key auth, `path: minecraft`, `delete-on-complete: false` so the
  local 3-day window stays). AutoPlug 10.2.0 has no S3 support — FTPS/SFTP or
  Google Drive only — so the Storage Box is its native fit.
- `mc-backup-prune.timer` (daily 06:30 UTC, enabled) keeps the remote at 30 days
  with `scripts/backup-prune.sh`; AutoPlug never prunes the remote side itself.
- Verified end to end: an AutoPlug restart triggered a real backup (314 MB) and
  the upload completed in ~5 s (`[OK][BackupTask] … Completed backup & upload`),
  leaving 328,763,224 bytes on the box. A decoy `2026-08-01-…-BACKUP.zip` was
  then pruned through the systemd unit while the real archive was left alone.
- The box's hostname did not resolve at first (NXDOMAIN from the server and from
  1.1.1.1 while authoritative servers answered): negative DNS caching, cleared
  with `resolvectl flush-caches`.

### 2026-10-16 - Alerts: also notify on leaves

- `scripts/alert-nonwhitelist.sh` now sends `Left: <name>` next to `Joined:`
  and `Blocked join:`. The trigger is Paper's `left the game` line, in both
  shapes (plain and the `System chat:` chat-plugin rewrite).
- `NAME lost connection: Disconnected` is deliberately ignored: scanners that
  never joined (FishNet) emit it too, and a real quit usually logs both lines.
  Evidence from the live logs: 122 `left the game` lines vs 575 `lost
  connection` lines, of which 113 are FishNet.
- Joins and leaves keep separate per-name cooldowns, so a leave right after a
  join is never swallowed by the join.
- The leave alert reports the session length when the join was seen
  (`… left the server at 00:00:03 UTC after 3s`). Tracking happens even when the
  join alert itself was coalesced, and is dropped once the player leaves; after
  a restart the duration is simply omitted.
- Verified: 9/9 on a synthetic alert matrix, 7/7 on the duration behaviour
  (incl. `format_duration` boundaries), and a replay of all ~59.8k lines of the
  server logs — 124 join alerts = 124 entity lines, 122 leave alerts = 122
  `left the game` lines, 8 blocked alerts = 8 rejection lines, zero non-200s.

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
