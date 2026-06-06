.PHONY: help sync run test lint lint-fix docker-build docker-run docker-run-env docker-logs docker-stop

# Container image / name (override: make docker-build IMAGE=foo:1.2.3)
IMAGE ?= modric-agent:latest
NAME  ?= modric-agent
CONFIG ?= $(CURDIR)/conf/config.ini

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync          Install Modric Agent dependencies"
	@echo "  run           Run Modric Agent"
	@echo "  test          Run Modric Agent tests"
	@echo "  lint          Lint Modric Agent with ruff"
	@echo "  lint-fix      Auto-fix Soil lint issues"
	@echo "  docker-build   Build the container image ($(IMAGE))"
	@echo "  docker-run     Run the agent in a container (mounts $(CONFIG))"
	@echo "  docker-run-env Run the agent, configured from MODRIC_* env vars"
	@echo "  docker-logs    Follow the container logs"
	@echo "  docker-stop    Stop and remove the container"

sync:
	uv sync --extra dev

run: sync
	uv run python -m app.main

test: sync
	uv run pytest tests -v --tb=short

lint: sync
	uv run ruff check .

lint-fix: sync
	uv run ruff check --fix .

docker-build:
	docker build -f Dockerfile-py -t $(IMAGE) .

docker-run:
	@test -f "$(CONFIG)" || { echo "Missing $(CONFIG) — copy conf/config.example.ini and edit it."; exit 1; }
	docker run -d --name $(NAME) --restart unless-stopped \
		-v "$(CONFIG):/app/conf/config.ini:ro" \
		$(IMAGE)
	@echo "Started '$(NAME)'. Follow logs with: make docker-logs"

# Configure from the environment instead of a mounted file. Export the MODRIC_*
# vars first (at least MODRIC_TOIL_WSS_URL and MODRIC_TOIL_API_KEY); `-e VAR`
# forwards each from the current shell. Bootstrap renders them to config.ini.
docker-run-env:
	@test -n "$$MODRIC_TOIL_WSS_URL" -a -n "$$MODRIC_TOIL_API_KEY" || \
		{ echo "Set MODRIC_TOIL_WSS_URL and MODRIC_TOIL_API_KEY in the environment first."; exit 1; }
	docker run -d --name $(NAME) --restart unless-stopped \
		-e MODRIC_TOIL_WSS_URL -e MODRIC_TOIL_API_KEY \
		-e MODRIC_AGENT_NAME -e MODRIC_AGENT_CAPACITY \
		-e MODRIC_AGENT_AUTO_UPGRADE -e MODRIC_AGENT_UPGRADE_CHANNEL \
		-e MODRIC_AGENT_LABELS \
		$(IMAGE)
	@echo "Started '$(NAME)'. Follow logs with: make docker-logs"

docker-logs:
	docker logs -f $(NAME)

docker-stop:
	-docker rm -f $(NAME)
