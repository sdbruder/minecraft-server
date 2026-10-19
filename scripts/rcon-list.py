#!/usr/bin/env python3
"""Print how many players are online, asked over RCON.

The ops scripts need the player count and there is no mcrcon on the server, so
this is the small client they share. It reads `rcon.port` and `rcon.password`
from server.properties and runs `list`.

Output (stdout):

    3           the number of players online
    unknown     no usable answer — nothing listening, wrong password, or an
                unparseable reply. The exit status is then 2, so a caller can
                tell "nobody is playing" from "no idea", which matters before
                restarting a server.

Usage:
    scripts/rcon-list.py                 # print the count
    scripts/rcon-list.py --names         # print one name per line
    scripts/rcon-list.py --host 127.0.0.1 --properties /path/server.properties

Environment:
    SERVER_PROPERTIES   file to read        (default: ~/minecraft/server.properties)
    RCON_HOST           host to connect to  (default: 127.0.0.1)
    RCON_PORT           fallback port       (default: 25575)
    RCON_TIMEOUT        seconds             (default: 5)
"""

import os
import re
import socket
import struct
import sys
from pathlib import Path

PLAYERS = re.compile(r"There are (\d+) of a max of (\d+) players online:?\s*(.*)")


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


def read_exact(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise OSError("connection closed by server")
        buf += chunk
    return buf


def rcon(host, port, password, command, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)

        def send(request_id, ptype, body):
            payload = struct.pack("<ii", request_id, ptype) + body.encode() + b"\x00\x00"
            sock.sendall(struct.pack("<i", len(payload)) + payload)

        def response():
            (length,) = struct.unpack("<i", read_exact(sock, 4))
            data = read_exact(sock, length)
            return struct.unpack("<ii", data[:8])[0], data[8:-2].decode("utf-8", "replace")

        send(1, 3, password)                       # SERVERDATA_AUTH
        request_id, _ = response()
        if request_id == -1:
            raise OSError("authentication failed")
        send(2, 2, command)                        # SERVERDATA_EXECCOMMAND
        return response()[1]


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0

    properties = Path(os.environ.get("SERVER_PROPERTIES")
                      or Path.home() / "minecraft" / "server.properties")
    host = os.environ.get("RCON_HOST", "127.0.0.1")
    timeout = float(os.environ.get("RCON_TIMEOUT", "5"))
    names_only = "--names" in argv

    for index, arg in enumerate(argv):
        if arg == "--properties" and index + 1 < len(argv):
            properties = Path(argv[index + 1])
        elif arg == "--host" and index + 1 < len(argv):
            host = argv[index + 1]

    props = read_properties(properties)
    port = int(props.get("rcon.port") or os.environ.get("RCON_PORT", "25575"))
    password = props.get("rcon.password", "")
    if not password:
        print("unknown")
        print(f"no rcon.password in {properties}", file=sys.stderr)
        return 2

    try:
        text = rcon(host, port, password, "list", timeout)
    except (OSError, struct.error) as exc:
        print("unknown")
        print(f"rcon {host}:{port}: {exc}", file=sys.stderr)
        return 2

    match = PLAYERS.search(text)
    if not match:
        print("unknown")
        print(f"unexpected reply: {text.strip()[:120]!r}", file=sys.stderr)
        return 2

    count, _maximum, listing = int(match.group(1)), int(match.group(2)), match.group(3)
    if names_only:
        for name in (part.strip() for part in listing.split(",")):
            if name:
                print(name)
    else:
        print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
