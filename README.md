# AI Customer Support Assistant (Zendesk + LLM + Telegram Admin)

Production-style backend service for automated Zendesk ticket handling with:
- asynchronous event processing,
- AI-generated replies,
- runtime admin controls via Telegram,
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
5. Manage runtime behavior from Telegram without redeploy.

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
- Structured JSON logging with context and secret redaction.

## Architecture Overview

Main runtime services:
- `Poller` (per brand)
- `InitialReplyWorker` (per brand)
- `FollowUpReplyWorker` (per brand)
- `TicketClosedWorker` (per brand)
- `TelegramAdmin`

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

4. Apply migrations:

```bash
uv run alembic upgrade head
```

5. Run app:

```bash
uv run python run.py
```

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

## Current Scope and Limitations

- Active LLM provider in runtime: Google.
- Current production setup uses one supported brand.
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
- Finalize `AgentDirectiveWorker`.
- Extend multi-brand and multi-provider runtime controls.
