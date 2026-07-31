# Common development commands.
.PHONY: help install run test lint format migrate revision up down logs worker beat purge

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime and development dependencies
	pip install -r requirements-dev.txt

run:  ## Start the API with autoreload
	uvicorn app.main:app --reload

test:  ## Run the test suite
	pytest -q

lint:  ## Lint the codebase
	ruff check .

format:  ## Apply formatting and safe autofixes
	ruff format . && ruff check . --fix

migrate:  ## Apply all migrations
	alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add table"
	alembic revision --autogenerate -m "$(m)"

up:  ## Start the full stack
	docker compose up --build -d

down:  ## Stop the stack
	docker compose down

logs:  ## Follow API logs (verification codes appear here)
	docker compose logs -f api

worker:  ## Run a Celery worker locally
	celery -A app.workers.celery_app.celery_app worker --loglevel=INFO

beat:  ## Run the Celery scheduler locally
	celery -A app.workers.celery_app.celery_app beat --loglevel=INFO

purge:  ## Trigger the unverified-user cleanup task now
	docker compose exec worker python -c "from app.workers.tasks import purge_unverified_users; print(purge_unverified_users.delay().get(timeout=60))"
