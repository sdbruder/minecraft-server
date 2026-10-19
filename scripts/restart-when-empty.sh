#!/usr/bin/env bash
#
# restart-when-empty.sh - restart the Minecraft server once nobody is playing.
#
# The dashboard's "Restart when empty" writes
# ~/minecraft/restart-when-empty.request, and
# systemd/mc-restart-when-empty.path fires this script (as root), which consumes
# the request and then waits for the last player to leave.
#
# The queue *is* the state file: it exists while the restart is waiting, so the
# dashboard can show it ("restart queued for when the server empties — 2 online,
# queued 19:52 UTC") and cancel it by deleting the file — this script notices
# within one poll interval and exits. The file is also removed on every exit
# path, so a killed or failed run cannot leave a phantom queue behind.
#
# When it acts:
#   - RCON reports 0 players                                  -> restart
#   - RCON reports nothing and UNIT has been inactive for INACTIVE_POLLS polls
#     in a row (nobody can be playing on a server that is not running)
#                                                             -> restart
#   - MAX_WAIT seconds have passed                            -> give up
#
# Usage:
#   scripts/restart-when-empty.sh            # one queue, then wait
#   scripts/restart-when-empty.sh --help
#
# Environment:
#   REQUEST         trigger file      (default: ~/minecraft/restart-when-empty.request)
#   STATE           queue file        (default: ~/minecraft/restart-when-empty.state)
#   UNIT            systemd unit      (default: autoplug)
#   RESTART_CMD     what to run       (default: systemctl restart autoplug)
#   POLL_SECONDS    poll interval     (default: 30)
#   MAX_WAIT        give up after     (default: 43200 = 12 h; 0 waits forever)
#   INACTIVE_POLLS  polls of a dead unit before acting anyway  (default: 3)
#   RCON_CMD        player count      (default: scripts/rcon-list.py beside this file)
#   VERBOSE         1 logs every poll (default: 0)
#
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REQUEST="${REQUEST:-$HOME/minecraft/restart-when-empty.request}"
STATE="${STATE:-$HOME/minecraft/restart-when-empty.state}"
UNIT="${UNIT:-autoplug}"
RESTART_CMD="${RESTART_CMD:-systemctl restart autoplug}"
POLL_SECONDS="${POLL_SECONDS:-30}"
MAX_WAIT="${MAX_WAIT:-43200}"
INACTIVE_POLLS="${INACTIVE_POLLS:-3}"
RCON_CMD="${RCON_CMD:-$HERE/rcon-list.py}"
VERBOSE="${VERBOSE:-0}"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"
    exit 0
fi

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

do_restart() {
    rm -f "$STATE"                      # the queue is over, whatever happens next
    log "running: $RESTART_CMD"
    if bash -c "$RESTART_CMD"; then
        log "restart command finished"
    else
        log "restart command failed (exit $?)"
    fi
    exit 0
}

if [ -e "$STATE" ]; then
    # One queue at a time. The path unit will not fire a second run while this
    # one is active and the dashboard refuses to queue one, so this is a belt
    # for a manual run: drop the request instead of racing a second waiter.
    log "already queued (see $STATE) — dropping this request"
    rm -f "$REQUEST"
    exit 0
fi

queued="$(date +%s)"
rm -f "$REQUEST"                        # consume the trigger so the path unit re-arms
printf 'queued %s\n' "$queued" >"$STATE" || { log "cannot write $STATE"; exit 1; }
# Both files go on the way out: a request that arrived while this run was
# waiting (only a manual one can) must not start a second queue later.
trap 'rm -f "$STATE" "$REQUEST"' EXIT INT TERM
log "queued: waiting for an empty server (max ${MAX_WAIT}s, poll ${POLL_SECONDS}s)"

inactive=0
while :; do
    if [ ! -e "$STATE" ]; then
        log "cancelled"
        exit 0
    fi
    if [ "$MAX_WAIT" -gt 0 ] && [ "$(( $(date +%s) - queued ))" -ge "$MAX_WAIT" ]; then
        log "gave up: still busy after ${MAX_WAIT}s"
        exit 0
    fi

    count="$("$RCON_CMD" 2>/dev/null)" || count="unknown"
    case "$count" in
        ''|*[!0-9]*) count="unknown" ;;
    esac

    if [ "$count" = 0 ]; then
        log "empty -> restarting"
        do_restart
    fi

    if [ "$count" = unknown ]; then
        if systemctl is-active --quiet "$UNIT"; then
            inactive=0
        else
            inactive=$((inactive + 1))
        fi
        if [ "$inactive" -ge "$INACTIVE_POLLS" ]; then
            log "$UNIT is not running -> restarting"
            do_restart
        fi
    else
        inactive=0
    fi

    [ "$VERBOSE" = 1 ] && log "waiting: ${count} online"
    sleep "$POLL_SECONDS"
done
