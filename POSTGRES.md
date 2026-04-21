# Postgres Plan

This repo now supports two database modes:

- shared PostgreSQL via `DATABASE_URL` or the `just` recipes

The intended architecture is:

- Syncthing handles Claude session handoff across machines
- Postgres becomes the shared analysis database
- each machine harvests locally and writes into the shared Postgres instance

## Secret Management

Use 1Password for all Postgres secrets.

Recommended vault:

- `ai-agent-army`

Recommended items:

- `autoimprove-postgres-app`
- `autoimprove-postgres-admin`

The committed config map [config/op.envmap](/Users/johnwu/code/ai-agent-autoimprove/config/op.envmap:1) assumes those names.

## Vault Fields

### `autoimprove-postgres-app`

Create these fields:

- `host`
- `port`
- `database`
- `username`
- `password`
- `sslmode`

This is the least-privileged app user that harvesting and analysis code should use.

### `autoimprove-postgres-admin`

Create the same fields:

- `host`
- `port`
- `database`
- `username`
- `password`
- `sslmode`

Use this only for bootstrap, migrations, and manual administration.

## What Not To Put In 1Password

Do not store ordinary infrastructure config in the vault:

- AWS region
- EC2 instance type
- subnet IDs
- security group IDs
- EBS size
- app feature flags

Keep those in repo config, docs, or infra code. Only secrets and sensitive connection details should be in 1Password.

## Config Map

This repo now uses a committed 1Password env map at [config/op.envmap](/Users/johnwu/code/ai-agent-autoimprove/config/op.envmap:1).

It contains safe-to-commit references like:

```text
PGHOST=op://ai-agent-army/autoimprove-postgres-app/host
PGPORT=op://ai-agent-army/autoimprove-postgres-app/port
PGDATABASE=op://ai-agent-army/autoimprove-postgres-app/database
PGUSER=op://ai-agent-army/autoimprove-postgres-app/username
PGPASSWORD=op://ai-agent-army/autoimprove-postgres-app/password
PGSSLMODE=op://ai-agent-army/autoimprove-postgres-app/sslmode
```

The `justfile` loads these refs through 1Password with `op run --env-file config/op.envmap -- ...`.

## Commands

From the repo root:

```bash
cd /Users/johnwu/code/ai-agent-autoimprove
just database-url
just database-url-admin
just psql
just psql-admin
just schema-init
just schema-init-admin
just harvest
```

What they do:

- `just database-url`: print the app `DATABASE_URL`
- `just database-url-admin`: print the admin `DATABASE_URL`
- `just psql`: open `psql` using the app credentials
- `just psql-admin`: open `psql` using the admin credentials
- `just schema-init`: create or update the schema using the app credentials
- `just schema-init-admin`: same bootstrap using the admin credentials
- `just harvest`: run `harvest.py` against the shared Postgres database

## Database Support In This Repo

The database layer in [db.py](/Users/johnwu/code/ai-agent-autoimprove/db.py:127) now:

- requires PostgreSQL via `DATABASE_URL` or the `just` recipes
- rejects non-Postgres URLs
- uses PostgreSQL upserts for conversation ingestion

[init_schema.py](/Users/johnwu/code/ai-agent-autoimprove/init_schema.py:1) is a tiny wrapper around `db.init_db()`, and `just schema-init` is the preferred entrypoint for schema bootstrapping.

[harvest.py](/Users/johnwu/code/ai-agent-autoimprove/harvest.py:560) supports:

- `--database-url`
- `--source-machine`

`source_machine` is stored with each harvested conversation so multiple machines can write to one shared database without losing provenance.

## RDS Notes

For an RDS-based setup:

- create one admin user and one app user
- store both in 1Password under the item names above
- keep `sslmode=require`
- make sure the RDS security group allows your client IP on port `5432`
- if `psql` times out, check networking first: public accessibility, subnet routing, and security group ingress

For this repo's current workflow, RDS plus `config/op.envmap` plus `just` is the supported secret-management path.
