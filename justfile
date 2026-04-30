set shell := ["bash", "-ceu"]
set dotenv-load := true

runtime_env := "config/op.envmap"
admin_env := "config/op-admin.envmap"
dev_vault_id := "ihqmf2zd73upmihfnh4o4t2tam"
dev_token_ref := "op://ai-agent-army/OnePasswordDevServiceAccount/SERVICE_ACCOUNT_PASSWORD"

default:
    @just --list

_require-op:
    @command -v op >/dev/null || { \
      echo "1Password CLI ('op') is required. Install it and enable CLI access first."; \
      exit 1; \
    }

require-1password: _require-op
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then \
      OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN" op item list --vault "{{ dev_vault_id }}" --format json >/dev/null; \
    elif [ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then \
      op item list --vault "{{ dev_vault_id }}" --format json >/dev/null; \
    else \
      op account list >/dev/null 2>&1 || { \
        echo "1Password CLI is not signed in. Run: just load-dev-token"; \
        exit 1; \
      }; \
    fi

require-admin-1password: _require-op
    @op item get autoimprove-postgres-admin --vault ai-agent-army >/dev/null 2>&1 || { \
      echo "Admin 1Password access is unavailable. Sign in interactively with access to the ai-agent-army vault."; \
      exit 1; \
    }

require-psql:
    @command -v psql >/dev/null || { \
      echo "psql is required. Install PostgreSQL client tools first."; \
      exit 1; \
    }

database-url: require-1password
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then export OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN"; fi; op run --env-file "{{ runtime_env }}" -- uv run python -c "from db import resolve_database_url; print(resolve_database_url())"

database-url-admin: require-admin-1password
    @op run --env-file "{{ runtime_env }}" --env-file "{{ admin_env }}" -- uv run python -c "from db import resolve_database_url; print(resolve_database_url(env_prefix='POSTGRES_ADMIN'))"

psql: require-psql require-1password
    @psql "$$(just --quiet database-url)"

psql-admin: require-psql require-admin-1password
    @psql "$$(just --quiet database-url-admin)"

schema-init *args: require-1password
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then export OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN"; fi; op run --env-file "{{ runtime_env }}" -- uv run python init_schema.py {{args}}

reset-db *args: require-admin-1password
    @op run --env-file "{{ runtime_env }}" --env-file "{{ admin_env }}" -- uv run python reset_db.py {{args}}

harvest *args: require-1password
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then export OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN"; fi; op run --env-file "{{ runtime_env }}" -- uv run harvest {{args}}

summarize *args: require-1password
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then export OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN"; fi; op run --env-file "{{ runtime_env }}" -- uv run summarize {{args}}

refresh *args: require-1password
    @if [ -n "${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-}" ]; then export OP_SERVICE_ACCOUNT_TOKEN="$AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN"; fi; op run --env-file "{{ runtime_env }}" -- uv run refresh {{args}}

# Seed .env with the ai-agent-army-dev service account token.
# Run once per machine; requires interactive 1Password access to ai-agent-army.
load-dev-token: _require-op
    #!/usr/bin/env bash
    set -euo pipefail
    token="$(op read "{{ dev_token_ref }}")"
    if [[ -z "$token" ]]; then
      echo "1Password returned an empty service account token from {{ dev_token_ref }}" >&2
      exit 1
    fi
    tmp="$(mktemp)"
    if [[ -f .env ]]; then
      grep -v '^AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN=' .env > "$tmp" || true
    fi
    printf '\nAI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN=%s\n' "$token" >> "$tmp"
    mv "$tmp" .env
    chmod 600 .env
    echo "[ok] AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN written to .env"

# Materialize runtime secrets into .env using the ai-agent-army-dev service account.
# This is optional; normal recipes can also run through op directly once load-dev-token has run.
setup-env: _require-op
    #!/usr/bin/env bash
    set -euo pipefail
    token="${AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN:-${OP_SERVICE_ACCOUNT_TOKEN:-}}"
    if [[ -z "$token" ]]; then
      echo "AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN is missing. Run: just load-dev-token" >&2
      exit 1
    fi
    tmp="$(mktemp)"
    OP_SERVICE_ACCOUNT_TOKEN="$token" op inject -f -i "{{ runtime_env }}" -o "$tmp" >/dev/null
    printf '\nAI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN=%s\n' "$token" >> "$tmp"
    mv "$tmp" .env
    chmod 600 .env
    echo "[ok] .env written from {{ runtime_env }}"
