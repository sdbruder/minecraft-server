"""Server-side HTML rendering.

Semantic markup only (styled by Pico CSS classless), no classes needed and no
client-side JavaScript. Charts are inline SVG drawn here.
"""

import html
import time

from collect import human_size

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
  <h1>{heading}</h1>
  <p>{subtitle}</p>
</header>
<main>
{body}
</main>
<footer>
  <p><small>Read-only. <a href="/state.json">state.json</a> · refreshed every {refresh} s · times in UTC</small></p>
  <form method="post" action="/logout"><button type="submit">Sign out</button></form>
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


def esc(value):
    return html.escape("—" if value is None else str(value), quote=True)


def _article(title, body):
    return f"<article>\n<h2>{esc(title)}</h2>\n{body}\n</article>\n"


def _table(headers, rows):
    if not rows:
        return "<p><em>nothing to show</em></p>\n"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>\n"


def _clock(epoch, with_date=False, seconds=False):
    fmt = "%Y-%m-%d %H:%M" if with_date else "%H:%M"
    if seconds:
        fmt += ":%S"
    return time.strftime(fmt, time.gmtime(epoch))


def bar_chart(points, width=880, height=150, pad_left=42, pad_bottom=22, pad_top=12, unit=""):
    """points: [(label, value)] -> inline SVG bars (scales with the page)."""
    if not points:
        return "<p><em>no data yet</em></p>\n"
    values = [max(0, int(v or 0)) for _, v in points]
    vmax = max(values) or 1
    count = len(points)
    plot_w = width - pad_left - 8
    plot_h = height - pad_top - pad_bottom
    slot = plot_w / count
    gap = min(2.0, slot * 0.2)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="bar chart" xmlns="http://www.w3.org/2000/svg">'
    ]
    parts.append(
        f'<line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{width - 8}" '
        f'y2="{pad_top + plot_h}" stroke="currentColor" stroke-opacity="0.35"/>'
    )
    for index, (label, value) in enumerate(points):
        value = max(0, int(value or 0))
        bar_h = (value / vmax) * plot_h
        x = pad_left + index * slot + gap / 2
        y = pad_top + plot_h - bar_h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(1.0, slot - gap):.1f}" '
            f'height="{max(0.0, bar_h):.1f}" rx="2" fill="currentColor" '
            f'fill-opacity="0.55"><title>{esc(label)}: {value}{esc(unit)}</title></rect>'
        )
    parts.append(
        f'<text x="{pad_left - 6}" y="{pad_top + 9}" text-anchor="end" font-size="11" '
        f'fill="currentColor" fill-opacity="0.7">{vmax:g}</text>'
    )
    parts.append(
        f'<text x="{pad_left - 6}" y="{pad_top + plot_h}" text-anchor="end" '
        f'font-size="11" fill="currentColor" fill-opacity="0.7">0</text>'
    )
    parts.append(
        f'<text x="{pad_left}" y="{height - 6}" font-size="11" fill="currentColor" '
        f'fill-opacity="0.7">{esc(points[0][0])}</text>'
    )
    parts.append(
        f'<text x="{width - 8}" y="{height - 6}" text-anchor="end" font-size="11" '
        f'fill="currentColor" fill-opacity="0.7">{esc(points[-1][0])}</text>'
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


def _history_panel(history):
    points = [(_clock(ts), n) for ts, n in history.get("series") or []]
    return _article("Players online — last 24 h",
                    bar_chart(points, width=640, height=170, unit=" online"))


def _playtime_panel(history):
    rows = [
        (player, f"{seconds // 3600}h {seconds % 3600 // 60:02d}m")
        for player, seconds in (history.get("playtime") or [])
    ]
    return _article("Most played — last 7 days", _table(["player", "played"], rows))


def _sessions_panel(history):
    recent = [
        (_clock(item["ts"], with_date=True, seconds=True), item["player"], item["event"])
        for item in (history.get("sessions") or [])
    ]
    return _article("Recent joins / leaves", _table(["when", "player", "event"], recent))


def _rejects_recent_panel(history):
    rows = [
        (item["ts_iso"] or item["ts"], item["name"], item["ip"])
        for item in (history.get("recent_rejects") or [])
    ]
    return _article("Rejected joins — most recent", _table(["when (UTC)", "name", "ip"], rows))


def _rejects_daily_panel(history):
    points = [(day[5:], n) for day, n in (history.get("per_day") or [])]
    return _article("Rejected joins — per day (30 d)", bar_chart(points, width=640, height=170, unit=" rejected"))


def _rejects_top_panel(history):
    rows = [(name, ip, n) for name, ip, n in (history.get("top") or [])]
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


def page(state, refresh=15, asset_v="0"):
    generated = state.get("generated") or time.time()
    players = state.get("players") or {}
    count, maximum = players.get("count"), players.get("max")
    if count is None:
        summary = "player count unavailable"
    else:
        summary = f"{count} player{'s' if count != 1 else ''} online" + (f" of {maximum}" if maximum else "")
    history = state.get("history") or {}

    body = "".join([
        _players_panel(players),
        _history_panel(history),
        _playtime_panel(history),
        _sessions_panel(history),
        _rejects_recent_panel(history),
        _rejects_daily_panel(history),
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
        body=body,
    )


def login_page(error=None, next_path="/", asset_v="0"):
    message = f"<p><strong>{esc(error)}</strong></p>\n" if error else ""
    return LOGIN_PAGE.format(error=message, next=esc(next_path), asset_v=asset_v)
