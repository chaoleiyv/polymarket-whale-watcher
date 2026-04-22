.PHONY: setup run dashboard briefing markets test lint clean docker-build docker-run help

PYTHON = python3
VENV = venv
PIP = $(VENV)/bin/pip
PY = $(VENV)/bin/python

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## One-click setup (venv + dependencies + .env)
	@chmod +x setup.sh && ./setup.sh

run: ## Start the whale watcher
	$(PY) -m src.main run

run-debug: ## Start with debug logging
	$(PY) -m src.main run --debug

dashboard: ## Start the web dashboard
	$(PY) -m src.main dashboard

briefing: ## Generate today's briefing
	$(PY) -m src.main briefing --today

markets: ## Show trending markets
	$(PY) -m src.main check-markets --limit 20

test: ## Run tests
	$(PY) -m pytest tests/ -v

lint: ## Run linter
	$(VENV)/bin/ruff check src/

clean: ## Remove generated files (keep data)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true

docker-build: ## Build Docker image
	docker build -t polymarket-whale-watcher .

docker-run: ## Run in Docker
	docker run --env-file .env -v $(PWD)/data:/app/data -v $(PWD)/reports:/app/reports polymarket-whale-watcher
