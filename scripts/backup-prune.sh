#!/usr/bin/env bash
#
# backup-prune.sh - delete old AutoPlug backup archives from a Hetzner
# Storage Box over SFTP.
#
# AutoPlug prunes its *local* directory (backup.max-days) but never the remote
# side, so the box would grow by ~one archive per day forever. This script
# keeps the remote window bounded.
#
# Archive names carry their own date (YYYY-MM-DD-HH.MM-BACKUP.zip), so nothing
# here parses `ls -l` output or remote timestamps - ISO dates just compare as
# strings.
#
# Usage:
#   scripts/backup-prune.sh            # delete archives older than the cutoff
#   scripts/backup-prune.sh --dry-run  # list what would be deleted, touch nothing
#
# Environment (defaults loaded from ~/minecraft/storagebox.env, which the unit
# sets; see systemd/mc-backup-prune.service):
#   SB_HOST         e.g. u123456.your-storagebox.de
#   SB_USER         e.g. u123456
#   SB_KEY          private key         (default: ~/.ssh/id_rsa_storagebox)
#   SB_PATH         directory on the box (default: minecraft)
#   SB_RETAIN_DAYS  keep this many days (default: 30)
#   SB_PORT         SSH port            (default: 22)
#
set -uo pipefail

ENV_FILE="${SB_ENV_FILE:-$HOME/minecraft/storagebox.env}"
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && . "$ENV_FILE"

SB_HOST="${SB_HOST:-}"
SB_USER="${SB_USER:-}"
SB_KEY="${SB_KEY:-$HOME/.ssh/id_rsa_storagebox}"
SB_PATH="${SB_PATH:-minecraft}"
SB_RETAIN_DAYS="${SB_RETAIN_DAYS:-30}"
SB_PORT="${SB_PORT:-22}"

dry_run=0
case "${1:-}" in
    --dry-run|-n) dry_run=1 ;;
    -h|--help) awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"; exit 0 ;;
esac

if [ -z "$SB_HOST" ] || [ -z "$SB_USER" ]; then
    echo "error: SB_HOST and SB_USER must be set (see $ENV_FILE)" >&2
    exit 2
fi

sftp_run() {
    sftp -q -b - \
        -i "$SB_KEY" \
        -P "$SB_PORT" \
        -o BatchMode=yes \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new \
        "$SB_USER@$SB_HOST"
}

cutoff="$(date -u -d "-${SB_RETAIN_DAYS} days" +%F)"
echo "prune ${SB_USER}@${SB_HOST}:${SB_PATH} - keeping archives newer than ${cutoff} UTC"

if ! listing="$(printf 'ls -1 %s\n' "$SB_PATH" | sftp_run 2>&1)"; then
    echo "error: cannot list ${SB_PATH}: ${listing}" >&2
    exit 1
fi

stale=()
while IFS= read -r name; do
    name="${name##*/}"      # some servers echo the full path
    name="${name%$'\r'}"
    case "$name" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*-BACKUP.zip) ;;
        *) continue ;;      # not one of ours; never touch anything else
    esac
    if [[ "${name:0:10}" < "$cutoff" ]]; then
        stale+=("$name")
    fi
done <<< "$listing"

if [ "${#stale[@]}" -eq 0 ]; then
    echo "nothing to prune"
    exit 0
fi

for name in "${stale[@]}"; do
    if [ "$dry_run" = 1 ]; then
        echo "would delete ${SB_PATH}/${name}"
        continue
    fi
    echo "deleting ${SB_PATH}/${name}"
    if ! printf 'rm %s\n' "${SB_PATH}/${name}" | sftp_run >/dev/null 2>&1; then
        echo "warning: failed to delete ${name}" >&2
    fi
done

echo "done: ${#stale[@]} archive(s) past ${SB_RETAIN_DAYS} days"
