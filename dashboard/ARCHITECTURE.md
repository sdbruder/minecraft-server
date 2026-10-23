# Dashboard — architecture

Read-only web page for the Minecraft server, with a short history of players and
rejected joins. This is the agreed design; it was implemented on 2026-10-13
(see the README, "Dashboard").

## Goal

One HTML page, served by the server itself, that shows at a glance:

- **Players** — how many are connected, and their names (Java and Bedrock).
- **Rejected joins** — name + IP of blocked (non-whitelisted) attempts.
- **Versions** — Paper/Minecraft, Java, and every plugin with its version.
- **Server** — MOTD, world, max players, world size on disk.
- **Performance** — TPS/MSPT and uptime.
- **History** — players online over time, per-player play sessions, and
  rejected-join counts, from a local time-series store.

## Non-goals

- No arbitrary commands *beyond* the RCON console (2026-10-20): `/console` runs
  one RCON command and shows the answer, and that is the whole of it — no file
  access, no shell, no plugin or world management. See "RCON console" below.
- The **only** file write is "Restart server" (2026-10-19): a POST that drops a
  trigger file systemd consumes. It cannot stop the server without restarting
  it, and it needs no privilege in the web app (see "Restart button" below).
- No accounts, sessions, or external database service — one shared Basic-auth
  credential and a local SQLite file.
- No client-side JavaScript, no build step, no CDN. Charts are static SVG
  rendered by the server.

## Constraints (from `TASKS.md` + recon)

- Listen on a **non-80 port**; expose on `0.0.0.0` **or** behind the existing
  nginx.
- Run as `steve` under **systemd**, same style as `autoplug.service`.
- Read-only apart from the restart trigger and the RCON console; **never**
  expose the RCON password or the AutoPlug server key to the browser. Player
  names are shown, so the page needs auth.
- Server facts: Ubuntu 24.04 (aarch64), `python3` 3.12.3, no go/node/bun,
  nginx 1.24 **active** with a `*.example.com` wildcard cert, wildcard DNS
  resolves for any `*.example.com` subdomain, and RCON is already enabled
  (`enable-rcon=true`, `rcon.port=25575`).

## Decisions

| area | choice | why | rejected |
|---|---|---|---|
| Language | **Python 3.12, standard library only** | Already installed; `http.server`, `socket` (RCON), `zipfile` (read `plugin.yml` from jars), `sqlite3`, `base64`, `html` cover everything | Go/Node — not installed, need a toolchain; Python + Flask — a venv and deps for no benefit |
| Framework | **none** — `ThreadingHTTPServer` + one `BaseHTTPRequestHandler` | No magic, no routing layer to learn | Flask/FastAPI |
| CSS | **Pico CSS v2, classless variant, vendored** (`dashboard/static/pico.classless.min.css`) | Semantic HTML needs **no classes**, so `render.py` emits plain `<table>`/`<h2>` and the server stays the only place with logic; one ~10 KB file served locally, so no CDN, works offline | hand-written style block; Water.css (plainer tables); Bootstrap (~227 KB, a class on every element); Tailwind (build step) |
| Layout | **`static/app.css`** (~2.5 KB) on top of Pico, multi-column flow | Pico scales the root font with the viewport (131.25% at ≥1536px) and caps content at 1450px, which reads oversized for a metrics page and wastes a wide screen. The override pins the type size, adds a full-width container and flows the cards into `columns: 5 26rem` — 3 columns at 1470, 4 at 1920, 5 at 3440. Card order is part of the layout: the balancer only breaks *between* cards, so the page is tuned to land at ~680 px per column on a 1470x869 screen (that is why the joins/leaves card sits with the players cards) | CSS grid (unequal card heights leave row gaps), masonry (not portable), a class-based framework (markup churn) |
| Frontend logic | **none** — `<meta http-equiv="refresh">` + server-rendered inline SVG | "Simplest": the server renders everything, including the charts. The timespan switch is a plain link (`?span=7d`), which the meta refresh keeps, and values are read on hover through SVG `<title>` — so interactivity needs no JavaScript either | JS `fetch`/Chart.js/uPlot/WebSocket |
| Chart window | **`?span=24h\|7d\|30d\|90d`**, bucket sizes 30 min→1 day | One table (`store.SPANS`) feeds both the query and the panel title, so they cannot drift; every span stays at 48-168 points | client-side zoom/pan (needs JS); a fixed window |
| Storage | **SQLite** (`sqlite3`, stdlib) | Embedded, one file, zero services, real SQL for time buckets; enough for a time series of this size | see "Storage" below |
| Player source (live) | **RCON `list`** on `127.0.0.1:25575` | Authoritative live count + names; no join/leave state to maintain | parsing `latest.log` joins (fragile — a chat plugin rewrites `X joined the game` to `System chat: ...`) |
| Player history | **RCON `list` sampled every 30 s, diffed** | Robust: exact names from RCON, transitions derived from consecutive samples (±30 s) | log-derived sessions (same fragility), player-event plugins |
| Rejected source | **`logs/blocked-joins.log`**, ingested into SQLite | Already written by `scripts/alert-nonwhitelist.sh`, append-only, tab-separated `ts/name/ip/iso`; ingest by byte offset is idempotent | re-parsing whitelist lines from the live log |
| Versions | **log head + `plugins/*.jar` `plugin.yml`** | No extra tooling; `zipfile` reads the manifest in-process | `unzip`/shelling out |
| Auth | **Sign-in form (`/login`) + HMAC-signed session cookie** | A real form with labelled `username`/`current-password` fields is what password managers save; Basic auth is not saved. Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` behind TLS, 30-day expiry, key derived from the password (so a password change logs everyone out) | HTTP Basic (not saved by password managers), nginx-only auth, no auth |
| Rendering | **server-side string + `html.escape`** | No template engine needed | Jinja2 |

## Storage (the "TSDB" question)

The requirement is a *time series* of two things: players online, and rejected
joins. The lightest thing that does this well is **SQLite** via the standard
library:

- It is a single file (`~/minecraft/dashboard.sqlite`), no daemon, no port, no
  config, and it survives restarts.
- Time-bucketed aggregation is a `GROUP BY strftime('%Y-%m-%d %H:00', ts)`
  query; no TSDB needed at this volume (dozens of rows/day).
- It is trivially backed up (it is inside `~/minecraft`) and can be deleted to
  start over.

Alternatives and why not, for this scale:

| option | verdict |
|---|---|
| **Prometheus + Grafana** | The "real" TSDB + dashboard stack — but two new services, a scrape endpoint, retention config, and a second UI, to plot two lines for one family. Over-kill. |
| **MariaDB** (already running on `:3306`) | Viable and already installed, but adds credentials, a schema in someone else's database, and a runtime dependency. No gain over SQLite here. |
| **Redis** (already running on `:6379`) | Great for counters, awkward for time-bucketed range queries and retention. |
| **JSONL append + read** | Simplest to write, but every query is a full scan and retention/pruning is manual. SQLite costs nothing more. |

### Schema (sketch)

```sql
-- one row per sampler tick (drives the "players online" and TPS charts).
-- up = 0 marks a tick where RCON did not answer: `players` is meaningless (0)
-- then and readers filter it out, so a row is *evidence* of an outage rather
-- than a gap. `up` was added on 2026-10-16; Store._migrate() ALTERs it in with
-- DEFAULT 1, which is correct for the older rows (they were only written while
-- the server answered).
CREATE TABLE samples (
  ts INTEGER PRIMARY KEY, players INTEGER, tps REAL, mspt REAL,
  up INTEGER NOT NULL DEFAULT 1
);

-- join/leave transitions derived from consecutive samples
CREATE TABLE sessions (
  ts INTEGER, player TEXT, event TEXT CHECK (event IN ('join','leave'))
);
CREATE INDEX sessions_player_ts ON sessions(player, ts);

-- ingested from blocked-joins.log (UNIQUE makes ingest idempotent)
CREATE TABLE rejects (
  ts TEXT, name TEXT, ip TEXT, ts_iso TEXT,
  UNIQUE (ts, name, ip)
);
```

- **Sampler:** a daemon thread inside the app calls RCON `list` every 30 s,
  writes a `samples` row, and diffs the name set against the previous tick to
  write `sessions` rows. The first tick after a restart is a baseline: it
  records a sample but emits **no** join/leave events (avoids a fake "everyone
  left" on reboot).
- **Reject ingest:** the same thread tails `blocked-joins.log` by byte offset
  and inserts new rows (`INSERT OR IGNORE`).
- **Retention:** prune rows older than `RETAIN_DAYS` (default 90) once a day.

The page reads only from SQLite for history (fast, no re-parsing); the live
panels still read RCON/files as before.

## Data flow

```mermaid
graph LR
  B[browser] -->|HTTPS, Basic auth| N[nginx]
  N -->|proxy_pass 127.0.0.1:8080| D[dashboard]
  D -->|"RCON list / tps / mspt"| R[(Minecraft :25575)]
  D -->|read head| L[logs/latest.log]
  D -->|read| J[logs/blocked-joins.log]
  D -->|read| P[plugins/*.jar]
  D -->|read| S[server.properties]
  D -->|read/write| Q[(dashboard.sqlite)]
  J -.->|ingest| Q
```

Read-only RCON commands only (`list`, `tps`, `mspt`). SQLite is the only thing
the app ever writes.

## Panels → source

| panel | source | notes |
|---|---|---|
| Players online | RCON `list` | `There are N of a max of M players online: a, b` |
| Max players / MOTD / world | `server.properties` | `max-players`, `motd`, `level-name` |
| World size | `du` of `level-name` dir | computed in-process (walk + sum) |
| TPS / MSPT + uptime | RCON `tps` / `mspt`, `/proc` or `systemd` | hidden if the commands are unavailable |
| Rejected joins (recent) | SQLite `rejects` | newest first, with name + IP |
| Rejected joins (history) | SQLite `rejects` | count per bucket over the span, top names/IPs |
| Server up/down | RCON + `systemctl is-active` + `/proc` | the first cell of the first column |
| Availability % | SQLite `samples.up` | share of sampled ticks that answered |
| Players online (chart) | SQLite `samples` | inline SVG line, peak per bucket, outage bands |
| TPS (chart) | SQLite `samples.tps` | inline SVG line, average per bucket |
| Play sessions | SQLite `sessions` | recent joins/leaves, and per-player playtime |
| Paper / Minecraft / Java | head of `logs/latest.log` | `Loading Paper … for Minecraft 26.2`, `Running Java 25 …` |
| Plugins + versions | `plugin.yml` inside `plugins/*.jar` | fallback to the jar filename |

## HTTP surface

| method | path | response |
|---|---|---|
| `GET` | `/` | the HTML page (all panels); `?span=24h\|7d\|30d\|90d` picks the chart window (an unknown value falls back to 24h) |
| `GET` | `/state.json` | same data as JSON, for scripting (same `?span=`) |
| `GET` | `/static/pico.classless.min.css` | the vendored stylesheet |
| `GET` | `/healthz` | `ok` — for systemd/nginx checks |
| `GET` | `/login` | the sign-in form (redirects to `/` when already signed in) |
| `POST` | `/login` | checks credentials → `302` + session cookie, or `401` with the form and an error |
| `POST` | `/logout` | clears the cookie → `302 /login` |
| `POST` | `/restart` | confirmation card; with `?confirm=1` queues a restart (`409` when one is pending or the server is starting) |
| `GET` | `/console` | the RCON console: transcript + one-line command form |
| `POST` | `/console` | runs one RCON command, appends it to the transcript → `302 /console` |
| any other | `*` | `405` / `404`, no body |

- Auth: the `mc_session` cookie on every path except `/healthz`, `/static/`
  (the stylesheet is not sensitive) and `/login`. Missing or invalid → `302` to
  `/login?next=<path>`.
- POSTs are guarded by `SameSite=Lax` plus an `Origin`/`Host` check: a
  verifiable cross-site origin is rejected with `403`, while an opaque `null`
  origin is accepted (Chromium sends it when the referrer policy hides the
  origin — hence `Referrer-Policy: same-origin`, not `no-referrer`).
- Failed logins are throttled per client IP (10 per 15 min → `429`).
- Per-collector `try/except` + RCON timeout: one failing source degrades one
  panel, never the whole page.

## Restart button (2026-10-19)

The page deliberately had no write path. A restart is the single exception, and
it is built so the web app still needs no privilege. Both restart buttons live
in the footer row next to Sign out — red for *now*, orange for *when empty* —
so the choice is visible instead of hidden behind a confirmation step:

| piece | role |
|---|---|
| `POST /restart` | the footer's buttons, each carrying `mode`; no confirmation step — the colours carry the warning, and each button is disabled while its action is impossible |
| `POST /restart`, `mode=now` | writes `~/minecraft/restart.request` → `mc-restart.path` → `mc-restart.service` runs `systemctl restart autoplug` |
| `POST /restart`, `mode=empty` | writes `~/minecraft/restart-when-empty.request` → `mc-restart-when-empty.path` → `mc-restart-when-empty.service` runs `scripts/restart-when-empty.sh`, which waits |
| `POST /restart`, `mode=cancel` | deletes the queue: the state file (waiter running) and/or the request file (not picked up yet) |
| `restart-when-empty.state` | the queue itself — written by the waiter, rendered by the banner, deleted to cancel |

Both modes are the same idea: the app's only capability is "create a file the
matching path unit watches". The deferred one adds a queue whose truth is a file
owned by the *waiter script*, so the dashboard holds no queue state and
restarting the dashboard can neither lose nor duplicate it. The script waits for
RCON to report 0 players, acts anyway if `autoplug` has been inactive for three
polls (nobody can be playing a server that is not running), and gives up after
`MAX_WAIT` (12 h) leaving the server alone.

Why not `sudo systemctl restart`: the service runs with
`NoNewPrivileges=yes`, and making sudo work would hand the web app a
root-capable command line. Why not RCON `stop`: that relies on AutoPlug
exiting by itself, and if that assumption is wrong the "restart" button becomes
a "shut down" button.

Guards: the route needs the session cookie like every other page, passes the
same `Origin`/`Host` check, answers `400` for a missing or unknown `mode`, and
refuses (`409`) while a request is already queued or while the server is
`starting`. The footer disables whichever button cannot act and the banner shows
the state, so those `409`s are only reachable by hand; because the unit consumes
the file, the pending state clears itself once the service picks it up.

## RCON console (2026-10-20)

`/console` is the second deliberate write path: one command, one answer. It
reuses `collect.rcon_command` — the same stdlib RCON client the sampler uses,
with the same credentials read from `server.properties` — so there is one
implementation of the protocol and the password still never reaches the
browser: the page posts the command text and nothing else.

| piece | role |
|---|---|
| `dashboard/console.py` | `Console.submit(command)`: run it, catch everything, append `{command, output, error, at}` to a `deque(maxlen=200)` |
| `POST /console` | POST-redirect-GET, so a reload or the back button re-runs nothing; the command — not its output — goes to the journal as the audit trail |
| `render.console_page` | one `<pre class="console">` transcript plus a one-line form; Pico already gives `pre` the monospace family, the code background and the radius |
| `render._mc_text` | Paper answers in §-codes (`§6TPS … §a20.0§r`), so the console turns them into `c0…cf`/`bold` spans; the CSS lifts the dim half of the palette for the dark theme |

Decisions worth keeping:

- **No terminal emulator.** xterm.js (or any WebSocket/SSE stream) is the
  pre-packaged answer for a *stream*, but RCON is request/response: no cursor
  movement, no partial writes, no resize. It would also mean JavaScript, a
  second protocol and a long-lived connection inside a `ThreadingHTTPServer`. A
  server-rendered `<pre>` does the same job here.
- **The transcript is memory only** — a console session, not a record. The
  journal keeps the commands, SQLite keeps the metrics.
- **History without JavaScript** is the input's `<datalist>`, filled with the
  distinct recent commands.
- **Scrolling without JavaScript**: the log grows and the page scrolls; the
  input carries `autofocus`, and a browser that focuses it also scrolls it into
  view, so the newest line is on screen after every command. A `#fragment`
  redirect was tried first and dropped — it suppresses `autofocus`, which would
  cost a click per command.

## Runtime & deployment

```
dashboard/
  server.py       # entrypoint: HTTP server, auth, routes, sampler thread
  collect.py      # RCON + file readers (live data)
  store.py        # SQLite schema, ingest, queries (history)
  render.py       # HTML + inline SVG
  static/
    pico.classless.min.css   # vendored Pico CSS v2 (no CDN)
    app.css                  # dense layout + multi-column flow
  ARCHITECTURE.md
systemd/mc-dashboard.service
```

- `mc-dashboard.service`: user `steve`, `Restart=always`,
  `EnvironmentFile=-/home/steve/minecraft/dashboard.env`,
  `ExecStart=/usr/bin/python3 /home/steve/minecraft/dashboard/server.py`,
  `After=network-online.target autoplug.service` (Wants, so a dead Minecraft
  doesn't block the page).
- Hardening: `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=full`,
  `ProtectKernelTunables=yes`, `ProtectControlGroups=yes`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX` (the last one for
  `systemctl show`). **Not** `ProtectHome` (the app must read/write
  `~/minecraft`).
- Deployment = copy `dashboard/` + the unit, `daemon-reload`, `restart`; plus an
  nginx `server` block for `mc.example.com` with `proxy_pass
  http://127.0.0.1:8080` and the existing `*.example.com` certificate.

## Configuration & secrets

`~/minecraft/dashboard.env` (mode 600, git-ignored, same pattern as
`mc-alerts.env`):

```
DASH_USER=...
DASH_PASS=...
DASH_BIND=127.0.0.1
DASH_PORT=8080
MC_DIR=/home/steve/minecraft
REFRESH=15
SAMPLE_SECONDS=30
RETAIN_DAYS=90
```

The RCON password is read from `server.properties` at startup and used only
server-side.

## Failure modes

| failure | effect | mitigation |
|---|---|---|
| Minecraft down / RCON refused | the banner turns red, players/TPS live panels degrade | each collector is isolated (one failing source never breaks the page); the sampler keeps ticking and records `up = 0`, so the outage becomes a band in the charts and lowers the availability figure. `starting` vs `down` is told apart with `systemctl is-active` + the Paper pid + uptime. |
| `systemctl`/`/proc` unavailable | status reads `down` while RCON is fine | RCON is checked first: a working RCON always wins, so a broken unit-query cannot make a live server look dead |
| app restart | potential fake mass-leave | first tick is a baseline, emits no events |
| `blocked-joins.log` missing | rejects panel empty | treat as empty; ingest resumes when it appears |
| SQLite locked/corrupt | history panels fail | single writer (sampler) + short timeout; file is disposable |
| a jar has no `plugin.yml` | that plugin shows filename | fallback |
| `latest.log` rotated mid-read | versions keep last good value | non-fatal |
| reload storm | extra RCON calls | cache collector results ~5 s |
| a restart path unit failed (`unit-start-limit-hit`) | that button silently does nothing, and the queue file stays | only a service that keeps failing reaches the start limit: read `journalctl -u mc-restart*`, fix the cause, `systemctl reset-failed <unit>`, then delete the stale `restart*.request` (the files are triggers, safe to remove) |

## Decisions taken (2026-10-13)

- Runtime: Python 3 standard library. CSS: Pico CSS v2, classless, vendored.
- Storage: SQLite embedded at `~/minecraft/dashboard.sqlite`.
- Cadence and retention: 30 s samples, 90 days.
- Exposure: nginx + HTTPS at `mc.example.com` (vhost in `nginx/`); the app
  binds `127.0.0.1:8080` only.
- Extras included: TPS/MSPT + uptime, world size, `/state.json`, and the player
  and rejected-join history.

## Decisions taken (2026-10-16): outages, charts, timespans

- **Uptime is data, not a live probe.** The sampler now writes a row on every
  tick, `up = 0` when RCON does not answer, instead of skipping the tick. That
  turns "the dashboard was not running" (no row) and "the server was down" (a
  row with `up = 0`) into two distinguishable states, which is what the outage
  bands and the availability percentage are drawn from.
- **The status card is the answer to "is it up?".** It is the first cell of the
  first column (an ordinary card in the flow, 2026-10-19 — it used to be a
  full-width `column-span: all` banner), carrying the unit state, the Paper pid,
  the uptime, the player count and how much of the selected window was up. It is
  toned through `data-status` (up/warn/down) rather than classes, plus a left
  accent bar; `--mc-down` doubles as the chart band colour.
- **One chart engine, a real time axis.** `time_chart()` replaced the old
  bar-per-index renderer: points are placed by timestamp, so a missing bucket is
  a gap and never a fake zero, and sparse data (7 rejected joins ever) cannot
  flatten the axis. It draws y gridlines on 1/2/5×10ⁿ steps, four x labels
  formatted for the window, an area+line or bars, outage bands behind the
  series, and `<title>` tooltips so hovering works without JavaScript.
  - Bar width is the **bucket's width on the axis**
    (`plot_w × bucket / window`), never `plot_w / len(points)`: the rejects
    series only carries the buckets that had events, so sizing by the point
    count drew 70px bars for 1-day buckets across 90 days and neighbours
    overlapped (fixed 2026-10-23). Bars are therefore 2.3px at 30d (6 h
    buckets), 3.1px at 90d (1 day) and 1.4px at 7d (1 h) — the `<title>`
    tooltip and the "N attempts in this window" caption carry what a thin bar
    cannot.
- **Spans are links, not client state.** `?span=` is re-queried server-side and
  survives the 15 s meta refresh; `/state.json?span=` gives scripts the same
  window. History is cached ~20 s per span, so a 90-day query does not run on
  every page load. The switch lives in the title band (`body.dash > header`,
  a wrapping flex row), not in the status card: it selects the chart window and
  says nothing about whether the server is up.
- **`playtime` follows the span** so "Most played — last 7 days" is honest when
  the window is 24 h or 90 d.
- **Not chosen:** vendoring a JS charting library (uPlot/Chart.js) for zoom and
  pan. It would be ~50 KB and a second rendering path for a page that must work
  with no build step; the server-rendered SVG covers what the data supports at
  this cadence.
