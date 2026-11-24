.PHONY: install dev-api dev-web contracts check test lint typecheck

API = cd apps/api &&
WEB = cd apps/web &&

install:            ## Install API and web dependencies
	$(API) uv sync
	$(WEB) npm ci

dev-api:            ## Run the API on :8000 with reload
	$(API) uv run fastapi dev --port 8000

dev-web:            ## Run the web app on :3000
	$(WEB) npm run dev

contracts:          ## Regenerate the JSON Schema and TypeScript types from the Pydantic models
	$(API) uv run python -m scripts.export_contracts
	$(WEB) npm run gen:types

lint:
	$(API) uv run ruff check . && uv run ruff format --check .
	$(WEB) npm run lint

typecheck:
	$(API) uv run mypy
	$(WEB) npm run typecheck

test:
	$(API) uv run pytest -q
	$(WEB) npm test

check: lint typecheck test   ## Everything CI runs
