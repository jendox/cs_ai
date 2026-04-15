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

## 5) Run PROD stack on VPS

1. Copy repo (or only `deploy/` + compose files) to VPS.
2. Put `deploy/.env.prod` with real values.
3. Ensure `CS_IMAGE/MCP_IMAGE` and tags point to Docker Hub images.

Apply migrations:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod --profile ops run --rm migrate
```

Start stack:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod up -d
```

Check app logs:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod logs -f app
```

Run Web Admin as a separate process/container using the same image and command:

```bash
uv run python run_web.py
```

If exposed publicly, put it behind HTTPS and set:

```dotenv
WEB__COOKIE_SECURE=true
```

Web Admin entrypoint: `/admin/login`.

On startup it bootstraps `WEB__BOOTSTRAP_USERNAME` as an active `superadmin` if
the user does not exist yet.

## 6) Notes

- In compose files, internal hostnames are fixed by service names:
  - Postgres: `postgres`
  - RabbitMQ: `rabbitmq`
  - MCP: `amazon-mcp`
- `MCP__HOST` must stay `amazon-mcp` for container-to-container access.
- RabbitMQ management port (`15672`) is exposed only in dev compose.
- Apply migrations before starting Web Admin; login requires the `admin_users`
  table.
- Keep `WEB__SESSION_SECRET` stable across restarts, otherwise existing admin
  sessions and CSRF cookies become invalid.
