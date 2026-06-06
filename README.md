# modric-agent

`modric-agent` is the outbound worker for Modric/Toil. It connects to the Toil WebSocket endpoint, registers the local machine, receives commands, executes scripts, and streams status and logs back to Toil.

## Requirements

- Python 3.11+
- uv

## Setup

```bash
git clone <repo-url> modric-agent
cd modric-agent
uv sync --extra dev
cp conf/config.example.ini conf/config.ini
```

Edit `conf/config.ini` for the machine:

```ini
[agent]
name = MY-MACHINE
capacity = 10
auto_upgrade = true
upgrade_channel = stable

[toil]
wss_url = wss://your-toil-host/ws/soil
api_key = your-registration-api-key
```

You can also point at another config file:

```bash
MODRIC_AGENT_CONFIG=/path/to/config.ini uv run python -m app.main
```

## Run

```bash
make run
```

## Run in a container

A Python 3.12 image is provided in `Dockerfile-py`. On start, `app.bootstrap`
renders `config.ini` from `MODRIC_*` environment variables (if set), then runs
the agent — so you can configure it entirely via env, with no file to mount.

```bash
# Build
docker build -f Dockerfile-py -t modric-agent:latest .

# Configure via environment (recommended)
docker run -d --name modric-agent --restart unless-stopped \
  -e MODRIC_TOIL_WSS_URL=ws://toil-host:8000/ws/soil \
  -e MODRIC_TOIL_API_KEY=<api-key> \
  -e MODRIC_AGENT_NAME=aba-agent-1 \
  -e MODRIC_AGENT_LABELS="template=LINUX_ABA, os=linux" \
  modric-agent:latest
```

Or via the Makefile (build + run):

```bash
make docker-build
export MODRIC_TOIL_WSS_URL=ws://toil-host:8000/ws/soil
export MODRIC_TOIL_API_KEY=<api-key>
export MODRIC_AGENT_NAME=aba-agent-1
make docker-run-env       # configure from env
# ...or mount a file instead:
make docker-run           # mounts conf/config.ini
```

### Environment variables

| Variable | Default | Maps to |
|---|---|---|
| `MODRIC_TOIL_WSS_URL` | — (required to render) | `[toil] wss_url` |
| `MODRIC_TOIL_API_KEY` | — (required to render) | `[toil] api_key` |
| `MODRIC_AGENT_NAME` | container hostname | `[agent] name` |
| `MODRIC_AGENT_CAPACITY` | `10` | `[agent] capacity` |
| `MODRIC_AGENT_AUTO_UPGRADE` | `false` | `[agent] auto_upgrade` |
| `MODRIC_AGENT_UPGRADE_CHANNEL` | `stable` | `[agent] upgrade_channel` |
| `MODRIC_AGENT_LABELS` | (none) | `[agent] labels` |
| `MODRIC_AGENT_CONFIG` | `/app/conf/config.ini` | path of the rendered/read config |

If neither `MODRIC_TOIL_WSS_URL` nor `MODRIC_TOIL_API_KEY` is set, nothing is
rendered and the agent uses an existing config file — mount one instead:

```bash
docker run -d -v "$(pwd)/conf/config.ini:/app/conf/config.ini:ro" modric-agent:latest
```

Notes:
- The agent connects **outbound** to Toil, so no ports need publishing.
- Use the reachable Toil URL — `localhost` inside the container is the container itself; use the host IP/DNS, `host.docker.internal` (Docker Desktop), or `--network host` (Linux).
- The api_key is never baked into the image (and never logged).
- The image includes `bash`, `curl`, `git`, and `jq` for typical Linux job steps; extend the Dockerfile if your scripts need more. Windows (`.bat`) steps won't run in a Linux container.
- `auto_upgrade` defaults to `false` in containers — redeploy a new image instead. If you enable it, run under `--restart unless-stopped`/an orchestrator so the agent restarts after a successful upgrade (exit code `75`).
- Logs stream to stdout/stderr — view with `docker logs -f modric-agent`.

## Test and Lint

```bash
make test
make lint
```

## Notes

- The worker initiates the outbound WebSocket connection, so external machines do not need inbound firewall rules.
- Local credentials belong in `conf/config.ini`, which is ignored by git.
- The protocol still uses the `/ws/soil` endpoint and internal Soil message names for compatibility with Toil.
- The agent reports its installed package version automatically; do not configure a per-machine version.
- Auto-upgrade enters drain mode first, waits for accepted work to finish, downloads an HTTPS artifact, verifies `sha256` when Toil provides it, starts an installer helper, and exits with code `75`.
- Run the agent under a supervisor such as systemd, a Windows service wrapper, or a container orchestrator so it is restarted after a successful auto-upgrade.
