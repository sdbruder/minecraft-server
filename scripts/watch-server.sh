#!/usr/bin/env bash
#
# watch-server.sh - push a Pushover alert when the Minecraft server goes down,
# and a second one when it comes back.
#
# This is a ONE-SHOT probe: it runs once a minute from
# systemd/mc-watchdog.timer (there is no daemon and nothing tails a log). A
# probe counts as "up" only when BOTH parts pass:
#
#   1. the AutoPlug unit is active -> systemctl is-active --quiet autoplug
#   2. the RCON port accepts TCP   -> /dev/tcp/127.0.0.1/PORT, under timeout
#
# The port comes from server.properties (rcon.port), so a change there is
# picked up without touching this script.
#
# One failed probe means nothing: the daily 05:00 restart and an AutoPlug
# self-update each take 30-60 s. So the DOWN alert waits for FAILS_BEFORE_ALERT
# consecutive failures, and any up probe resets the counter.
#
# Alerts fire on a TRANSITION only, never repeatedly: down -> "Server down"
# (priority 1), up again -> "Server up" (priority 0, with the outage length).
# While the server stays up, or while an outage continues, nothing is sent.
#
# The state lives in STATE_FILE as plain key=value:
#   state=up|down   "down" means the DOWN alert was already sent
#   fails=N         consecutive failed probes since the last up probe
#   since=EPOCH     start of the current outage, 0 while up
#
# Exit status is always 0 on the normal paths, also when the server is down:
# a failing status would make systemd mark the unit failed every single minute.
#
# Usage:
#   scripts/watch-server.sh          # one probe, alert on a transition
#   scripts/watch-server.sh --help   # print this header
#
# Environment:
#   PUSHOVER_TOKEN     Pushover app API token (required; from pushover.net/apps)
#   PUSHOVER_USER      Pushover user/group key (required)
#   PUSHOVER_URL       Pushover API endpoint (default: https://api.pushover.net/1/messages.json)
#   STATE_FILE         probe state (default: ~/minecraft/server-watch.state)
#   FAILS_BEFORE_ALERT consecutive failed probes before the DOWN alert (default: 3)
#   PROBE_CMD          command line that replaces the built-in probe, for tests.
#                      It prints one line "unit=<state> rcon=<state>" and exits
#                      0 only when the server is up (default: built-in probe)
#   SERVER_PROPERTIES  file read for rcon.port (default: ~/minecraft/server.properties)
#   RCON_HOST          host of the TCP probe         (default: 127.0.0.1)
#   RCON_PORT          fallback when rcon.port is absent (default: 25575)
#   RCON_TIMEOUT       seconds for the TCP probe                    (default: 3)
#   AUTOPLUG_UNIT      systemd unit that runs the server    (default: autoplug)
#   VERBOSE            1 to print the API reply also on success     (default: 0)
#
set -uo pipefail

PUSHOVER_TOKEN="${PUSHOVER_TOKEN:-}"
PUSHOVER_USER="${PUSHOVER_USER:-}"
PUSHOVER_URL="${PUSHOVER_URL:-https://api.pushover.net/1/messages.json}"
STATE_FILE="${STATE_FILE:-$HOME/minecraft/server-watch.state}"
FAILS_BEFORE_ALERT="${FAILS_BEFORE_ALERT:-3}"
PROBE_CMD="${PROBE_CMD:-}"
SERVER_PROPERTIES="${SERVER_PROPERTIES:-$HOME/minecraft/server.properties}"
RCON_HOST="${RCON_HOST:-127.0.0.1}"
RCON_PORT="${RCON_PORT:-25575}"
RCON_TIMEOUT="${RCON_TIMEOUT:-3}"
AUTOPLUG_UNIT="${AUTOPLUG_UNIT:-autoplug}"
VERBOSE="${VERBOSE:-0}"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"
    exit 0
fi

rcon_port() {
    local p=""
    if [ -r "$SERVER_PROPERTIES" ]; then
        p="$(awk -F= '$1 ~ /^[[:space:]]*rcon\.port[[:space:]]*$/ {v=$2; gsub(/[[:space:]]/, "", v); print v; exit}' "$SERVER_PROPERTIES")"
    fi
    case "$p" in
        ''|*[!0-9]*) printf '%s\n' "$RCON_PORT" ;;
        *)           printf '%s\n' "$p" ;;
    esac
}

probe_builtin() {
    # Prints one "unit=... rcon=... port=..." line. Up only when both pass.
    local port unit rcon
    port="$(rcon_port)"
    if systemctl is-active --quiet "$AUTOPLUG_UNIT"; then
        unit="active"
    else
        unit="inactive"
    fi
    # A bare connect: /dev/tcp only succeeds when something listens, and
    # `timeout` bounds it so a half-open port cannot stall the timer run.
    if timeout "$RCON_TIMEOUT" bash -c "exec 3<>/dev/tcp/$RCON_HOST/$port" 2>/dev/null; then
        rcon="ok"
    else
        rcon="unreachable"
    fi
    echo "unit=$unit rcon=$rcon port=$port"
    [ "$unit" = active ] && [ "$rcon" = ok ]
}

run_probe() {
    # Runs PROBE_CMD instead of the built-in probe when it is set, so a test
    # can force up or down with no Minecraft server and without editing this
    # script. Sets PROBE_LINE to the last line the probe printed.
    local out rc
    if [ -n "$PROBE_CMD" ]; then
        out="$(bash -c "$PROBE_CMD" 2>&1)"
    else
        out="$(probe_builtin 2>&1)"
    fi
    rc=$?
    PROBE_LINE="${out##*$'\n'}"
    return "$rc"
}

down_reason() {
    # What failed, for the DOWN message: reads the probe's "unit=/rcon=/port="
    # line and names the parts that did not pass.
    local line="$1" part unit="" rcon="" port="" reason=""
    for part in $line; do
        case "$part" in
            unit=*) unit="${part#unit=}" ;;
            rcon=*) rcon="${part#rcon=}" ;;
            port=*) port="${part#port=}" ;;
        esac
    done
    [ "$unit" = active ] || reason="unit $AUTOPLUG_UNIT not active"
    if [ "$rcon" != ok ]; then
        [ -n "$port" ] || port="$(rcon_port)"
        [ -n "$reason" ] && reason="$reason and "
        reason="${reason}RCON port $port unreachable"
    fi
    if [ -z "$reason" ]; then
        # Probe did not speak our format: report whatever it did say.
        reason="probe failed: ${line:-no output}"
    fi
    printf '%s\n' "$reason"
}

format_duration() {
    local secs="$1"
    if [ "$secs" -lt 60 ]; then
        echo "${secs}s"
    elif [ "$secs" -lt 3600 ]; then
        echo "$((secs / 60))m $((secs % 60))s"
    else
        echo "$((secs / 3600))h $((secs % 3600 / 60))m"
    fi
}

utc_time() {
    # Epoch -> HH:MM:SS UTC. GNU date and BSD date disagree on the flag.
    date -u -d "@$1" +%H:%M:%S 2>/dev/null || date -u -r "$1" +%H:%M:%S
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

alert() {
    # $1 tag, $2 title, $3 priority, $4 message
    local tag="$1" http
    http="$(pushover_send "$2" "$3" "$4")"
    if [ "$VERBOSE" = 1 ] || [ "$http" != 200 ]; then
        echo "pushover[$tag] http=$http" >&2
    fi
}

read_state() {
    STATE=up
    FAILS=0
    SINCE=0
    [ -r "$STATE_FILE" ] || return 0
    local key val
    while IFS='=' read -r key val; do
        case "$key" in
            state) [ "$val" = down ] && STATE=down ;;
            fails) case "$val" in ''|*[!0-9]*) ;; *) FAILS="$val" ;; esac ;;
            since) case "$val" in ''|*[!0-9]*) ;; *) SINCE="$val" ;; esac ;;
        esac
    done <"$STATE_FILE"
    return 0
}

write_state() {
    # Replace through a temp file so a killed run cannot leave a half line.
    local tmp="${STATE_FILE}.tmp.$$"
    if ! printf 'state=%s\nfails=%s\nsince=%s\n' "$STATE" "$FAILS" "$SINCE" >"$tmp" 2>/dev/null ||
       ! mv -f "$tmp" "$STATE_FILE" 2>/dev/null; then
        echo "warning: cannot write $STATE_FILE" >&2
        rm -f "$tmp" 2>/dev/null
    fi
    return 0
}

read_state
now="$(date +%s)"

if run_probe; then
    # Up: the counter always resets, also when no alert is due.
    FAILS=0
    if [ "$STATE" = down ]; then
        down_for=0
        [ "$SINCE" -gt 0 ] && down_for=$((now - SINCE))
        [ "$down_for" -lt 0 ] && down_for=0
        msg="Minecraft server is up at $(utc_time "$now") UTC after $(format_duration "$down_for")"
        [ "$SINCE" -gt 0 ] && msg="$msg (down since $(utc_time "$SINCE") UTC)"
        alert up "Server up" 0 "$msg"
        STATE=up
        SINCE=0
    fi
    echo "server up"
else
    if [ "$STATE" = up ]; then
        FAILS=$((FAILS + 1))
        if [ "$FAILS" -ge "$FAILS_BEFORE_ALERT" ]; then
            STATE=down
            SINCE="$now"
            alert down "Server down" 1 \
                "Minecraft server down at $(utc_time "$now") UTC: $(down_reason "$PROBE_LINE")"
        fi
    fi
    echo "server down (failed probes: $FAILS of $FAILS_BEFORE_ALERT)"
fi

write_state
exit 0