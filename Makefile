UV ?= uv
DOCKER_COMPOSE ?= docker compose
DEV_COMPOSE_FILE ?= deploy/docker-compose.dev.yml
DEV_ENV_FILE ?= deploy/.env.dev
PROD_COMPOSE_FILE ?= deploy/docker-compose.prod.yml
PROD_ENV_FILE ?= deploy/.env.prod

# Docker Hub image coordinates for the CS app (override via env or make CS_IMAGE=...)
CS_IMAGE ?= yourdockerhub/cs-app
CS_TAG ?= latest

# Ruff
lint: ## Проверяет линтерами код в репозитории
	ruff check .

format: ## Запуск автоформатера
	ruff check --fix .

# Alembic
init-async-alembic: ## Инициализация асинхронного alembic
	$(UV) run alembic init -t async alembic

makemigrations: ## Создание миграции (использование: make migrations message="Init migration")
	$(UV) run alembic revision --autogenerate -m "$(message)"

migrate: ## Применение миграции
	$(UV) run alembic upgrade head

downgrade: ## Откат миграции
	$(UV) run alembic downgrade -1

up: ## Запустить окружение
	$(UV) run docker-compose up -d

down: ## Остановить окружение
	$(UV) run docker-compose down

down-v: ## Остановить окружение с очисткой локального хранилища
	$(UV) run docker-compose down -v

mcp-build: ## Собрать dev image Amazon MCP из соседнего репозитория ../amazon_mcp
	docker build -f deploy/Dockerfile.mcp -t amazon-mcp:dev ../amazon_mcp

mcp-up: ## Запустить только Amazon MCP из deploy/docker-compose.dev.yml
	$(DOCKER_COMPOSE) -f $(DEV_COMPOSE_FILE) --env-file $(DEV_ENV_FILE) up -d amazon-mcp

mcp-down: ## Остановить Amazon MCP из dev compose
	$(DOCKER_COMPOSE) -f $(DEV_COMPOSE_FILE) --env-file $(DEV_ENV_FILE) stop amazon-mcp

mcp-logs: ## Смотреть логи Amazon MCP из dev compose
	$(DOCKER_COMPOSE) -f $(DEV_COMPOSE_FILE) --env-file $(DEV_ENV_FILE) logs -f amazon-mcp

server: ## Запустить Web Admin FastAPI server
	$(UV) run python run_web.py

# ---- Prod deployment helpers --------------------------------------------------

app-build: ## Собрать prod-образ cs-app с тегами $(CS_IMAGE):$(CS_TAG) и :<git sha>
	docker build -f deploy/Dockerfile.app -t $(CS_IMAGE):$(CS_TAG) -t $(CS_IMAGE):$$(git rev-parse --short HEAD) .

app-push: ## Запушить оба тега ($(CS_TAG) и git sha) в Docker Hub
	docker push $(CS_IMAGE):$(CS_TAG)
	docker push $(CS_IMAGE):$$(git rev-parse --short HEAD)

prod-pull: ## На сервере: спулить свежий образ из Docker Hub
	$(DOCKER_COMPOSE) -f $(PROD_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) pull

prod-migrate: ## На сервере: применить миграции одноразовым контейнером (profile ops)
	$(DOCKER_COMPOSE) -f $(PROD_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) --profile ops run --rm migrate

prod-up: ## На сервере: поднять/обновить стек (app + web)
	$(DOCKER_COMPOSE) -f $(PROD_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) up -d

prod-logs: ## На сервере: логи app и web
	$(DOCKER_COMPOSE) -f $(PROD_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) logs -f app web

prod-down: ## На сервере: остановить prod стек (без удаления томов)
	$(DOCKER_COMPOSE) -f $(PROD_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) down

prod-deploy: prod-pull prod-migrate prod-up ## На сервере: полный upgrade = pull → migrate → up

list: ## Отображает список доступных команд и их описания
	@echo "Cписок доступных команд:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
