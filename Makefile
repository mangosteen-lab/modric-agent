.PHONY: help sync run test lint lint-fix install install-interactive uninstall status release-tarball release docker-build docker-run docker-run-env docker-logs docker-stop

# Container image / name (override: make docker-build IMAGE=foo:1.2.3)
IMAGE ?= modric-agent:latest
NAME  ?= modric-agent
CONFIG ?= $(CURDIR)/conf/config.ini
# Short git commit baked into the image so the containerized agent reports it to
# Toil (no .git in the image ⇒ git rev-parse can't run at runtime). Empty if this
# tree isn't a git checkout.
COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null)

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync          Install Modric Agent dependencies"
	@echo "  run           Run Modric Agent"
	@echo "  test          Run Modric Agent tests"
	@echo "  lint          Lint Modric Agent with ruff"
	@echo "  lint-fix      Auto-fix Soil lint issues"
	@echo "  install        Install the agent as an OS service (sudo/admin)"
	@echo "  install-interactive  Windows: run in the console desktop on logon (GUI steps)"
	@echo "  uninstall      Remove the agent OS service"
	@echo "  status         Show the agent OS service status"
	@echo "  build          Build a release wheel + print its sha256 (for self-upgrade)"
	@echo "  release-tarball  Build a source .tar.gz + print its sha256 (for git-style hot upgrade)"
	@echo "  release          Bump+commit version, tag, build tarball, publish a GitHub release (make release VERSION=1.0.1)"
	@echo "  docker-build   Build the container image ($(IMAGE))"
	@echo "  docker-run     Run the agent in a container (mounts $(CONFIG))"
	@echo "  docker-run-env Run the agent, configured from MODRIC_* env vars"
	@echo "  docker-logs    Follow the container logs"
	@echo "  docker-stop    Stop and remove the container"

sync:
	uv sync --extra dev

run: sync
	uv run python -m app.main

test:
	uv run --frozen --extra dev python -m pytest tests -v --tb=short

lint: sync
	uv run ruff check .

lint-fix: sync
	uv run ruff check --fix .

# Install/remove/inspect the agent as an OS service (systemd / launchd / Windows task).
# On Linux/macOS this typically needs elevation, e.g. `sudo make install`.
install: sync
	uv run python -m app.main service install

# Windows GUI hosts: don't run in Session 0. Remove the service and run the agent
# inside the logged-in console desktop via StartModricAgent.bat (+ Autologon).
install-interactive: sync
	uv run python -m app.main service install-interactive

uninstall:
	uv run python -m app.main service uninstall

status:
	uv run python -m app.main service status

# Build a distributable wheel and print its sha256 (the values to wire into Toil's
# [soil] upgrade_artifact_url + upgrade_artifact_sha256). Bump version in pyproject.toml first.
build:
	rm -rf dist
	uv build --wheel
	@echo "Built artifacts (host at an HTTPS URL, then set Toil [soil] latest_version/url/sha256):"
	@for f in dist/*.whl; do \
		echo "  $$f"; \
		echo "  sha256 = $$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$$f")"; \
	done

# Build a source tarball for the "Upgrade agent" button: the agent downloads this,
# extracts the code over its dir (preserving conf/config.ini + .venv), `uv sync`s and
# restarts. Uses `git archive` (tracked files only, nested under a top-level dir like
# GitHub's source archives). Host the .tar.gz and set Toil [soil]
# upgrade_artifact_url/upgrade_artifact_sha256 to it. Bump pyproject.toml version first.
release-tarball:
	rm -rf dist && mkdir -p dist
	$(eval VERSION := $(shell python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"))
	git archive --format=tar.gz --prefix=modric-agent-$(VERSION)/ -o dist/modric-agent-$(VERSION).tar.gz HEAD
	@echo "Built source tarball (host at an HTTPS URL, then set Toil [soil] latest_version/url/sha256):"
	@f=dist/modric-agent-$(VERSION).tar.gz; \
		echo "  $$f"; \
		echo "  sha256 = $$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$$f")"

# Full release: bump + commit the version, tag, build the source tarball, and publish
# a GitHub release with it attached (needs `gh auth login`). Verifies the tarball
# actually carries the version, then prints the Toil [soil] values. `make release VERSION=1.0.1`.
release:
	scripts/release.sh $(VERSION)

docker-build:
	docker build -f Dockerfile-py --build-arg MODRIC_AGENT_COMMIT=$(COMMIT) -t $(IMAGE) .

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
