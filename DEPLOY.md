# StudyGenie deployment guide

StudyGenie is a Flask + SQLite multi-user app. SQLite with WAL mode handles
the expected initial user base on a single server. No PostgreSQL, Redis, or
cloud services are required for the first release.

**Recommended hosting: Hetzner Cloud CX22** — 2 vCPU, 4 GB RAM, 40 GB NVMe,
~€4.60/month (verify current price at hetzner.com/cloud; US regions
available). Single server, Docker + Caddy, SQLite on a Docker volume.
Why: cheapest reliable single-server option with persistent local disk —
exactly what SQLite needs. Upgrade path: resize to CX32/CX42 in place when
usage grows; move to managed Postgres only if you outgrow one server.

## Operational constraints (SQLite)

- Single app server only. Do not run multiple app containers against one
  SQLite file.
- WAL mode is enabled (see `db.connect`): readers never block on writes.
- gunicorn: 2 workers is the default; fine for this workload.

## Quick deploy (Docker Compose + Caddy for HTTPS)

Prerequisites (done by you once):
1. A Hetzner (or equivalent) Ubuntu 24.04 server with Docker installed.
2. A domain (e.g. `studygenie.example.com`) with an A record pointing at
   the server's IP.
3. A `SECRET_KEY`: `python3 -c "import secrets; print(secrets.token_hex(32))"`.

On the server:

```bash
git clone https://github.com/Merolearns/studygenie && cd studygenie
export DOMAIN=your-domain.com
export SECRET_KEY='<paste the generated key>'
# optional: export GEMINI_API_KEY='<key>' to enable AI features
docker compose up -d --build
```

Caddy obtains a Let's Encrypt certificate automatically on first request.
The app is served at `https://$DOMAIN`. Check health:

```bash
curl -s https://$DOMAIN/healthz   # {"status":"ok"}
```

Migrations run automatically on first request (`before_request` hook);
older databases are upgraded in place.

## Required environment variables

| Variable         | Purpose |
|------------------|---------|
| `SECRET_KEY`     | Signs sessions/CSRF. **Required when `PRODUCTION=1`** — the app refuses to start without it. |
| `PRODUCTION`     | Set to `1` in real deployments: enforces SECRET_KEY, secure cookies, ProxyFix, debug off. |
| `STUDYGENIE_DB`  | SQLite path (compose sets `/data/studygenie.db` on a persistent volume). |
| `GEMINI_API_KEY` | Optional. AI features degrade gracefully without it. |
| `DOMAIN`         | Used by the Caddyfile for HTTPS. |

Never commit secrets. `.db` files and secrets are excluded from the image
via `.dockerignore`.

## Backup and recovery

```bash
# backup (WAL-safe; run while the app is live)
docker compose exec app /app/scripts/backup.sh /data/backups

# copy off the server
docker cp $(docker compose ps -q app):/data/backups/<file> ./
```

Restore drill (tested 2026-10-12 on an isolated copy — passed):

```bash
./scripts/restore.sh <backup-file> /tmp/restored-test.db
# verify, then for a REAL restore:
docker compose stop app
docker cp /tmp/restored-test.db $(docker compose ps -q app):/data/studygenie.db
docker compose start app
curl -s https://$DOMAIN/healthz
```

Recommended: nightly cron backup + keep 7 days, copied off-server.

## Rollback

Each deploy is `docker compose up -d --build`. To roll back:

```bash
git log --oneline -5            # find the previous good commit
git checkout <sha> -- .         # or git revert
docker compose up -d --build
```

Database schema is forward-migrated automatically; rolling back app code
does not roll back schema changes (all Phase 5/6 migrations are additive).

## Release checklist

- [x] App boots with `PRODUCTION=1` + `SECRET_KEY`; refuses without key
- [x] Debug off, secure/HttpOnly/SameSite cookies, ProxyFix for HTTPS
- [x] CSRF on all forms; 404/500 pages leak no internals
- [x] `/healthz` returns 200 with a working database
- [x] Per-user isolation tested (183 automated tests)
- [x] Backup + restore drill executed on isolated copy — passed
- [x] No secrets in repo or image; `.db` excluded
- [ ] Server provisioned + domain pointed (needs you)
- [ ] `SECRET_KEY` generated and set (needs you)
- [ ] First `docker compose up`, HTTPS verified via `curl https://$DOMAIN/healthz`
- [ ] Nightly backup cron installed
