set shell := ["bash", "-ceu"]

default:
    @just --list

require-1password:
    @command -v op >/dev/null || { \
      echo "1Password CLI ('op') is required. Install it and enable CLI access first."; \
      exit 1; \
    }
    @op account list >/dev/null 2>&1 || { \
      echo "1Password CLI is not signed in. Open 1Password and enable CLI integration, then try again."; \
      exit 1; \
    }

require-psql:
    @command -v psql >/dev/null || { \
      echo "psql is required. Install PostgreSQL client tools first."; \
      exit 1; \
    }

database-url: require-1password
    @op run --env-file config/op.envmap -- uv run python -c "from db import resolve_database_url; print(resolve_database_url())"

database-url-admin: require-1password
    @op run --env-file config/op.envmap -- uv run python -c "from db import resolve_database_url; print(resolve_database_url(env_prefix='POSTGRES_ADMIN'))"

psql: require-psql require-1password
    @psql "$$(just --quiet database-url)"

psql-admin: require-psql require-1password
    @psql "$$(just --quiet database-url-admin)"

schema-init *args: require-1password
    @op run --env-file config/op.envmap -- uv run python init_schema.py {{args}}

reset-db *args: require-1password
    @op run --env-file config/op.envmap -- uv run python reset_db.py {{args}}

harvest *args: require-1password
    @op run --env-file config/op.envmap -- uv run harvest {{args}}

summarize *args: require-1password
    @op run --env-file config/op.envmap -- uv run summarize {{args}}

refresh *args: require-1password
    @op run --env-file config/op.envmap -- uv run refresh {{args}}
