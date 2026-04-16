UV ?= uv
DOCKER_COMPOSE ?= docker compose
DEV_COMPOSE_FILE ?= deploy/docker-compose.dev.yml
DEV_ENV_FILE ?= deploy/.env.dev

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

list: ## Отображает список доступных команд и их описания
	@echo "Cписок доступных команд:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
