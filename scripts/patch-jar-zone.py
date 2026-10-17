#!/usr/bin/env python3
"""Rewrite a timezone ID inside the class files of a jar.

Why this exists (2026-10-17)
----------------------------
Modrinth's PlaytimeLimit (project DMhofPzM, 1.0.0) hardcodes
``ZoneId.of("Asia/Shanghai")`` in three classes and has no ``timezone`` key in
its config. Its "day" - and therefore the daily playtime reset - is midnight in
Shanghai, which is 13:00 in Sao Paulo, so an evening of play counts into the
next local day. Rewriting that one constant is the only way to move the reset
to local midnight.

How it does it
--------------
A CONSTANT_Utf8 entry in a class file is ``tag(1) length(u2) bytes``. This
replaces that whole entry in place: the tag stays, the length and the bytes
change, and every later byte of the class shifts by the size difference. No
other part of a class file depends on absolute offsets (only counts and
per-entry lengths), so the result is a valid class file.

The patched jar no longer matches the upstream hash - keep the original next to
it, which is why this refuses to overwrite its input.

Usage:
    scripts/patch-jar-zone.py IN.jar OLD_ZONE NEW_ZONE OUT.jar

Example:
    scripts/patch-jar-zone.py PlaytimeLimit-1.0.0.jar.orig \
        Asia/Shanghai America/Sao_Paulo PlaytimeLimit-1.0.0.jar
"""

import hashlib
import os
import sys
import zipfile

TAG_UTF8 = 1


def patch_class(data, old, new):
    """Replace every CONSTANT_Utf8 entry equal to ``old``. Returns (bytes, n)."""
    needle = bytes([TAG_UTF8]) + len(old).to_bytes(2, "big") + old.encode()
    replacement = bytes([TAG_UTF8]) + len(new).to_bytes(2, "big") + new.encode()
    count = data.count(needle)
    return (data.replace(needle, replacement), count) if count else (data, 0)


def main(argv):
    if len(argv) != 5:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source, old_zone, new_zone, target = argv[1:5]
    if os.path.abspath(source) == os.path.abspath(target):
        print("refusing to overwrite the input jar; pass a different output", file=sys.stderr)
        return 2
    if len(old_zone.encode()) == len(new_zone.encode()):
        print("note: same length, so the constant length prefix is unchanged")

    total = 0
    members = 0
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith(".class"):
                data, count = patch_class(data, old_zone, new_zone)
                if count:
                    members += 1
                    total += count
                    print(f"  patched {count}x {old_zone} -> {new_zone} in {info.filename}")
            out.writestr(info, data)

    if not total:
        os.remove(target)
        print(f"error: {old_zone} not found in any class of {source}", file=sys.stderr)
        return 1

    def digest(path, algorithm):
        with open(path, "rb") as handle:
            return hashlib.new(algorithm, handle.read()).hexdigest()

    print(f"\n{members} class file(s), {total} constant(s) replaced")
    for path in (source, target):
        print(f"  {os.path.getsize(path):>8} bytes  sha256 {digest(path, 'sha256')[:32]}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
