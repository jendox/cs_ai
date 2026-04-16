# AI Customer Support Assistant (Zendesk + LLM + Admin UI)

Production-style backend service for automated Zendesk ticket handling with:
- asynchronous event processing,
- AI-generated replies,
- runtime admin controls via Telegram and Web Admin,
- durable queues and retries via RabbitMQ.

This repository is designed as a practical engineering example: real integrations, operational concerns, and modular architecture.

## Why This Project

Customer support teams often need to:
- answer first-line tickets quickly,
- filter out service/spam noise,
- keep control over AI behavior in production.

This project addresses that with an event-driven pipeline:
1. Poll Zendesk updates.
2. Convert updates to jobs.
3. Process jobs with workers.
4. Generate and post replies.
5. Manage runtime behavior from Telegram or Web Admin without redeploy.

## Key Features

- Zendesk incremental polling with checkpointing and distributed lock.
- RabbitMQ job topology with retry queues and dead-letter queues.
- AI first-reply and follow-up reply workers.
- Rule-based + LLM-based service/spam filtering.
- Runtime LLM settings and prompt management in DB.
- Telegram admin panel for:
  - users/roles,
  - ticket observing controls,
  - LLM settings,
  - prompt export/import,
  - Zendesk comment mode (`internal` vs `public`).
- FastAPI Web Admin for:
  - Zendesk comment mode (`internal` vs `public`),
  - LLM response/classification runtime settings,
  - prompt viewing/export/editing,
  - production ticket history and bot reply attempts,
  - LLM playground for isolated prompt/model testing,
  - web-admin user management.
- Structured JSON logging with context and secret redaction.

## Architecture Overview

Main runtime services:
- `Poller` (per brand)
- `InitialReplyWorker` (per brand)
- `FollowUpReplyWorker` (per brand)
- `TicketClosedWorker` (per brand)
- `TelegramAdmin`
- `WebAdmin` (standalone FastAPI entrypoint)

Core dependencies:
- PostgreSQL for state/configuration.
- RabbitMQ for async jobs/retries.
- Zendesk API for tickets/comments.
- Amazon MCP server for tool calls used by LLM context.
- Google Gemini client (currently active provider).

## End-to-End Flow

1. `Poller` reads updated tickets from Zendesk.
2. New tickets produce `initial_reply` jobs.
3. New user comments produce `followup_reply` jobs.
4. Closed/solved status produces `ticket_closed` jobs.
5. Worker validates ticket state and generates AI response.
6. Worker checks runtime Zendesk mode:
   - `internal` -> internal note,
   - `public` -> public comment.
7. Reply is deduplicated (`our_posts`) and posted to Zendesk.

## Tech Stack

- Python `3.13`
- `anyio`, `aio-pika`, `aiogram`, `httpx`
- `SQLAlchemy` + `asyncpg` + `alembic`
- `pydantic` + `pydantic-settings`
- `google-genai` (Gemini)
- Docker / Docker Compose

## Repository Layout

- `src/app.py` - application orchestration and service startup.
- `src/zendesk/poller.py` - incremental polling and event generation.
- `src/workers/` - job consumers and business logic.
- `src/jobs/` - queue contracts and RabbitMQ topology.
- `src/db/` - SQLAlchemy models and repositories.
- `src/ai/` - LLM runtime settings, prompts, clients, tools.
- `src/telegram/` - admin bot handlers/middlewares/filters.
- `src/web_admin/` - FastAPI Web Admin routes, templates, sessions, auth.
- `deploy/` - Dockerfiles, compose files, env templates, deployment guide.

## Local Development

Prerequisites:
- Python `3.13`
- `uv`
- Docker (for PostgreSQL/RabbitMQ/MCP)

1. Install dependencies:

```bash
uv sync
```

2. Prepare `.env` in project root (based on your secrets).

3. Start infra (`postgres`, `rabbitmq`, `amazon-mcp`) via compose in `deploy/`.

Useful Make targets:

```bash
make up          # root docker-compose.yml: postgres + rabbitmq
make mcp-build   # build amazon-mcp:dev from ../amazon_mcp
make mcp-up      # start only amazon-mcp from deploy/docker-compose.dev.yml
make mcp-logs    # follow amazon-mcp logs
make mcp-down    # stop amazon-mcp
```

4. Apply migrations:

```bash
uv run alembic upgrade head
```

5. Run app:

```bash
uv run python run.py
```

6. Run Web Admin in a separate process:

```bash
uv run python run_web.py
```

By default the Web Admin listens on `WEB__HOST=0.0.0.0` and `WEB__PORT=8080`.

## Web Admin

The Web Admin is a separate FastAPI app started by `run_web.py`.

Required environment variables:

- `WEB__SESSION_SECRET` - random secret used for signed session and CSRF cookies.
- `WEB__BOOTSTRAP_USERNAME` - initial superadmin username.
- `WEB__BOOTSTRAP_PASSWORD` - initial superadmin password.
- `WEB__COOKIE_SECURE` - set to `true` behind HTTPS in production.

On startup Web Admin ensures the bootstrap user exists, is active, and has the
`superadmin` role. It does not overwrite the password of an existing bootstrap
user.

Available sections:

- `Zendesk` - switch generated comments between internal notes and public replies.
- `Tickets` - local production Zendesk tickets, activity counters, conversation timeline, refresh from Zendesk, and direct Zendesk link.
- `Replies` - generated/posted/failed reply attempts with filters, pagination, summary cards, and job/status breakdown.
- `Playground` - admin-only isolated LLM sandbox for local test conversations.
- `LLM Settings` - update response and classification runtime parameters.
- `Prompts` - view/export prompts; users with `admin` or `superadmin` can edit.
- `Users` - superadmin-only management for web-admin users.

Web-admin roles:

- `user` - read-only access to runtime pages.
- `admin` - can update Zendesk mode, LLM settings, prompts, and use Playground.
- `superadmin` - can also manage web-admin users.

Before first Web Admin start, apply migrations:

```bash
uv run alembic upgrade head
```

The `admin_users` table is required for login. The bootstrap superadmin is
created automatically by `run_web.py`.

The Web Admin initializes LLM runtime context for Playground. If the local MCP
server is not running, Web Admin still starts and logs a warning; generation
runs that require MCP tools may fail and are stored as failed playground runs.

### Tickets and Replies

Production ticket analysis lives in two related sections:

- `Tickets` (`/admin/tickets`) lists locally observed Zendesk tickets with
  filters by ticket id, status, brand, and observing state.
- `Ticket Detail` (`/admin/tickets/{ticket_id}`) shows ticket summary,
  Zendesk/customer/agent/bot timeline, bot attempts, refresh action, and a
  direct Zendesk link.
- `Replies` (`/admin/replies`) lists reply attempts across tickets with
  filters by ticket id prefix, status, job type, brand, and period.

Old `/admin/replies/tickets/{ticket_id}` URLs redirect to the new ticket detail
route.

### LLM Playground

Playground is isolated from production Zendesk data and requires `admin` or
`superadmin`.

Routes:

- `GET /admin/playground`
- `POST /admin/playground/tickets`
- `GET /admin/playground/tickets/{id}`
- `POST /admin/playground/tickets/{id}/messages`
- `POST /admin/playground/tickets/{id}/generate-initial`
- `POST /admin/playground/tickets/{id}/generate-followup`
- `POST /admin/playground/tickets/{id}/close`

Data is stored in separate tables:

- `llm_playground_tickets`
- `llm_playground_messages`
- `llm_playground_runs`

Playground uses the same response runtime settings and prompts as production
reply generation. To test different models, change `LLM Settings` first, then
run generation in Playground. Generated assistant messages and failed runs are
stored in the playground tables only.

## Docker (Dev/Prod)

Deployment assets are provided in `deploy/`:
- `docker-compose.dev.yml`
- `docker-compose.prod.yml`
- `.env.*.example`
- `README.md` with build/run commands

## Runtime Operations

Telegram admin commands include:
- `/llm_settings`
- `/llm_response_set ...`
- `/llm_classification_set ...`
- `/prompts`, `/prompt_info`, `/prompt_export`, `/prompt_import`
- `/zendesk_mode`, `/zendesk_mode_set internal|public`
- `/ticket`, `/observe`, `/not_observe`

Web Admin is available at:

- `GET /admin/login`
- `GET /admin/zendesk/mode`
- `GET /admin/tickets`
- `GET /admin/tickets/{ticket_id}`
- `GET /admin/replies`
- `GET /admin/playground` (`admin` and `superadmin`)
- `GET /admin/llm`
- `GET /admin/prompts`
- `GET /admin/users` (`superadmin` only)

## Current Scope and Limitations

- Active LLM provider in runtime: Google.
- Current production setup uses one supported brand.
- Playground model/provider override is controlled through global `LLM Settings`; there is no per-run override in the first MVP.
- `AgentDirectiveWorker` exists as a stub and is not enabled in app startup.

## Why It Is Portfolio-Worthy

This is a strong backend portfolio project because it demonstrates:
- async architecture under operational constraints,
- resilient message processing with retries/dead-lettering,
- integration-heavy system design (Zendesk, RabbitMQ, PostgreSQL, MCP, Telegram),
- runtime configurability without redeploy,
- pragmatic production concerns (logging, migrations, Docker deployment).

## Next Improvements

- Add tests (unit + integration + contract tests for external APIs).
- Add metrics/health endpoints and dashboarding.
- Add per-run model override in Playground.
- Finalize `AgentDirectiveWorker`.
- Extend multi-brand and multi-provider runtime controls.
