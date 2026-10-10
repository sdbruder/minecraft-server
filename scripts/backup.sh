#!/usr/bin/env bash
#
# backup.sh - make a compressed "essentials" backup of a remote Minecraft
# server tree into this repository.
#
# What it backs up:
#   the AutoPlug wrapper (AutoPlug-Client.jar) + start.sh, the Paper server
#   jar, root configs, config/, .paper/, família/, plugins/, logs/ and the
#   AutoPlug *.yml files.
# This is enough to restart from scratch: AutoPlug re-fetches Java and Paper
# re-fetches its libraries/cache on first start. To boot fully offline, also
# capture autoplug/system/jre, libraries/ and cache/ (see the comment at the
# INCLUDES list).
# It skips the nested autoplug/backups/, plugins/.paper-remapped/ (regenerated
# by Paper) and the unused vanilla/spigot jars + versions/.
#
# Usage:
#   scripts/backup.sh [REMOTE_HOST] [REMOTE_DIR]
#
# Environment overrides:
#   REMOTE      ssh host            (default: host.example.com)
#   REMOTE_DIR  dir on the host     (default: ~/minecraft)
#   OUT_DIR     local output dir    (default: repository root)
#   STAMP       filename suffix     (default: remote date, e.g. 2026-10-10)
#
# Example:
#   scripts/backup.sh
#   REMOTE=other.example.com REMOTE_DIR=~/srv/mc OUT_DIR=/tmp scripts/backup.sh
#
set -o pipefail

REMOTE="${REMOTE:-host.example.com}"
REMOTE_DIR="${REMOTE_DIR:-~/minecraft}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$(dirname "$SCRIPT_DIR")}"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"
    exit 0
fi
[ -n "${1:-}" ] && REMOTE="$1"
[ -n "${2:-}" ] && REMOTE_DIR="$2"

# Files/dirs to include. Names must not contain spaces.
INCLUDES=(
    AutoPlug-Client.jar
    paper.jar
    start.sh
    server.properties
    server.properties.pre-26.2-downgrade.bak
    bukkit.yml
    spigot.yml
    commands.yml
    eula.txt
    ops.json
    whitelist.json
    banned-ips.json
    banned-players.json
    usercache.json
    config
    .paper
    família
    plugins
    logs
    autoplug/general.yml
    autoplug/updater.yml
    autoplug/plugins.yml
    autoplug/mods.yml
    autoplug/shared-files.yml
    autoplug/logger.yml
    autoplug/ssh.yml
    autoplug/web.yml
    autoplug/restarter.yml
    autoplug/backup.yml
    autoplug/system/config.yml
)

TAR_OPTS="--ignore-failed-read --warning=no-file-changed --warning=no-file-removed --exclude=plugins/.paper-remapped"

# Filename date comes from the remote host so it matches the server's clock.
if [ -z "${STAMP:-}" ]; then
    STAMP="$(ssh -T "$REMOTE" 'date +%Y-%m-%d')" || { echo "error: cannot reach $REMOTE" >&2; exit 1; }
fi

NAME="minecraft-essentials-${STAMP}.tar.gz"
mkdir -p "$OUT_DIR"
FINAL="$OUT_DIR/$NAME"
TMP="$OUT_DIR/.$NAME.tmp.$$"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

echo "backing up $REMOTE:$REMOTE_DIR -> $FINAL"
# A nonzero exit here can be a harmless tar warning (files changed while
# being read), so the archive integrity check below is the real gate.
ssh -T "$REMOTE" "cd $REMOTE_DIR && tar czf - $TAR_OPTS ${INCLUDES[*]}" >"$TMP"

if ! gzip -t "$TMP" 2>/dev/null; then
    echo "error: archive stream is not a valid gzip (transfer failed?)" >&2
    exit 1
fi

entries="$(tar tzf "$TMP" | wc -l | tr -d ' ')"
if [ "$entries" -lt 10 ]; then
    echo "error: archive only has $entries entries, expected many more" >&2
    exit 1
fi

mv "$TMP" "$FINAL"
trap - EXIT

size="$(du -h "$FINAL" | cut -f1)"
if command -v shasum >/dev/null 2>&1; then
    sum="$(shasum -a 256 "$FINAL" | cut -d' ' -f1)"
else
    sum="$(sha256sum "$FINAL" | cut -d' ' -f1)"
fi

echo "done: $FINAL"
echo "  size    : $size"
echo "  entries : $entries"
echo "  sha256  : $sum"
