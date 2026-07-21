.PHONY: api build check check-all compose-down compose-up coverage evidence format format-check generate lint local-rc resource-preflight security sync test typecheck web

sync:
	uv sync --all-packages
	npm install

format:
	uv run ruff format .
	npm exec --workspace @study-agent/web prettier -- --write .

format-check:
	uv run ruff format --check .

generate:
	npm run generate:api

lint:
	uv run ruff check .
	npm run lint

typecheck:
	MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src \
		uv run mypy -p study_contracts -p study_agent -p study_worker
	npm run typecheck

coverage:
	mkdir -p .local/coverage
	COVERAGE_FILE=.local/coverage/.coverage uv run coverage erase
	COVERAGE_FILE=.local/coverage/.coverage uv run coverage run -m pytest
	COVERAGE_FILE=.local/coverage/.coverage uv run coverage combine .local/coverage
	COVERAGE_FILE=.local/coverage/.coverage uv run coverage report --fail-under=80

test: coverage
	npm test

build:
	npm run build

security:
	./scripts/security_check.sh

check-all:
	./scripts/check_all.sh

evidence:
	uv run python scripts/generate_implementation_manifest.py --quick

local-rc:
	./scripts/run_local_rc.sh --smoke

resource-preflight:
	uv run python scripts/run_resource_preflight.py

check:
	./scripts/check_workspace.sh
	$(MAKE) format-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) build

compose-up:
	docker compose -f infra/compose/compose.yml up -d --wait

compose-down:
	docker compose -f infra/compose/compose.yml down

api:
	uv run study-agent-api

web:
	npm run dev --workspace @study-agent/web
