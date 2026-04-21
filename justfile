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
    @op run --env-file config/op.envmap -- python3 -c "from os import environ; from urllib.parse import quote; user = quote(environ['PGUSER'], safe=''); password = quote(environ['PGPASSWORD'], safe=''); database = quote(environ['PGDATABASE'], safe=''); url = f\"postgresql://{user}:{password}@{environ['PGHOST']}:{environ['PGPORT']}/{database}\"; sslmode = environ.get('PGSSLMODE', ''); print(url + (f\"?sslmode={quote(sslmode, safe='')}\" if sslmode else ''))"

database-url-admin: require-1password
    @op run --env-file config/op.envmap -- python3 -c "from os import environ; from urllib.parse import quote; user = quote(environ['POSTGRES_ADMIN_USER'], safe=''); password = quote(environ['POSTGRES_ADMIN_PASSWORD'], safe=''); database = quote(environ['POSTGRES_ADMIN_DATABASE'], safe=''); url = f\"postgresql://{user}:{password}@{environ['POSTGRES_ADMIN_HOST']}:{environ['POSTGRES_ADMIN_PORT']}/{database}\"; sslmode = environ.get('POSTGRES_ADMIN_SSLMODE', ''); print(url + (f\"?sslmode={quote(sslmode, safe='')}\" if sslmode else ''))"

psql: require-psql
    @psql "$$(just --quiet database-url)"

psql-admin: require-psql
    @psql "$$(just --quiet database-url-admin)"

schema-init:
    @DATABASE_URL="$$(just --quiet database-url)" \
      uv run python init_schema.py --database-url "$$DATABASE_URL"

schema-init-admin:
    @DATABASE_URL="$$(just --quiet database-url-admin)" \
      uv run python init_schema.py --database-url "$$DATABASE_URL"

harvest:
    @DATABASE_URL="$$(just --quiet database-url)" \
      uv run python harvest.py --database-url "$$DATABASE_URL"
