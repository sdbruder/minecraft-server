#!/usr/bin/env bash
#
# alert-nonwhitelist.sh - push an ntfy alert when a player joins the Minecraft
# server, and when a non-whitelisted player tries to join.
#
# It follows the server log. For a successful join it matches the entity line,
# which carries name and IP for both Java and Bedrock players:
#
#   [Server thread/INFO]: Steve[/203.0.113.11:12733] logged in with entity id 78 at (...)
#   [Server thread/INFO]: .Kai[/203.0.113.12:0] logged in with entity id 51244 at (...)
#
# Floodgate prefixes Bedrock usernames with "." (username-prefix), so a leading
# dot marks the player as Bedrock and is stripped from the alert.
#
# For a rejected join it matches the whitelist rejection lines Paper and Geyser
# write. Java players get name + IP on one line:
#
#   [20:20:10] [Server thread/INFO]: Disconnecting Alex (/203.0.113.10:55658): You are not whitelisted on this server!
#
# Bedrock (Geyser) players split across two lines: the Java side has only the
# IP, the username is on Geyser's line. The script pairs them.
#
#   [Server thread/INFO]: Disconnecting /203.0.113.13:0: You are not whitelisted on this server!
#   [Geyser-Spigot] Kai has disconnected from the Java server because of §rYou are not whitelisted on this server!
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
#   EVENTS     blocked-join log       (default: ~/minecraft/logs/blocked-joins.log)
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
declare -A last_join=()
last_bedrock_ip=""
last_bedrock_ts=0

valid_name() { [[ "$1" =~ ^[A-Za-z0-9_.]{1,16}$ ]]; }

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

send_join_alert() {
    local name="$1" ip="$2" ts="$3" now http
    now="$(date +%s)"
    if [ "${last_join[$name]:-0}" != 0 ]; then
        [ "$((now - last_join[$name]))" -lt "$COOLDOWN" ] && return 0
    fi
    last_join[$name]="$now"

    http="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' \
        -H "Title: Joined: $name" \
        -H "Tags: white_check_mark" \
        -d "$name joined from $ip at $ts UTC" \
        "$NTFY_URL/$NTFY_TOPIC" 2>&1)"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "ntfy[join:$name] http=$http topic=$NTFY_TOPIC" >&2
    fi
}

process_line() {
    local line="$1" rest name ip ts now kind
    ts="${line#\[}"; ts="${ts%%\]*}"

    # Successful join. The entity line has name and IP for Java and Bedrock:
    #   [Server thread/INFO]: Steve[/203.0.113.11:12733] logged in with entity id 78 at (...)
    #   [Server thread/INFO]: .Kai[/203.0.113.12:0] logged in with entity id 51244 at (...)
    # This line is preferred over the alternatives, which are unreliable:
    #   - "X joined the game" is sometimes rewritten to "System chat: X joined
    #     the game" by a chat plugin, and carries no IP.
    #   - "[User Authenticator #N/INFO]: UUID of player X is ..." fires on auth
    #     attempts that can still be rejected by the whitelist.
    case "$line" in
        *"]: "*"[/"*"] logged in with entity id "*)
            rest="${line#*]: }"
            name="${rest%%\[*}"
            rest="${rest#*\[/}"
            ip="${rest%%:*}"
            kind="java"
            if [[ "$name" == .* ]]; then
                name="${name#.}"; kind="bedrock"   # Floodgate username-prefix
            fi
            valid_name "$name" || return 0
            is_ignored "$name" && return 0
            send_join_alert "$name" "$ip ($kind)" "$ts"
            return 0
            ;;
    esac

    # Any whitelist rejection line that starts with "Disconnecting ".
    case "$line" in
        *"Disconnecting "*"not whitelisted on this server!"*)
            rest="${line#*Disconnecting }"
            if [[ "$rest" == /* ]]; then
                # Bedrock, Java-side line: no username, only the IP.
                #   [Server thread/INFO]: Disconnecting /203.0.113.13:0: You are not whitelisted on this server!
                ip="${rest#/}"; ip="${ip%%:*}"
                last_bedrock_ip="$ip"; last_bedrock_ts="$(date +%s)"
                return 0
            fi
            # Java player: name and IP on one line.
            #   [Server thread/INFO]: Disconnecting Alex (/203.0.113.10:55658): You are not whitelisted on this server!
            name="${rest%% (/*}"
            valid_name "$name" || return 0
            ip="${rest#*(/}"; ip="${ip%%:*}"
            is_ignored "$name" && return 0
            send_alert "$name" "$ip" "$ts"
            return 0
            ;;
    esac

    # Bedrock, Geyser-side line: has the real username + reason.
    #   [Geyser-Spigot] Kai has disconnected from the Java server because of §rYou are not whitelisted on this server!
    case "$line" in
        *"[Geyser-Spigot] "*" has disconnected from the Java server because of "*"not whitelisted on this server!"*)
            name="${line#*\[Geyser-Spigot\] }"; name="${name%% has disconnected*}"
            valid_name "$name" || return 0
            is_ignored "$name" && return 0
            now="$(date +%s)"
            if [ -n "$last_bedrock_ip" ] && [ "$((now - last_bedrock_ts))" -le 15 ]; then
                ip="$last_bedrock_ip (bedrock)"
            else
                ip="unknown (bedrock)"
            fi
            send_alert "$name" "$ip" "$ts"
            return 0
            ;;
    esac
    return 0
}

if [ "${1:-}" = "--stdin" ]; then
    while IFS= read -r line; do process_line "$line"; done
else
    echo "watching $LOG -> $NTFY_URL/$NTFY_TOPIC (ignoring: $IGNORE)" >&2
    tail -n0 -F "$LOG" 2>/dev/null | while IFS= read -r line; do process_line "$line"; done
fi
