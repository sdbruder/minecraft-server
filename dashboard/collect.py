"""Live, read-only data collection for the Minecraft dashboard.

Everything here only reads: RCON (``list`` / ``tps`` / ``mspt``),
``server.properties``, ``logs/latest.log`` and ``plugins/*.jar``.
Nothing in this module writes to the game.
"""

import os
import re
import socket
import struct
import subprocess
import zipfile

SECTION = re.compile("\u00a7.")  # Minecraft colour/format codes (§x)


# ------------------------------------------------------------------ RCON ----

class RconError(RuntimeError):
    pass


def _read_exact(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise RconError("connection closed by server")
        buf += chunk
    return buf


def _packet(sock, request_id, ptype, body):
    payload = struct.pack("<ii", request_id, ptype) + body.encode() + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(payload)) + payload)


def _response(sock):
    (length,) = struct.unpack("<i", _read_exact(sock, 4))
    data = _read_exact(sock, length)
    request_id, ptype = struct.unpack("<ii", data[:8])
    return request_id, ptype, data[8:-2].decode("utf-8", "replace")


def rcon_command(host, port, password, command, timeout=5.0):
    """Run one RCON command and return its text response."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        _packet(sock, 1, 3, password)              # SERVERDATA_AUTH
        request_id, _, _ = _response(sock)
        if request_id == -1:
            raise RconError("authentication failed")
        _packet(sock, 2, 2, command)               # SERVERDATA_EXECCOMMAND
        _, _, body = _response(sock)
        return body


# ------------------------------------------------------------- parsing ------

def parse_players(text):
    """'There are 1 of a max of 20 players online: a, b' -> (1, 20, ['a','b'])."""
    match = re.search(r"There are (\d+) of a max of (\d+) players online:?\s*(.*)", text)
    if not match:
        return None
    count, maximum, names = int(match.group(1)), int(match.group(2)), match.group(3)
    players = [name.strip() for name in names.split(",") if name.strip()]
    return count, maximum, players


def parse_tps(text):
    """'TPS from last 1m, 5m, 15m: 20.0, 20.0, 20.0' -> [20.0, 20.0, 20.0]."""
    tail = SECTION.sub("", text).split(":", 1)[-1]
    values = re.findall(r"\d+\.\d+", tail)
    return [float(v) for v in values[:3]] or None


def parse_mspt(text):
    """Tick times are avg/min/max per window; keep the three averages."""
    tail = SECTION.sub("", text).split(":", 1)[-1]
    values = [float(v) for v in re.findall(r"\d+\.\d+", tail)]
    averages = values[0::3]
    return averages[:3] or None


# ------------------------------------------------------------- files --------

def read_properties(path):
    props = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    props[key.strip()] = value.strip()
    except OSError:
        pass
    return props


def read_log_head(path, limit=400):
    """First ``limit`` lines of the current log (holds the boot banner)."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        pass
    return lines


def parse_versions(log_lines):
    """Paper / Minecraft / Java / Geyser versions from the boot banner."""
    found = {}
    for line in log_lines:
        match = re.search(r"Loading Paper (\S+).*for Minecraft (\S+)", line)
        if match and "paper" not in found:
            found["paper"], found["minecraft"] = match.group(1), match.group(2)
        match = re.search(r"Running Java (\S+)", line)
        if match and "java" not in found:
            found["java"] = match.group(1)
        match = re.search(r"Loading Geyser version (\S+)", line)
        if match and "geyser" not in found:
            found["geyser"] = match.group(1)
    return found


PLUGIN_NAME = re.compile(r"^name:[ \t]*(.+?)[ \t]*$", re.M)
PLUGIN_VERSION = re.compile(r"^version:[ \t]*(.+?)[ \t]*$", re.M)


def read_plugins(plugins_dir):
    """[{name, version, jar}] read from each plugin.yml inside plugins/*.jar."""
    plugins = []
    try:
        entries = sorted(os.listdir(plugins_dir))
    except OSError:
        return plugins
    for filename in entries:
        if not filename.endswith(".jar"):
            continue
        name = version = None
        try:
            with zipfile.ZipFile(os.path.join(plugins_dir, filename)) as archive:
                text = archive.read("plugin.yml").decode("utf-8", "replace")
            match = PLUGIN_NAME.search(text)
            name = match.group(1).strip("\"'") if match else None
            match = PLUGIN_VERSION.search(text)
            version = match.group(1).strip("\"'") if match else None
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
        plugins.append({
            "name": name or filename.removesuffix(".jar"),
            "version": version or "?",
            "jar": filename,
        })
    return plugins


def dir_size(path):
    """Recursive byte size, skipping unreadable entries."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _find_java_pid(marker=b"paper.jar"):
    """PID of the Paper process (AutoPlug runs it as its own JVM)."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                if marker in handle.read():
                    return int(entry)
        except OSError:
            continue
    return None


def _process_uptime(pid):
    """Seconds since ``pid`` started, from /proc/<pid>/stat field 22."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="ascii") as handle:
            stat = handle.read()
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            boot_seconds = float(handle.read().split()[0])
        start_ticks = float(stat.rsplit(")", 1)[-1].split()[19])
        return max(0.0, boot_seconds - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return None


def _unit_uptime(unit="autoplug.service"):
    """Fall back to the AutoPlug unit's own uptime."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        started_us = int(result.stdout.strip() or "0")
        if started_us <= 0:
            return None
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            boot_seconds = float(handle.read().split()[0])
        return max(0.0, boot_seconds - started_us / 1e6)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def server_uptime_seconds():
    """Uptime of the Minecraft server process, or None."""
    pid = _find_java_pid()
    if pid is not None:
        value = _process_uptime(pid)
        if value is not None:
            return value
    return _unit_uptime()


def server_pid():
    """PID of the Paper process, or None when it is not running."""
    return _find_java_pid()


def unit_state(unit="autoplug.service"):
    """``systemctl is-active``: 'active', 'inactive', 'failed', 'activating', …

    None when the query itself fails, which is different from 'inactive'.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


# ----------------------------------------------------------- formatting -----

def human_size(num):
    if num is None:
        return "—"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
