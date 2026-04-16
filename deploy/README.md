# Deploy Guide (Docker / VPS)

## 1) Files in this folder

- `Dockerfile.app` — image for this project (`cs` service).
- `Dockerfile.mcp` — image for sibling project `amazon_mcp` (build with external context).
- `docker-compose.dev.yml` — local/dev stack.
- `docker-compose.prod.yml` — VPS/prod stack.
- `.env.dev.example` / `.env.prod.example` — environment templates.

## 2) Prepare env files

From project root:

```bash
cp deploy/.env.dev.example deploy/.env.dev
cp deploy/.env.prod.example deploy/.env.prod
```

Fill real secrets in both files.

## 3) Build images

Build CS image (from this repo):

```bash
docker build -f deploy/Dockerfile.app -t cs-app:dev .
```

Build Amazon MCP image (from sibling repo):

```bash
docker build -f deploy/Dockerfile.mcp -t amazon-mcp:dev ../amazon_mcp
```

The project `Makefile` also exposes convenience commands:

```bash
make mcp-build
make mcp-up
make mcp-logs
make mcp-down
```

Optional push to Docker Hub:

```bash
docker tag cs-app:dev yourdockerhub/cs-app:latest
docker tag amazon-mcp:dev yourdockerhub/amazon-mcp:latest
docker push yourdockerhub/cs-app:latest
docker push yourdockerhub/amazon-mcp:latest
```

## 4) Run DEV stack

```bash
docker compose -f deploy/docker-compose.dev.yml --env-file deploy/.env.dev up -d
```

Run DB migrations (inside dev app image):

```bash
docker compose -f deploy/docker-compose.dev.yml --env-file deploy/.env.dev run --rm app uv run alembic upgrade head
```

Check logs:

```bash
docker compose -f deploy/docker-compose.dev.yml --env-file deploy/.env.dev logs -f app
```

Run Web Admin locally from the project root:

```bash
uv run python run_web.py
```

The Web Admin uses the same database and env file values. It is not started by
the current compose files. Default URL: `http://localhost:8080/admin/login`.
For LLM Playground tests that use Amazon tools, keep `amazon-mcp` running:

```bash
make mcp-up
```

If MCP is not available, Web Admin still starts, but tool-dependent Playground
generation runs may be stored as failed runs.

## 5) Run PROD stack on VPS

The prod stack now includes a `web` service that serves the FastAPI Web Admin
(`run_web.py`) from the same image as the worker (`app`). Both containers share
the `.env.prod` file and talk to the same Postgres / RabbitMQ instances.

### 5.1) Build & push the image (on your dev machine)

```bash
make app-build               # builds yourdockerhub/cs-app:latest + :<git sha>
make app-push                # pushes both tags

# override image/tag if needed:
make app-build CS_IMAGE=acme/cs-app CS_TAG=v1.2.3
make app-push  CS_IMAGE=acme/cs-app CS_TAG=v1.2.3
```

(Same flow for the MCP sidecar if it also changed — see `make mcp-build`.)

### 5.2) Initial setup (first time on VPS)

1. Copy `deploy/` to the server (compose files + env example).
2. Create `deploy/.env.prod` from `.env.prod.example` and fill real values.
3. Ensure `CS_IMAGE` / `CS_TAG` / `MCP_IMAGE` / `MCP_TAG` in the env file point
   to the Docker Hub coordinates you pushed above.
4. Pull images, run migrations, start the stack:

```bash
make prod-pull
make prod-migrate
make prod-up
```

Or the equivalent raw commands:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod pull
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod \
  --profile ops run --rm migrate
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod up -d
```

### 5.3) Routine upgrades (after pushing a new image)

```bash
make prod-deploy       # = prod-pull → prod-migrate → prod-up
```

Rolling individual steps:

```bash
make prod-pull         # download new image
make prod-migrate      # apply Alembic migrations (idempotent)
make prod-up           # recreate containers that changed
make prod-logs         # tail app + web
```

### 5.4) Web Admin HTTPS via Caddy

The prod stack includes a **Caddy** reverse proxy that automatically obtains
a TLS certificate from Let's Encrypt and serves the admin UI over HTTPS.

**Prerequisites on the VPS:**

1. Create a DNS **A record**: `cs.partach-dev.ru` → VPS IP address.
2. Open ports **80** and **443** in the firewall (`ufw allow 80,443/tcp`).
3. Set `CS_DOMAIN=cs.partach-dev.ru` in `deploy/.env.prod` (already the default).
4. Keep `WEB__COOKIE_SECURE=true`.

After `make prod-up` the admin is reachable at:

```
https://cs.partach-dev.ru/admin/login
```

Caddy stores certificates in the `caddy_data` volume. As long as that volume
persists, renewals are automatic and invisible.

To use a different domain, change `CS_DOMAIN` in `.env.prod` and re-up.

Web Admin entrypoint: `/admin/login`. On first startup it bootstraps
`WEB__BOOTSTRAP_USERNAME` as an active `superadmin` if that user does not yet
exist.

### 5.5) Rollback

Pin an older tag and re-up:

```bash
CS_TAG=<previous-git-sha> make prod-deploy
```

(Alembic migrations are forward-only; if a rollback needs schema changes, run
`alembic downgrade` explicitly via `docker compose ... run --rm migrate \
uv run alembic downgrade -1`.)

## 6) Notes

- In compose files, internal hostnames are fixed by service names:
  - Postgres: `postgres`
  - RabbitMQ: `rabbitmq`
  - MCP: `amazon-mcp`
- `MCP__HOST` must stay `amazon-mcp` for container-to-container access.
- RabbitMQ management port (`15672`) is exposed only in dev compose.
- Always run `prod-migrate` before `prod-up` after an image change — Web Admin
  login requires the `admin_users` table, Playground requires `llm_playground_*`,
  etc.
- Keep `WEB__SESSION_SECRET` stable across restarts, otherwise existing admin
  sessions and CSRF cookies become invalid.
- `app` (worker) and `web` (admin) are intentionally separate services: you can
  restart the admin UI without touching the poller / reply workers and vice
  versa (`docker compose ... restart web` / `... restart app`).
