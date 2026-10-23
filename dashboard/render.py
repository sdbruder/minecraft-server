"""Server-side HTML rendering.

Semantic markup only (styled by Pico CSS classless), no classes needed and no
client-side JavaScript. Charts are inline SVG drawn here.
"""

import html
import math
import re
import time

from collect import human_duration, human_size
from store import DEFAULT_SPAN, SPANS, parse_iso, span_spec

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>{title}</title>
<link rel="stylesheet" href="/static/pico.classless.min.css">
<link rel="stylesheet" href="/static/app.css?v={asset_v}">
</head>
<body class="dash">
<header>
  <div>
    <h1>{heading}</h1>
    <p>{subtitle}</p>
  </div>
  {span_nav}
</header>
<main>
{body}
</main>
<footer>
  <p><small>Read-only metrics · <a href="/state.json">state.json</a> · refreshed every {refresh} s · times in UTC</small></p>
  <div>{actions}</div>
</footer>
</body>
</html>
"""

# Password-manager friendly: a real form, a stable URL (/login), labelled
# username/password fields and the standard autocomplete hints.
LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Minecraft família</title>
<link rel="stylesheet" href="/static/pico.classless.min.css">
<link rel="stylesheet" href="/static/app.css?v={asset_v}">
</head>
<body class="login">
<header>
  <h1>Minecraft — família</h1>
  <p>Sign in to see the server dashboard.</p>
</header>
<main>
<article>
{error}<form method="post" action="/login">
  <input type="hidden" name="next" value="{next}">
  <label for="username">Username
  <input type="text" id="username" name="username" autocomplete="username"
         autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
  </label>
  <label for="password">Password
  <input type="password" id="password" name="password"
         autocomplete="current-password" required>
  </label>
  <button type="submit">Sign in</button>
</form>
</article>
</main>
<footer><p><small>Read-only server metrics.</small></p></footer>
</body>
</html>
"""


# The console page: same header/footer band as the metrics page, one card with
# a transcript and a one-line form. No meta refresh — it would take the focus
# out of the input mid-command — and no JavaScript.
CONSOLE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="/static/pico.classless.min.css">
<link rel="stylesheet" href="/static/app.css?v={asset_v}">
</head>
<body class="console">
<header>
  <h1>{heading}</h1>
  <p>{subtitle}</p>
</header>
<main>
<article>
<h2>RCON console</h2>
{log}
<form method="post" action="/console">
  <label for="command">Command</label>
  <input type="text" id="command" name="command" list="command-history"
         maxlength="{max_command}" autocomplete="off" autocapitalize="none"
         autocorrect="off" spellcheck="false" required autofocus>
  <button type="submit">Run</button>
</form>
{history}
</article>
</main>
<footer>
  <p><small>{note}</small></p>
  <div>
    <a role="button" href="/">Metrics</a>
    <form method="post" action="/logout"><button type="submit">Sign out</button></form>
  </div>
</footer>
</body>
</html>
"""


def esc(value):
    return html.escape("—" if value is None else str(value), quote=True)


def _article(title, body):
    return f"<article>\n<h2>{esc(title)}</h2>\n{body}\n</article>\n"


def _table(headers, rows, cls=""):
    if not rows:
        return "<p><em>nothing to show</em></p>\n"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    attrs = f' class="{cls}"' if cls else ""
    return f"<table{attrs}>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>\n"


def _nb(text):
    """Join a short value's words so a narrow column cannot split it.

    "203.0.113.13 (bedrock)" used to break between the address and the
    flavour, which doubled the height of every bedrock row.
    """
    return (text or "").replace(" ", "\u00a0")


def _clock(epoch, with_date=False, seconds=False):
    # No-break space between date and time: in the narrow table columns the
    # stamp used to wrap onto two lines and every row became two rows tall.
    fmt = "%Y-%m-%d\u00a0%H:%M" if with_date else "%H:%M"
    if seconds:
        fmt += ":%S"
    return time.strftime(fmt, time.gmtime(epoch))


def _nice_ceiling(value):
    """Smallest 1/2/5×10ⁿ that is >= value, so y-axis labels read cleanly."""
    if value <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(value))
    for factor in (1, 2, 5, 10):
        if value <= factor * base * 1.0001:
            return factor * base
    return 10 * base


def _fmt(value):
    if value is None:
        return "—"
    return f"{round(value):g}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def _x_format(hours):
    if hours <= 48:
        return "%H:%M"
    if hours <= 24 * 10:
        return "%a %H:%M"
    return "%m-%d"


def _series_summary(values):
    if not values:
        return "no samples in this window"
    return f"peak {_fmt(max(values))}, average {_fmt(sum(values) / len(values))}"


def time_chart(points, span, kind="line", width=640, height=150, unit="",
               bands=(), y_min=0, integer=False):
    """Inline SVG chart on a real time axis, drawn entirely on the server.

    points: [(ts, value or None)] oldest first. None means "no samples in that
        bucket" and is drawn as a gap, never as zero.
    bands:  [(start, end)] windows (outages) drawn behind the series.
    y_min:  0 anchors the axis at zero; None follows the data (TPS hovers near
        the top of its range, where a zero baseline wastes the plot).

    No JavaScript: values are readable by hovering, through SVG <title>.
    """
    if not points:
        return "<p><em>no data for this window yet</em></p>\n"

    x1 = int(time.time())
    x0 = x1 - span["hours"] * 3600
    pad_left, pad_right, pad_top, pad_bottom = 34, 6, 10, 20
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    values = [value for _, value in points if value is not None]
    if not values:
        return "<p><em>the server was down for this whole window</em></p>\n"
    top = max(values)
    if y_min is None:                       # follow the data (TPS sits near 20)
        step = _nice_ceiling(max(top - min(values), 0.5) / 2)
        lo = math.floor(min(values) / step) * step
    else:
        lo = float(y_min)
        step = _nice_ceiling(max(top - lo, 1.0) / 2)
    if integer:                             # players and reject counts
        step = max(1.0, step)
    hi = lo + 2 * step
    while hi < top:
        hi += step
    spread = hi - lo

    def sx(ts):
        return pad_left + (min(max(ts, x0), x1) - x0) / (x1 - x0) * plot_w

    def sy(value):
        return pad_top + plot_h - (value - lo) / spread * plot_h

    fmt = _x_format(span["hours"])
    base_y = sy(lo)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="time series chart" xmlns="http://www.w3.org/2000/svg">'
    ]

    # Outages first: everything else is drawn on top of them.
    for start, end in bands:
        left, right = sx(start), sx(end + span["bucket"])
        parts.append(
            f'<rect x="{left:.1f}" y="{pad_top}" width="{max(1.0, right - left):.1f}" '
            f'height="{plot_h}" fill="var(--mc-down)" fill-opacity="0.18">'
            f'<title>server down {_clock(start)}–{_clock(end)}</title></rect>'
        )

    for value in (lo, lo + step, hi):
        y = sy(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" y2="{y:.1f}" '
            f'stroke="currentColor" stroke-opacity="0.13"/>'
        )
        parts.append(
            f'<text x="{pad_left - 6}" y="{y + 3.5:.1f}" text-anchor="end" font-size="10" '
            f'fill="currentColor" fill-opacity="0.7">{value:g}</text>'
        )
    parts.append(
        f'<line x1="{pad_left}" y1="{base_y:.1f}" x2="{width - pad_right}" y2="{base_y:.1f}" '
        f'stroke="currentColor" stroke-opacity="0.35"/>'
    )

    ticks = 4
    for index in range(ticks):
        ts = x0 + (x1 - x0) * index / (ticks - 1)
        anchor = "start" if index == 0 else "end" if index == ticks - 1 else "middle"
        parts.append(
            f'<text x="{sx(ts):.1f}" y="{height - 6}" text-anchor="{anchor}" font-size="10" '
            f'fill="currentColor" fill-opacity="0.7">{time.strftime(fmt, time.gmtime(ts))}</text>'
        )

    def edge_note(ts):
        """The first and last bucket are only partly inside the window, so the
        newest point is the peak *so far* — say so, or it reads as a live count
        that contradicts the players panel."""
        if ts + span["bucket"] > x1:
            return " (so far)"
        return " (partial)" if ts < x0 else ""

    if kind == "bars":
        # The slot width is the bucket's own width on the time axis, never
        # plot_w / len(points): the series only carries the buckets that have
        # events, so sizing by the point count drew 70px bars across a 90-day
        # window and neighbouring buckets overlapped each other.
        slot = plot_w * span["bucket"] / (span["hours"] * 3600)
        width_bar = max(1.0, min(slot * 0.7, slot - 1))
        for ts, value in points:
            if value is None:
                continue
            x, y = sx(ts) + (slot - width_bar) / 2, sy(value)
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{width_bar:.1f}" '
                f'height="{max(0.5, base_y - y):.1f}" rx="1" fill="currentColor" '
                f'fill-opacity="0.55"><title>{_clock(ts)}{edge_note(ts)}: '
                f'{_fmt(value)}{esc(unit)}</title></rect>'
            )
    else:
        segments, run = [], []
        for ts, value in points:
            if value is None:
                if run:
                    segments.append(run)
                    run = []
            else:
                run.append((sx(ts), sy(value), ts, value))
        if run:
            segments.append(run)
        for segment in segments:
            if len(segment) == 1:
                x, y, _, _ = segment[0]
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="currentColor"/>')
                continue
            line = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in segment)
            parts.append(
                f'<polygon points="{segment[0][0]:.1f},{base_y:.1f} {line} '
                f'{segment[-1][0]:.1f},{base_y:.1f}" fill="currentColor" fill-opacity="0.15"/>'
            )
            parts.append(
                f'<polyline points="{line}" fill="none" stroke="currentColor" '
                f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            )
        if len(points) <= 100:   # wider hit targets; not worth the bytes past that
            with_date = span["hours"] > 48
            for ts, value in points:
                if value is None:
                    continue
                x, y = sx(ts), sy(value)
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="none" '
                    f'pointer-events="all"><title>{_clock(ts, with_date=with_date)}'
                    f'{edge_note(ts)}: {_fmt(value)}{esc(unit)}</title></circle>'
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="currentColor" '
                    f'pointer-events="none"/>'
                )

    parts.append("</svg>\n")
    return "".join(parts)


# ------------------------------------------------------------------ panels --

def _players_panel(players):
    if players.get("error"):
        return _article("Players online", f"<p><em>unavailable — {esc(players['error'])}</em></p>")
    count, maximum, names = players.get("count"), players.get("max"), players.get("names") or []
    if count is None:
        return _article("Players online", "<p><em>unavailable</em></p>")
    heading = f"Players online — {count}" + (f"/{maximum}" if maximum else "")
    if names:
        listed = ", ".join(esc(n) for n in names)
        body = f"<p>{listed}</p>"
    else:
        body = "<p><em>nobody is playing right now</em></p>"
    return _article(heading, body)


def _bucket_label(span):
    return {
        1800: "30 min", 3600: "hour", 21600: "6 h", 86400: "day",
    }.get(span["bucket"], f"{span['bucket'] // 60} min")


def _players_chart_panel(players, history, span):
    """The line is the peak per bucket, so its newest point is the busiest
    moment of the bucket *so far* — never the live count. Lead the caption with
    the live figure so the two cannot look like they disagree."""
    points = [(ts, count) for ts, count, _tps, _down, _ticks in history.get("series") or []]
    values = [value for _, value in points if value is not None]
    live = players.get("count")
    now = f"{live} online now" if live is not None else "live count unknown"
    return _article(
        f"Players online — peak per {_bucket_label(span)}, {span['label']}",
        time_chart(points, span, unit=" online", integer=True,
                   bands=history.get("outages") or [])
        + f"<p><small>{esc(now)} · line: {esc(_series_summary(values))} per "
          f"{_bucket_label(span)} · gaps: no samples · red bands: server down</small></p>",
    )


def _tps_panel(history, span):
    points = [(ts, tps) for ts, _players, tps, _down, _ticks in history.get("series") or []]
    values = [value for _, value in points if value is not None]
    if not values:
        return ""
    return _article(
        f"TPS — average per {_bucket_label(span)}, {span['label']}",
        time_chart(points, span, y_min=None, unit=" TPS", bands=history.get("outages") or [])
        + f"<p><small>{esc(_series_summary(values))} · 20 is a healthy server</small></p>",
    )


def _playtime_panel(history, span):
    rows = [
        (player, f"{seconds // 3600}h {seconds % 3600 // 60:02d}m")
        for player, seconds in (history.get("playtime") or [])
    ]
    return _article(f"Most played — {span['label']}", _table(["player", "played"], rows))


def _sessions_panel(history):
    recent = [
        (_clock(item["ts"], with_date=True, seconds=True), item["player"], item["event"])
        for item in (history.get("sessions") or [])
    ]
    return _article("Recent joins / leaves", _table(["when", "player", "event"], recent, cls="stamps"))


def _rejects_recent_panel(history):
    rows = []
    for item in history.get("recent_rejects") or []:
        # Same compact stamp as the joins/leaves list: the raw
        # "2026-10-19T13:17:11+0000" has no break opportunity that keeps it on
        # one line, so it wrapped the column and made every row two lines tall.
        ts = parse_iso(item["ts_iso"])
        when = _clock(ts, with_date=True, seconds=True) if ts is not None else (item["ts_iso"] or item["ts"])
        rows.append((when, item["name"], _nb(item["ip"])))
    return _article("Rejected joins — most recent",
                    _table(["when (UTC)", "name", "ip"], rows, cls="stamps"))


def _rejects_chart_panel(history, span):
    points = [(ts, n) for ts, n in (history.get("rejects") or [])]
    total = sum(n for _, n in points)
    plural = "" if total == 1 else "s"
    return _article(
        f"Rejected joins — per {_bucket_label(span)}, {span['label']}",
        time_chart(points, span, kind="bars", width=440, height=130, unit=" rejected",
                   integer=True)
        + f"<p><small>{total} attempt{plural} in this window</small></p>",
    )


def _rejects_top_panel(history):
    rows = [(name, _nb(ip), n) for name, ip, n in (history.get("top") or [])]
    return _article("Rejected joins — most frequent", _table(["name", "ip", "attempts"], rows))


def _perf_panel(perf):
    tps = perf.get("tps")
    mspt = perf.get("mspt")
    uptime = perf.get("uptime")
    rows = []
    if tps:
        rows.append(("TPS (1m / 5m / 15m)", " / ".join(f"{v:g}" for v in tps)))
    if mspt:
        rows.append(("MSPT avg (5s / 10s / 1m)", " / ".join(f"{v:g}" for v in mspt)))
    if uptime is not None:
        hours, minutes = divmod(int(uptime) // 60, 60)
        days, hours = divmod(hours, 24)
        rows.append(("server up", f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"))
    return _article("Performance", _table(["metric", "value"], rows))


def _server_panel(state):
    server, versions = state.get("server") or {}, state.get("versions") or {}
    rows = [
        ("MOTD", server.get("motd")),
        ("world", f"{server.get('world')} ({human_size(state.get('world_size'))})"),
        ("max players", server.get("max_players")),
        ("online mode", "yes" if server.get("online_mode") else "no"),
        ("Paper", versions.get("paper")),
        ("Minecraft", versions.get("minecraft")),
        ("Java", versions.get("java")),
        ("Geyser", versions.get("geyser")),
    ]
    return _article("Server", _table(["field", "value"], [(k, v) for k, v in rows if v not in (None, "")]))


def _plugins_panel(state):
    rows = [(p["name"], p["version"]) for p in (state.get("plugins") or [])]
    return _article("Plugins", _table(["plugin", "version"], rows))


def _errors_panel(errors):
    items = "".join(f"<li>{esc(e)}</li>" for e in errors)
    return _article("Problems", f"<ul>{items}</ul>")


# ------------------------------------------------------------ status bar ----

STATUS_HEADLINE = {
    "up": ("Server is up", "up"),
    "starting": ("Server is starting", "warn"),
    "unresponsive": ("Server is not responding", "warn"),
    "down": ("Server is down", "down"),
}
STATUS_HELP = {
    "starting": "no RCON answer yet — a cold start takes 25-35 s",
    "unresponsive": "the autoplug unit is running but RCON stays silent",
    "down": "the autoplug unit is not active",
}


def _span_nav(span):
    """Timespan switch: plain links, so the page stays JavaScript-free."""
    items = []
    for key in SPANS:
        if key == span["key"]:
            items.append(f"<li><strong>{esc(key)}</strong></li>")
        else:
            items.append(f'<li><a href="/?span={esc(key)}">{esc(key)}</a></li>')
    return f"<nav><ul>{''.join(items)}</ul></nav>\n"


def _status_banner(state, span):
    """The first thing on the page: is the server up, and has it been?"""
    status = state.get("status") or {}
    players = state.get("players") or {}
    availability = (state.get("history") or {}).get("availability") or {}
    name = status.get("state") or "unknown"
    headline, tone = STATUS_HEADLINE.get(name, ("Server state unknown", "warn"))

    facts = ["RCON answering" if status.get("rcon") else "RCON unreachable"]
    if status.get("unit"):
        facts.append(f"autoplug.service {status['unit']}")
    if status.get("pid"):
        facts.append(f"paper pid {status['pid']}")
    if status.get("uptime") is not None:
        facts.append(f"started {human_duration(status['uptime'])} ago")
    if players.get("count") is not None:
        facts.append(f"{players['count']} of {players.get('max') or '?'} players online")
    if STATUS_HELP.get(name):
        facts.append(STATUS_HELP[name])

    if availability.get("ticks"):
        summary = (f"{availability['up_pct']:.2f}% up over the {span['label']} — "
                   f"{availability['down_ticks']} of {availability['ticks']} samples down")
    else:
        summary = f"nothing sampled for the {span['label']} yet"
    if availability.get("outage_start"):
        summary += (" · this outage started "
                    f"{_clock(availability['outage_start'], with_date=True, seconds=True)} UTC")

    pending = status.get("restart_pending")
    waiting = status.get("restart_waiting")
    if pending:
        note = (f"<p><small>restart requested "
                f"{_clock(pending, with_date=True, seconds=True)} UTC — waiting for the "
                f"service to pick it up</small></p>")
    elif waiting:
        online = players.get("count")
        count_text = f"{online} online" if online is not None else "live count unknown"
        note = (f"<p><small>restart queued for when the server empties — {esc(count_text)}, "
                f"queued {_clock(waiting, with_date=True, seconds=True)} UTC</small></p>")
    elif name == "starting":
        note = "<p><small>a restart is in progress…</small></p>"
    else:
        note = ""

    return (
        f'<article data-status="{tone}">\n'
        f"<h2>{esc(headline)}</h2>\n"
        f"<p>{esc(' · '.join(facts))}</p>\n"
        f"<p>{esc(summary)}</p>\n"
        f"{note}\n"
        f"</article>\n"
    )


def _footer_actions(state):
    """The page's buttons, all in one row: two restarts and Sign out.

    Both restart buttons act directly (the colours carry the warning), so each
    is disabled exactly when the server cannot take that action: "now" while a
    restart is already queued or running, "when empty" once one is queued — at
    which point the same slot offers the cancel instead.
    """
    status = state.get("status") or {}
    pending = status.get("restart_pending")
    waiting = status.get("restart_waiting")
    busy = status.get("state") == "starting"
    blocked = bool(pending) or busy

    if waiting:
        second = ('<button type="submit" name="mode" value="cancel">'
                  'Cancel restart</button>')
    else:
        second = (f'<button type="submit" name="mode" value="empty"'
                  f'{" disabled" if blocked else ""}>Restart when empty</button>')
    first = (f'<button type="submit" name="mode" value="now"'
             f'{" disabled" if blocked or waiting else ""}>Restart now</button>')

    return (
        '<a role="button" href="/console">Console</a>'
        f'<form method="post" action="/restart">{first}{second}</form>'
        '<form method="post" action="/logout"><button type="submit">Sign out</button></form>'
    )


def page(state, refresh=15, asset_v="0"):
    generated = state.get("generated") or time.time()
    span = state.get("span") or span_spec(DEFAULT_SPAN)
    players = state.get("players") or {}
    count, maximum = players.get("count"), players.get("max")
    if count is None:
        summary = "player count unavailable"
    else:
        summary = f"{count} player{'s' if count != 1 else ''} online" + (f" of {maximum}" if maximum else "")
    history = state.get("history") or {}

    body = "".join([
        _status_banner(state, span),
        _players_panel(players),
        _players_chart_panel(players, history, span),
        # The joins/leaves list sits with the players cards: that is where it
        # belongs, and it also packs the columns evenly (the multi-column
        # balancer breaks between cards, so card order sets the column heights).
        _sessions_panel(history),
        _tps_panel(history, span),
        _playtime_panel(history, span),
        _rejects_recent_panel(history),
        _rejects_chart_panel(history, span),
        _rejects_top_panel(history),
        _perf_panel(state.get("perf") or {}),
        _server_panel(state),
        _plugins_panel(state),
        _errors_panel(state["errors"]) if state.get("errors") else "",
    ])
    return PAGE.format(
        refresh=refresh,
        asset_v=asset_v,
        title="Minecraft — família",
        heading="Minecraft — família",
        subtitle=f"host.example.com · {esc(summary)} · updated {_clock(generated, with_date=True, seconds=True)} UTC",
        span_nav=_span_nav(span),
        actions=_footer_actions(state),
        body=body,
    )


def login_page(error=None, next_path="/", asset_v="0"):
    message = f"<p><strong>{esc(error)}</strong></p>\n" if error else ""
    return LOGIN_PAGE.format(error=message, next=esc(next_path), asset_v=asset_v)


_MC_COLOUR_CODES = "0123456789abcdef"


def _mc_text(text):
    """Minecraft's §-codes as HTML: colours and bold, the rest dropped.

    Paper answers RCON in the same format it prints in the log, e.g.
    "§6TPS from last 1m, 5m, 15m: §a20.0§r", so the console translates the
    codes instead of showing them. Colours are class names (c0…cf) so the CSS
    can lift the dim ones for the dark theme; §k/§m/§n/§o have no equivalent
    here and are dropped.
    """
    parts = []
    style = []
    for chunk in re.split(r"(§[0-9a-fk-or])", text):
        if not chunk:
            continue
        if len(chunk) == 2 and chunk[0] == "§":
            code = chunk[1]
            if code in _MC_COLOUR_CODES:
                style = [f"c{code}"]        # a colour resets the styles, as in game
            elif code == "l":
                style = style + ["bold"]
            elif code == "r":
                style = []
            continue
        body = esc(chunk)
        parts.append(f'<span class="{" ".join(style)}">{body}</span>' if style else body)
    return "".join(parts)


def _console_log(entries):
    """The transcript: what was asked, then what came back.

    One <pre>, so the browser keeps the newlines and Pico's monospace styling
    applies. The log grows and the page scrolls: the input below carries
    ``autofocus``, and a browser that focuses it also scrolls it into view, so
    the newest line ends up on screen without a line of JavaScript.
    """
    if not entries:
        return ('<pre class="console" id="log">'
                '<span class="muted">no commands yet — try <b>list</b> or <b>tps</b></span></pre>')
    lines = []
    for entry in entries:
        lines.append(f'<span class="prompt">&gt; {esc(entry["command"])}</span>')
        if entry["error"]:
            lines.append(f'<span class="error">{esc(entry["error"])}</span>')
        elif entry["output"].strip():
            lines.append(_mc_text(entry["output"].rstrip("\n")))
        else:
            lines.append('<span class="muted">(no output)</span>')
    return '<pre class="console" id="log">' + "\n".join(lines) + "</pre>"


def console_page(entries, history=(), asset_v="0"):
    options = "".join(f'<option value="{esc(command)}"></option>' for command in history)
    datalist = f'<datalist id="command-history">{options}</datalist>' if options else ""
    return CONSOLE_PAGE.format(
        asset_v=asset_v,
        title="RCON console · Minecraft família",
        heading="Minecraft — família",
        subtitle="RCON console · commands run as the server console",
        log=_console_log(entries),
        history=datalist,
        max_command=400,
        note="Runs with the server console's authority (op, ban, stop, …) · "
             "the transcript is kept in memory only, and the commands go to the journal",
    )



