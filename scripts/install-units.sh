#!/usr/bin/env bash
#
# install-units.sh - render the systemd/ and nginx/ templates with this host's
# values and install them.
#
# The repository is host-agnostic: units and the nginx conf carry placeholders
# (`steve`, `/home/steve`, `host.example.com`, `mc.example.com`) so a public
# clone holds no personal data. systemd cannot read those from an env file -
# User=, Group=, ExecStart= and EnvironmentFile= are resolved before any
# environment exists - so they are substituted here instead.
#
# Values come from deploy.env (git-ignored, see deploy.env.example). Scripts
# need no substitution: they default to $HOME and take overrides from their own
# env files.
#
# Usage:
#   scripts/install-units.sh                  # render into /etc/systemd/system
#   scripts/install-units.sh --out /tmp/units # stage into a directory instead
#   scripts/install-units.sh --diff           # show what would change
#
# Environment:
#   DEPLOY_ENV   site values   (default: <repo>/deploy.env)
#
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_ENV="${DEPLOY_ENV:-$HERE/deploy.env}"
OUT="/etc/systemd/system"
mode="install"

case "${1:-}" in
    -h|--help) awk 'NR>1 && /^#/ {print} NR>1 && !/^#/ {exit}' "$0"; exit 0 ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
        --diff) mode="diff"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ ! -r "$DEPLOY_ENV" ]; then
    echo "error: $DEPLOY_ENV not found - copy deploy.env.example and fill it in" >&2
    exit 2
fi
# shellcheck disable=SC1090
. "$DEPLOY_ENV"

for var in SITE_USER SITE_HOME SITE_MC_DIR SITE_SERVER_HOST SITE_DASH_HOST; do
    eval "value=\"\${$var:-}\""
    if [ -z "$value" ]; then
        echo "error: $var is not set in $DEPLOY_ENV" >&2
        exit 2
    fi
done

# Optional: the bare domain, for prose that is not a hostname (certificate
# wildcards, mail addresses). Without it those stay as `example.com`.
SITE_DOMAIN="${SITE_DOMAIN:-}"

# One substitution table, applied to every template. Order matters only for the
# hostnames: the bare `example.com` is never rewritten, so no rule can cascade.
# `steve` is replaced everywhere in a unit file - as User=/Group=, in
# Environment=USER=, and in the prose comments that name the runtime user.
render() {
    sed -E \
        -e "s#/home/steve#${SITE_HOME}#g" \
        -e "s#/home/steve#${SITE_HOME}#g" \
        -e "s#steve#${SITE_USER}#g" \
        -e "s#steve#${SITE_USER}#g" \
        -e "s#host\\.example\\.com#${SITE_SERVER_HOST}#g" \
        -e "s#mc\\.example\\.com#${SITE_DASH_HOST}#g" \
        ${SITE_DOMAIN:+-e "s#example\\.com#${SITE_DOMAIN}#g"} \
        "$1"
}

templates=()
for pattern in "$HERE"/systemd/*.service "$HERE"/systemd/*.timer "$HERE"/systemd/*.path; do
    [ -f "$pattern" ] && templates+=("$pattern")
done
[ -f "$HERE/nginx/mc.example.com.conf" ] && templates+=("$HERE/nginx/mc.example.com.conf")

[ "${#templates[@]}" -gt 0 ] || { echo "error: no templates found" >&2; exit 1; }

if [ "$mode" = install ]; then
    mkdir -p "$OUT" || { echo "error: cannot create $OUT" >&2; exit 1; }
fi

changed=0
for template in "${templates[@]}"; do
    base="$(basename "$template")"
    # The nginx template is named after the placeholder host, so it lands under
    # the real dashboard hostname and nginx picks it up.
    case "$template" in
        */nginx/*) base="${SITE_DASH_HOST}.conf" ;;
    esac
    target="$OUT/$base"

    if [ "$mode" = diff ]; then
        if [ -r "$target" ]; then
            if diff -u "$target" <(render "$template") >/dev/null; then
                echo "same     $base"
            else
                echo "DIFFERS  $base"
                diff -u "$target" <(render "$template") | sed -n '3,12p'
                changed=$((changed + 1))
            fi
        else
            echo "absent   $base"
            changed=$((changed + 1))
        fi
        continue
    fi

    render "$template" >"${target}.tmp" || { echo "error: cannot render $base" >&2; exit 1; }
    chmod 644 "${target}.tmp"
    if [ -r "$target" ] && diff -q "$target" "${target}.tmp" >/dev/null; then
        echo "same     $base"
        rm -f "${target}.tmp"
    else
        echo "installed $base"
        mv "${target}.tmp" "$target"
        changed=$((changed + 1))
    fi
done

if [ "$mode" = install ] && command -v systemctl >/dev/null && [ "$OUT" = "/etc/systemd/system" ]; then
    if [ "$changed" -gt 0 ]; then
        systemctl daemon-reload && echo "daemon-reload done"
    else
        echo "nothing changed, no daemon-reload"
    fi
fi

echo "$changed file(s) ${mode/diff/needing attention}"