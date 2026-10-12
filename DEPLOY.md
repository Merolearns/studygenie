# StudyGenie deployment guide

StudyGenie is a Flask + SQLite multi-user app. SQLite with WAL mode handles
the expected initial user base (dozens of concurrent users) on a single
server. No PostgreSQL/Redis/cloud services are required for the first release.

## Operational constraints (SQLite)

- Single server only. Do not run multiple app servers against one SQLite file.
- Writes are serialized; reads are concurrent. Fine for this workload.
- gunicorn workers: 2–4 threads each is fine; keep `--workers` small.
- Backups: copy the database file (SQLite backup API or plain file copy
  while the app is running is safe for WAL mode).

## Required environment variables

| Variable         | Purpose                                              |
|------------------|------------------------------------------------------|
| `SECRET_KEY`     | Signs sessions and CSRF tokens. **Required** — generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Never commit it. |
| `STUDYGENIE_DB`  | Path to the SQLite file (default `studygenie.db`; Docker uses `/data/studygenie.db`). |
| `GEMINI_API_KEY` | Optional. Enables AI features (quiz, notes, flashcards, guide, coach). The app works fully offline without it. |

## Deploy with Docker

```bash
docker build -t studygenie .
docker run -d --name studygenie -p 8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -v studygenie-data:/data \
  studygenie
```

Put a reverse proxy (Caddy, nginx) in front for HTTPS. Never expose the
Flask dev server directly.

## Backup and recovery

The entire product state is the one SQLite file:

```bash
# backup (safe while running)
docker exec studygenie sqlite3 /data/studygenie.db ".backup '/tmp/backup.db'"
docker cp studygenie:/tmp/backup.db ./studygenie-backup-$(date +%F).db

# restore: stop the container, replace /data/studygenie.db, start again
```

Recommended: nightly backup + keep 7 days.

## First-run notes

- The database schema (including the `users` table) is created automatically
  on first start; older databases are migrated in place.
- If the database already has courses from the pre-account era, the first
  registered account adopts them.
- Users can export (JSON download) and delete their data from Account.
