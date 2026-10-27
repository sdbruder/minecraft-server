# Deploying

How the files in this repository get onto a real host, and what is deliberately
left out of that.

## What is deployed, and what is not

| deployed from here | never deployed from here |
|---|---|
| 11 systemd units (`systemd/`) | the world (`família/`), `paper.jar`, `plugins/*.jar` |
| the nginx conf (`nginx/`) | `plugins/*/config.yml` — plugins own their own config |
| `scripts/*.sh` (copied, never rendered) | `autoplug/*.yml`, the dashboard's SQLite history |
| the dashboard app — `dashboard/*.py` + `dashboard/static/` (rendered, then copied) | the runtime env files (`dashboard.env`, `storagebox.env`, `mc-alerts.env`) |

AutoPlug owns the server side: it fetches Java and Paper, applies the plugin and
server updates, and writes the backups (see README, "Backups"). For the systemd
layer and nginx, deployment means `deploy.sh`; the dashboard app has no script
(it is four files and a restart) — see "The dashboard app" below.

## Why there are templates

The repository is host-agnostic — it carries `steve`, `/home/steve`,
`host.example.com` instead of a real user and domain — so a public clone holds no
personal data. systemd cannot fill those in from an env file: it resolves
`User=`, `Group=`, `ExecStart=` and `EnvironmentFile=` while parsing the unit,
before any environment exists for the service. Hence `scripts/install-units.sh`,
which substitutes the site values into the templates.

Runtime *secrets* are a different matter and are still env files, because
processes read them, not systemd.

## Topology

Two directions, same renderer:

| where it runs | script | for |
|---|---|---|
| on the host, from a checkout there | `scripts/install-units.sh` | renders and installs in place |
| on this machine, against a remote host | `scripts/deploy.sh` | renders, pushes over ssh, installs, reloads |

`deploy.sh` runs `install-units.sh` internally, so there is one substitution
table to maintain.

## The site values

Copy `deploy.env.example` to `deploy.env` (git-ignored) and fill it in:

| key | placeholder | meaning |
|---|---|---|
| `SITE_USER` | `steve` | `User=`/`Group=` in every unit; owner of the server tree |
| `SITE_HOME` | `/home/steve` | its home directory |
| `SITE_MC_DIR` | `/home/steve/minecraft` | the server tree inside it |
| `SITE_SERVER_HOST` | `host.example.com` | the address players connect to |
| `SITE_DASH_HOST` | `mc.example.com` | the dashboard's hostname (`server_name`, cert) |
| `SITE_DOMAIN` | `example.com` | bare domain, for prose like `*.example.com` |
| `SITE_SSH_HOST` | `host.example.com` | ssh target for `deploy.sh` |
| `SITE_NGINX_CONF` | `/etc/nginx/sites-available/mc.example.com.conf` | where the rendered conf belongs |

Fill each with your own value; the renderer replaces the placeholder throughout
the templates. The mapping is 1:1 — the README, "About this repository",
tabulates the same pairs.

## The dashboard app

`dashboard/*.py` are templates too, and `deploy.sh` does **not** carry them: the
host copy of `render.py` holds the real site values (the page subtitle's
hostname, one RFC 5737 example address in a docstring) where the repository
holds the placeholders (`host.example.com`, `203.0.113.13`). Render with the
same table `install-units.sh` uses — never `scp` the raw file, which would put
the placeholder on the live page:

```sh
. ./deploy.env
sed -E -e "s#/home/steve#$SITE_HOME#g" -e "s#steve#$SITE_USER#g" \
       -e "s#host\.example\.com#$SITE_SERVER_HOST#g" \
       -e "s#mc\.example\.com#$SITE_DASH_HOST#g" \
       -e "s#example\.com#$SITE_DOMAIN#g" \
       -e "s#203\.0\.113\.13#<the host's real client address>#g" \
       dashboard/render.py > /tmp/render.py
diff -u <(ssh HOST 'cat ~/minecraft/dashboard/render.py') /tmp/render.py   # expect: only your change
ssh HOST 'stamp=$(date +%Y%m%d-%H%M%S); mkdir -p ~/minecraft/dashboard-backup-$stamp \
          && cp -a ~/minecraft/dashboard/render.py ~/minecraft/dashboard-backup-$stamp/'
scp /tmp/render.py HOST:~/minecraft/dashboard/render.py
ssh HOST 'python3 -c "import ast;ast.parse(open(\"/home/steve/minecraft/dashboard/render.py\").read())" \
          && sudo systemctl restart mc-dashboard'
```

- The `diff` before the copy is the point: it proves the render is lossless, so
  what lands is your change and nothing else.
- `static/` needs no substitution; copy it as-is (it is only CSS).
- `collect.py`, `console.py`, `server.py` and `store.py` carry no placeholders
  either, but they live in the same directory and change on the same cadence.
- Verify through the real URL, not just the unit: sign in at
  `https://$SITE_DASH_HOST/` and check the page carries what you deployed (the
  unit can be `active` while the app serves an old render).
- Rollback: copy `render.py` back out of the newest
  `~/minecraft/dashboard-backup-<stamp>/` and restart the unit.

## First-time setup

```sh
# 1. site values (git-ignored)
cp deploy.env.example deploy.env && $EDITOR deploy.env

# 2. the scripts that run on the host: copied, not rendered - they default to
#    $HOME and read their own env files
scp scripts/*.sh HOST:~/minecraft/scripts/

# 3. runtime env files on the host, mode 600, never committed:
#    dashboard.env  DASH_USER DASH_PASS [DASH_BIND DASH_PORT MC_DIR REFRESH
#                   SAMPLE_SECONDS RETAIN_DAYS]
#    mc-alerts.env  PUSHOVER_TOKEN PUSHOVER_USER
#    storagebox.env SB_HOST SB_USER SB_KEY [SB_PATH SB_RETAIN_DAYS SB_PORT]

# 4. render, diff, install
scripts/deploy.sh            # dry run: what the host has vs what this renders
scripts/deploy.sh --apply    # units + daemon-reload
scripts/deploy.sh --apply --nginx   # also the conf, nginx -t, reload

# 5. enable - explicit, because which units a host wants is a decision
sudo systemctl enable --now autoplug.service mc-dashboard.service
sudo systemctl enable --now mc-alerts.service
sudo systemctl enable --now mc-backup-prune.timer mc-watchdog.timer
sudo systemctl enable --now mc-restart.path mc-restart-when-empty.path
```

## Verifying

```sh
scripts/deploy.sh                  # expect: same=N differs=0
ssh HOST 'systemctl is-active autoplug mc-dashboard mc-alerts \
          mc-watchdog.timer mc-backup-prune.timer mc-restart.path'
ssh HOST 'ss -ltn | grep -E ":25565|:8080"'
```

A dry run that reports `same=12 differs=0` for this repository's own host is the
proof that the substitution is lossless: those files were produced by rendering
the scrubbed templates with the real values, and come out byte-identical to what
is running.

## Day two

- Edit a template in the repo, then `scripts/deploy.sh` to see the diff,
  `--apply` to install. The renderer leaves files that are already correct alone
  and only runs `systemctl daemon-reload` when something changed.
- Never edit `/etc/systemd/system/*` by hand on the host: the next deploy
  overwrites it. That is what `~/minecraft/units-backup-<stamp>/` is for.
- Rollback: copy the wanted files back out of the newest `units-backup-<stamp>/`
  and `sudo systemctl daemon-reload`.

## Limitations worth knowing

- `deploy.sh` uses plain `ssh` with `BatchMode=yes`, so key auth must already
  work for `SITE_SSH_HOST`; it never prompts.
- It deploys units and the nginx conf only. A new *script* becomes live when it
  is copied to the host (step 2 above), and a new *dependency* is the plugin's or
  AutoPlug's business, not this repository's.
- The placeholders are exact-string substitutions, and the exclude list in
  `backup.yml` takes exact paths — so a host that renames things (a different
  dashboard file name) needs the template edited, not the deploy script.