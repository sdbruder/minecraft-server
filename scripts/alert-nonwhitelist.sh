#!/usr/bin/env bash
#
# alert-nonwhitelist.sh - push an ntfy alert when a non-whitelisted player
# tries to join the Minecraft server.
#
# It follows the server log and matches the whitelist rejection line that
# Paper writes, which contains the name and IP, e.g.:
#
#   [20:20:10] [Server thread/INFO]: Disconnecting Alex (/203.0.113.10:55658): You are not whitelisted on this server!
#
# Note: scanners like FishNet never reach the whitelist check (they disconnect
# during the handshake) so they do not produce that line and are ignored for
# free. IGNORE exists only as a belt-and-braces extra blocklist.
#
# Usage:
#   scripts/alert-nonwhitelist.sh            # tail the log forever
#   scripts/alert-nonwhitelist.sh --stdin    # read lines from stdin (for tests)
#
# Environment:
#   LOG        server log to follow   (default: ~/minecraft/logs/latest.log)
#   NTFY_URL   ntfy base url          (default: https://ntfy.sh)
#   NTFY_TOPIC ntfy topic for phone   (default: see below)
#   COOLDOWN   per-name seconds       (default: 300)
#   IGNORE     space-separated names  (default: FishNet)
#   EVENTS     local append log       (default: ~/minecraft/logs/blocked-joins.log)
#   VERBOSE    1 to print ntfy reply  (default: 0)
#
set -uo pipefail

LOG="${LOG:-$HOME/minecraft/logs/latest.log}"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-mc-familia-REDACTED}"
COOLDOWN="${COOLDOWN:-300}"
IGNORE="${IGNORE:-FishNet}"
EVENTS="${EVENTS:-$HOME/minecraft/logs/blocked-joins.log}"
VERBOSE="${VERBOSE:-0}"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"
    exit 0
fi

declare -A last_seen=()

is_ignored() {
    local n="$1" x
    for x in $IGNORE; do [ "$x" = "$n" ] && return 0; done
    return 1
}

send_alert() {
    local name="$1" ip="$2" ts="$3" now http
    now="$(date +%s)"
    if [ "${last_seen[$name]:-0}" != 0 ]; then
        [ "$((now - last_seen[$name]))" -lt "$COOLDOWN" ] && return 0
    fi
    last_seen[$name]="$now"

    printf '%s\t%s\t%s\t%s\n' "$ts" "$name" "$ip" "$(date +%Y-%m-%dT%H:%M:%S%z)" >>"$EVENTS" 2>/dev/null

    http="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' \
        -H "Title: Blocked join: $name" \
        -H "Priority: high" \
        -H "Tags: no_entry" \
        -d "$name tried to join from $ip at $ts UTC (not whitelisted)" \
        "$NTFY_URL/$NTFY_TOPIC" 2>&1)"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "ntfy[$name] http=$http topic=$NTFY_TOPIC" >&2
    fi
}

process_line() {
    local line="$1" rest name ip ts
    case "$line" in
        *"Disconnecting "*": You are not whitelisted on this server!"*) ;;
        *) return 0 ;;
    esac
    rest="${line#*Disconnecting }"
    name="${rest%% (/*}"
    ip="${rest#*(/}"; ip="${ip%%:*}"
    ts="${line#\[}"; ts="${ts%%\]*}"
    [ -n "$name" ] || return 0
    is_ignored "$name" && return 0
    send_alert "$name" "$ip" "$ts"
}

if [ "${1:-}" = "--stdin" ]; then
    while IFS= read -r line; do process_line "$line"; done
else
    echo "watching $LOG -> $NTFY_URL/$NTFY_TOPIC (ignoring: $IGNORE)" >&2
    tail -n0 -F "$LOG" 2>/dev/null | while IFS= read -r line; do process_line "$line"; done
fi
