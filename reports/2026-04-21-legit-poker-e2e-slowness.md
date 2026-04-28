# Legit-Poker E2E Testing Slowness — Analysis

- **Date:** 2026-04-21
- **Skill:** `/autoimprove`
- **Query:** legit-poker e2e testing slowness
- **Cohort:** 32 e2e-flavoured legit-poker sessions (2026-03-27 → 2026-04-21)
- **Filter:** `project` or `cwd` in legit-poker family AND struggles/tags/message mention e2e, playwright, chrome-devtools, puppeteer, integration test, browser test, headless, pretest server, flaky test, or visual regression.

## Observed Patterns

- **Backend cold-compile races the health-check (~7 sessions).** `e2e-infra-backend` invokes `cargo run --release` directly, so on a cold workspace cargo is still type-checking when the 240 s health waiter expires. Codex `019db189` explicitly diagnoses this: "first session attempt timed out while the backend was still compiling, requiring a warm build and retry."
- **Frontend prod build is wiped + rebuilt every spin-up (~5 sessions).** `e2e-infra-frontend prod` runs `rm -rf .next && pnpm run build` unconditionally — 30–90 s tax per stack restart, plus repeated "unstyled page" / "stale Next dev server" investigations caused by mixing it with the regular dev stack.
- **Multi-actor browser flows pick the wrong tool (~5 sessions).** Agents start with `mcp__chrome-devtools` or the Claude-in-Chrome extension; the extension "never connected", and the MCP browser profile collides across host/guest subagents (codex `019dac0b`: "old MCP browser profile that's still running … both threads were forced onto the same shared browser profile"). Agents only fall back to the in-repo Playwright multi-context harness after wasted turns.
- **E2E discovery is ad-hoc (~5 sessions).** First user turns like "How do I run the e2e infra" and "Can you spin up an e2e prod stack" do not route to `/e2e-session`; agents shell-explore the Justfile and READMEs instead.
- **Auth/env preflight failures repeat (~8 sessions).** Privy JWKS, dev-vs-prod route gating, `ALLOWED_ORIGINS` not matching the chosen frontend port, missing `NEXT_PUBLIC_*` / `E2E_*` vars, and pnpm-vs-npm mismatches each cause a long debugging chain before the first browser action.
- **Port and process hygiene gaps (~8 sessions).** Recipes fail-fast when ports are busy but offer no diagnostic; agents reinvent `lsof | xargs kill` patterns each time.
- **Three-actor scenarios stall on auto-populated bot seats.** The host's room creation auto-fills five bot seats, blocking the second guest in the canonical `three-agent-room-ux` scenario (codex `019db189` only succeeded after the agent hardened the helper).
- **Multi-stack collisions.** Agents accumulate two or three half-running stacks across worktrees and proof systems, with no single "what is currently running" command.

## Skill Improvements

Target: `/Users/johnwu/code/zk/legit-poker/.claude/skills/e2e-session.md` (+ README + CLAUDE.md routing).

1. **Cold-start vs warm-stack section.** Require `just build-bin legit_poker_server prod true && just wasm-build` before `e2e-infra-backend`; explicitly warn that letting `cargo run` compile inline races the 240 s health waiter.
2. **Forbid Chrome DevTools MCP for multi-actor.** Any flow with ≥ 2 browser actors MUST use the Playwright multi-context dev-wallet harness (`createDevWalletContext` per actor, as in `tests/dev-wallet-ui.spec.ts`). Document that the Claude-in-Chrome extension is single-tab and the MCP browser shares a profile across subagents.
3. **Document the auto-bot-seat trap** in the skill and in `scenarios/three-agent-room-ux.json`. Require human-only seats before guests attempt to join.
4. **"Verify before relaunching" rule.** Call `just e2e-stack-status` and `just e2e-session-list` before any `e2e-infra-*` invocation.
5. **Route natural-language queries to the skill.** The README and CLAUDE.md should map "how do I run e2e" / "spin up e2e" / "three agents play poker" → `/e2e-session` so high-friction first turns land on the prescribed path.

## CLI Opportunities

Target: `/Users/johnwu/code/zk/legit-poker/justfiles/e2e.just`.

1. **`e2e-infra-backend`: `cargo build` + binary launch.** Replace `cargo run --release ...` with `cargo build --release ...` followed by `exec ./target/release/legit_poker_server ...`. The health waiter then races process startup (~1–2 s) instead of a workspace re-check (~30–90 s), and the cold-compile failure mode disappears.
2. **`e2e-infra-frontend`: cache-aware `.next` reuse.** Hash the frontend source tree plus env-derived inputs (`E2E_BACKEND_PORT`, `NEXT_PUBLIC_*`) into a marker under `.next/`; only rebuild when the hash changes. Eliminates the biggest "just to spin up a stack" tax.
3. **`just e2e-doctor` / `just e2e-stack-status`.** One truth table covering: Anvil reachable, Supabase port bound, `E2E_BACKEND_PORT` / `E2E_FRONTEND_PORT` free or owned by an E2E PID, `ALLOWED_ORIGINS` matches `FRONTEND_PORT`, Privy app id set, Spartan WASM artifact present and dimension-matched, dev-wallet RPC reachable, wallet-mode env consistent across backend/frontend. Most repeat preflight failures map to one row.
4. **`just e2e-clean-all`.** Find and kill only processes bound to `E2E_*` ports (backend, frontend, anvil, supabase, chrome MCP), optionally clean `.e2e/profiles/sessions/`. Replaces the current ad-hoc `lsof | xargs kill` patterns.
5. **`--human-seats` flag on the host room-creation helper** used by `e2e-session-run`, so multi-agent scenarios cannot collide with the auto-bot-seat default.

## External Tool Recommendations

1. **Playwright `webServer` config.** Migrate stack lifecycle into Playwright's built-in `webServer` array in `e2e/playwright.config.ts`: one entry per service (anvil, backend, frontend), each with `command`, `url`, `timeout`, and `reuseExistingServer: !process.env.CI`. Eliminates the bespoke bash health loop in `e2e-session-stack`, removes the cargo-run race, and gives "reuse warm stack" for free. Playwright is already a dependency.
   - https://playwright.dev/docs/test-webserver
2. **`mprocs` or `overmind` for supervised E2E processes.** A single `Procfile.e2e` describing anvil/supabase/backend/frontend plus a TUI status pane surfaces multi-stack collisions and dead processes immediately, rather than via a chain of `lsof` and `kill` invocations. Both tools are widely used in Rails/Elixir/Rust dev stacks.
   - https://github.com/pvolok/mprocs
   - https://github.com/DarthSim/overmind
3. **`wait-on`** for service readiness checks inside existing recipes if a full Playwright `webServer` migration is too large for a single PR. Consistent HTTP/TCP/file readiness semantics with timeouts and exponential backoff.
   - https://github.com/jeffbski/wait-on

## Evidence — Representative Sessions

| Pattern | Session (prefix) | Friction | Duration | Note |
|---|---|---|---|---|
| Backend cold-compile race | codex `019db189…4213d` | 3.0 | 18m | "first attempt timed out while backend still compiling; warm build + retry" |
| Backend cold-compile race | codex `019d…2e84b40b6a7c` | — | — | "slow browser/stack startup" |
| Stale `.next` / unstyled page | codex `019d…4897c0d44c9b` | — | — | "stale generated types … setup took too long" |
| Stale `.next` / unstyled page | codex `019d…ab4407bb78b5` | 2.0 | 347m | "stale mixed `.next` build caused unstyled pages" |
| Chrome MCP profile collision | claude `87dc628b…6f372e0f6ede` | 8.0 | 4448m | "Claude Chrome extension never connected; fall back to chrome-devtools/playwright" |
| Chrome MCP profile collision | codex `019d…dc494efed38b` | 1.0 | 0m | "can it stop moving my focus to the browser" |
| Chrome MCP profile collision | codex `019dac0b…c69623bc804f` | 2.0 | 58m | "old MCP browser profile still running … forced onto shared profile" |
| Auth / env preflight | claude `7a784edc…74f27cafd497` | 9.0 | 952m | "Privy JWKS, release-vs-debug gating, pnpm/npm mismatch, CORS" |
| Ports / kill loops | claude `…b69ba1b98841` | — | — | repeated `lsof` / kill patterns |
| Three-actor bot-seat blocker | codex `019db189…4213d` | 3.0 | 18m | "auto-populating five bot seats; first guest never gets a free seat" |
| Discovery / wrong entry point | claude `db9cf8ca…923c9ad23383` | 8.0 | 902m | first message: "How do I run the e2e infra. I need to run it" |

## Proposed Improvement Records (for `improvements` table)

| type | target_name | description |
|---|---|---|
| skill | `e2e-session` | Cold-start section + multi-actor tool choice + bot-seat trap + "verify before relaunching" + routing |
| cli | `e2e-infra-backend (binary launch)` | Replace `cargo run` with `cargo build` + binary launch |
| cli | `e2e-infra-frontend (incremental .next reuse)` | Stop unconditional `rm -rf .next && pnpm run build` |
| cli | `e2e-doctor` | Truth-table preflight CLI |
| cli | `e2e-clean-all` | E2E-only port-scoped process cleanup |
| tool | Playwright webServer config | Runner-owned stack lifecycle |
| tool | mprocs / overmind | Supervised E2E processes |

## Persistence Note

Persisting these recommendations via `persist_analysis_artifacts()` failed because the live `analysis_runs` table is older than the schema `db.py::insert_analysis_run` expects. The live table has columns `run_id, ran_at, analyzed_from, analyzed_to, conversation_count, findings, skills_affected`; the insert statement also writes `query_text`, `filters_json`, `report_markdown`, `research_performed`, and `model_used`. A migration is needed before this run can be stored in the database.
