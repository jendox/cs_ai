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

## 6) Notes

- In compose files, internal hostnames are fixed by service names:
  - Postgres: `postgres`
  - RabbitMQ: `rabbitmq`
  - MCP: `amazon-mcp`
- `MCP__HOST` must stay `amazon-mcp` for container-to-container access.
- RabbitMQ management port (`15672`) is exposed only in dev compose.
