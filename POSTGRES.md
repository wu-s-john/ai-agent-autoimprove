# Postgres Plan

This repo uses shared PostgreSQL for the canonical conversation index and summary store.

The intended architecture is:

- Syncthing handles Claude and Codex artifact handoff across machines
- PostgreSQL stores harvested metadata, summaries, analysis runs, and improvements
- `just` loads both DB credentials and OpenAI credentials through 1Password

## Secret Management

Vaults and items expected by this repo:

- `ai-agent-army/autoimprove-postgres-app`
- `ai-agent-army/autoimprove-postgres-admin`
- `Personal/OpenAI`

The committed env map at [config/op.envmap](/Users/johnwu/code/ai-agent-autoimprove/config/op.envmap:1) assumes those names.

### `autoimprove-postgres-app`

Required fields:

- `host`
- `port`
- `database`
- `username`
- `password`
- `sslmode`

### `autoimprove-postgres-admin`

Required fields:

- `host`
- `port`
- `database`
- `username`
- `password`
- `sslmode`

### `Personal/OpenAI`

Required field:

- `API_KEY`

`OPENAI_MODEL` is not stored in 1Password here. Set it in your shell or pass `--model`.

## Commands

From the repo root:

```bash
cd /Users/johnwu/code/ai-agent-autoimprove
just database-url
just database-url-admin
just psql
just psql-admin
just schema-init
just reset-db
just harvest
just summarize --model gpt-5.4-mini
just refresh --model gpt-5.4-mini
```

What they do:

- `just database-url`: print the app `DATABASE_URL`
- `just database-url-admin`: print the admin `DATABASE_URL`
- `just psql`: open `psql` with app credentials
- `just psql-admin`: open `psql` with admin credentials
- `just schema-init`: create or update the schema
- `just reset-db`: drop and recreate the schema from scratch
- `just harvest`: ingest Claude and Codex artifacts
- `just summarize`: write structured per-session summaries
- `just refresh`: run `harvest` then `summarize`

## Notes

- `db.py` resolves `DATABASE_URL` from `DATABASE_URL` itself or from `PG*` / `POSTGRES_ADMIN_*` env vars.
- The `justfile` uses `op run --env-file config/op.envmap -- ...` so the scripts can read those env vars directly.
- `summarize` and `refresh` require `OPENAI_API_KEY` from 1Password plus either `OPENAI_MODEL` or `--model`.
- For RDS, keep `sslmode=require` and make sure your security group allows port `5432` from your client IP.
