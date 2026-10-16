#!/usr/bin/env bash
#
# alert-nonwhitelist.sh - push a Pushover alert when a player joins or leaves
# the Minecraft server, and when a non-whitelisted player tries to join.
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
# For a leave it matches Paper's "left the game" line, which carries the
# (Floodgate-prefixed) name and comes in two shapes because a chat plugin
# sometimes rewrites system messages:
#
#   [Server thread/INFO]: Steve left the game
#   [Server thread/INFO]: System chat: Steve left the game
#
# The other candidate, "NAME lost connection: Disconnected", is deliberately
# NOT used: scanners that never join (FishNet and friends) emit it too, and a
# real quit usually logs both lines, so it would mean alerts for bots and
# duplicates for players.
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
#   LOG            server log to follow   (default: ~/minecraft/logs/latest.log)
#   PUSHOVER_TOKEN Pushover app API token (required; from pushover.net/apps)
#   PUSHOVER_USER  Pushover user/group key (required)
#   PUSHOVER_URL   Pushover API endpoint  (default: https://api.pushover.net/1/messages.json)
#   COOLDOWN       per-name seconds, tracked separately for joins and for
#                  leaves so a leave is never hidden by the preceding join
#                                          (default: 300)
#   IGNORE         space-separated names  (default: FishNet)
#   EVENTS         blocked-join log       (default: ~/minecraft/logs/blocked-joins.log)
#   VERBOSE        1 to print API reply   (default: 0)
#
set -uo pipefail

LOG="${LOG:-$HOME/minecraft/logs/latest.log}"
PUSHOVER_TOKEN="${PUSHOVER_TOKEN:-}"
PUSHOVER_USER="${PUSHOVER_USER:-}"
PUSHOVER_URL="${PUSHOVER_URL:-https://api.pushover.net/1/messages.json}"
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
declare -A last_left=()
last_bedrock_ip=""
last_bedrock_ts=0

valid_name() { [[ "$1" =~ ^[A-Za-z0-9_.]{1,16}$ ]]; }

is_ignored() {
    local n="$1" x
    for x in $IGNORE; do [ "$x" = "$n" ] && return 0; done
    return 1
}

pushover_send() {
    # $1 title, $2 priority (-2..2), $3 message. Prints the HTTP status code.
    local title="$1" priority="$2" msg="$3"
    if [ -z "$PUSHOVER_TOKEN" ] || [ -z "$PUSHOVER_USER" ]; then
        echo "pushover: PUSHOVER_TOKEN and PUSHOVER_USER must be set" >&2
        return 1
    fi
    curl -sS --max-time 15 -o /dev/null -w '%{http_code}' \
        --form-string "token=$PUSHOVER_TOKEN" \
        --form-string "user=$PUSHOVER_USER" \
        --form-string "title=$title" \
        --form-string "priority=$priority" \
        --form-string "message=$msg" \
        "$PUSHOVER_URL" 2>&1
}

send_alert() {
    local name="$1" ip="$2" ts="$3" now http
    now="$(date +%s)"
    if [ "${last_seen[$name]:-0}" != 0 ]; then
        [ "$((now - last_seen[$name]))" -lt "$COOLDOWN" ] && return 0
    fi
    last_seen[$name]="$now"

    printf '%s\t%s\t%s\t%s\n' "$ts" "$name" "$ip" "$(date +%Y-%m-%dT%H:%M:%S%z)" >>"$EVENTS" 2>/dev/null

    http="$(pushover_send "Blocked join: $name" 1 "$name tried to join from $ip at $ts UTC (not whitelisted)")"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "pushover[blocked:$name] http=$http" >&2
    fi
}

send_join_alert() {
    local name="$1" ip="$2" ts="$3" now http
    now="$(date +%s)"
    if [ "${last_join[$name]:-0}" != 0 ]; then
        [ "$((now - last_join[$name]))" -lt "$COOLDOWN" ] && return 0
    fi
    last_join[$name]="$now"

    http="$(pushover_send "Joined: $name" 0 "$name joined from $ip at $ts UTC")"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "pushover[join:$name] http=$http" >&2
    fi
}

send_left_alert() {
    # Own cooldown map: sharing one with joins would swallow a leave that
    # follows a join within COOLDOWN, which is exactly when it matters.
    local name="$1" kind="$2" ts="$3" now http
    now="$(date +%s)"
    if [ "${last_left[$name]:-0}" != 0 ]; then
        [ "$((now - last_left[$name]))" -lt "$COOLDOWN" ] && return 0
    fi
    last_left[$name]="$now"

    http="$(pushover_send "Left: $name" 0 "$name ($kind) left the server at $ts UTC")"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "pushover[left:$name] http=$http" >&2
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

    # Left the game. "System chat: " is checked first: the plain pattern below
    # would otherwise capture the literal "System chat: name" as the name.
    #   [Server thread/INFO]: Steve left the game
    #   [Server thread/INFO]: System chat: Steve left the game
    case "$line" in
        *"System chat: "*" left the game"*)
            name="${line#*System chat: }" ;;
        *"]: "*" left the game"*)
            name="${line#*]: }" ;;
        *)
            name="" ;;
    esac
    if [ -n "$name" ]; then
        name="${name%% left the game*}"
        kind="java"
        if [[ "$name" == .* ]]; then
            name="${name#.}"; kind="bedrock"   # Floodgate username-prefix
        fi
        valid_name "$name" || return 0
        is_ignored "$name" && return 0
        send_left_alert "$name" "$kind" "$ts"
        return 0
    fi

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
    echo "watching $LOG -> pushover (ignoring: $IGNORE)" >&2
    if [ -z "$PUSHOVER_TOKEN" ] || [ -z "$PUSHOVER_USER" ]; then
        echo "warning: PUSHOVER_TOKEN/PUSHOVER_USER not set; alerts will not send" >&2
    fi
    tail -n0 -F "$LOG" 2>/dev/null | while IFS= read -r line; do process_line "$line"; done
fi
