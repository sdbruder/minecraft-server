#!/usr/bin/env bash
#
# deploy.sh - render this repository's unit templates with the site values and
# put them on the host.
#
# The repository is host-agnostic: systemd/ and nginx/ carry placeholders
# (`steve`, `/home/steve`, `host.example.com`), because systemd resolves User=,
# Group=, ExecStart= and EnvironmentFile= before any environment exists and so
# cannot fill them in at runtime. scripts/install-units.sh does the rendering;
# this script is the remote half: render here, push there, install, reload.
#
# Nothing happens without --apply. The default is a dry run that renders into a
# temporary directory and diffs it against what the host has right now.
#
# Usage:
#   scripts/deploy.sh                  # dry run against $SITE_SSH_HOST
#   scripts/deploy.sh --apply          # install units + daemon-reload
#   scripts/deploy.sh --apply --nginx  # also install the nginx conf and reload
#   scripts/deploy.sh --host other.example.com
#
# Environment (see deploy.env, deploy.env.example):
#   SITE_SSH_HOST     ssh target       (default: SITE_SERVER_HOST)
#   SITE_NGINX_CONF   rendered conf's destination on the host (for --nginx)
#   DEPLOY_ENV        site values      (default: <repo>/deploy.env)
#
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_ENV="${DEPLOY_ENV:-$HERE/deploy.env}"
apply=0
nginx=0
host_override=""

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) apply=1; shift ;;
        --nginx) nginx=1; shift ;;
        --host) host_override="${2:?--host needs a value}"; shift 2 ;;
        -h|--help) awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ ! -r "$DEPLOY_ENV" ]; then
    echo "error: $DEPLOY_ENV not found - copy deploy.env.example and fill it in" >&2
    exit 2
fi
# shellcheck disable=SC1090
. "$DEPLOY_ENV"

HOST="${host_override:-${SITE_SSH_HOST:-${SITE_SERVER_HOST:-}}}"
if [ -z "$HOST" ]; then
    echo "error: set SITE_SSH_HOST (or SITE_SERVER_HOST) in $DEPLOY_ENV" >&2
    exit 2
fi
if [ "$nginx" = 1 ] && [ -z "${SITE_NGINX_CONF:-}" ]; then
    echo "error: --nginx needs SITE_NGINX_CONF in $DEPLOY_ENV" >&2
    exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

echo "host: ${HOST}"
echo "render -> $work"
if ! "$HERE/scripts/install-units.sh" --out "$work" >"$work/render.log" 2>&1; then
    cat "$work/render.log" >&2
    echo "error: rendering failed" >&2
    exit 1
fi
rendered=("$work"/*.service "$work"/*.timer "$work"/*.path)
# The nginx conf lands under the dashboard hostname from deploy.env, so glob it
# rather than naming it after the placeholder.
for conf_file in "$work"/*.conf; do
    [ -f "$conf_file" ] && rendered+=("$conf_file")
done
echo "rendered $(printf '%s\n' "${rendered[@]}" | wc -l | tr -d ' ') file(s)"

# ---------------------------------------------------------------- compare ---
same=0
differs=()
for file in "${rendered[@]}"; do
    base="$(basename "$file")"
    case "$base" in
        *.conf) target="${SITE_NGINX_CONF:-/etc/nginx/conf.d/$base}" ;;
        *) target="/etc/systemd/system/$base" ;;
    esac
    if ssh -o BatchMode=yes -T "$HOST" "cat '$target'" 2>/dev/null | diff -q "$file" - >/dev/null; then
        same=$((same + 1))
    else
        differs+=("$base")
        echo "differs: $base"
        ssh -o BatchMode=yes -T "$HOST" "cat '$target'" 2>/dev/null | diff -u "$file" - | sed -n '3,10p'
    fi
done
echo "same=$same differs=${#differs[@]}"

if [ "$apply" = 0 ]; then
    echo "dry run: nothing installed (pass --apply)"
    exit 0
fi

if [ "${#differs[@]}" -eq 0 ]; then
    echo "nothing to do: the host already runs exactly these files"
    exit 0
fi

# ------------------------------------------------------------------ apply ---
stamp="$(date -u +%Y-%m-%dT%H-%M-%S)"
staging="/tmp/mc-deploy-$stamp"
echo "staging -> ${HOST}:$staging"
ssh -o BatchMode=yes -T "$HOST" "mkdir -p '$staging'"
scp -q "${rendered[@]}" "$HOST:$staging/"

units=()
backup_list=""
for base in "${differs[@]}"; do
    case "$base" in
        *.conf) ;;
        *) units+=("$staging/$base"); backup_list+="/etc/systemd/system/$base " ;;
    esac
done

remote=""
# Keep the outgoing copies: install(1) overwrites without a trace otherwise.
remote+="install -d -m 755 ~/minecraft/units-backup-$stamp; "
[ -n "$backup_list" ] && remote+="cp -a $backup_list ~/minecraft/units-backup-$stamp/ 2>/dev/null; "
if [ "${#units[@]}" -gt 0 ]; then
    remote+="sudo install -m 644 -o root -g root ${units[*]} /etc/systemd/system/ && sudo systemctl daemon-reload; "
fi
if [ "$nginx" = 1 ]; then
    conf="$staging/$(basename "$SITE_NGINX_CONF")"
    remote+="sudo install -m 644 -o root -g root '$conf' '$SITE_NGINX_CONF' && sudo nginx -t && sudo systemctl reload nginx; "
fi
remote+="rm -rf '$staging'"

if ssh -o BatchMode=yes -T "$HOST" "set -e; $remote"; then
    echo "applied; previous files kept in ~/minecraft/units-backup-$stamp"
else
    echo "error: apply failed - see the output above" >&2
    exit 1
fi

echo "verify:"
"$0" --host "$HOST" | tail -2