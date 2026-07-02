# modric-agent

`modric-agent` is the outbound worker for Modric/Toil. It connects to the Toil WebSocket endpoint, registers the local machine, receives commands, executes scripts, and streams status and logs back to Toil.

## Install (one-liner)

Fetches the latest release, installs to a system path, prompts for the Toil connection, and registers the OS service.

**Linux / macOS** — installs to `/opt/mangosteen/modric-agent`:

```bash
curl -fsSL https://raw.githubusercontent.com/mangosteen-lab/modric-agent/master/scripts/install.sh | sudo bash
```

**Windows** (elevated PowerShell) — installs to `C:\Program Files\mangosteen\modric-agent`, and asks whether to run as a background service or in interactive-desktop mode (for GUI job steps):

```powershell
irm https://raw.githubusercontent.com/mangosteen-lab/modric-agent/master/scripts/install.ps1 | iex
```

Non-interactive (CI/automation): pre-set the `MODRIC_*` env vars and no prompts are shown:

```bash
MODRIC_TOIL_WSS_URL=wss://toil/ws/soil MODRIC_TOIL_API_KEY=key \
  MODRIC_AGENT_LABELS="template=LINUX_ABA" sudo -E bash install.sh
```

| Env var | Purpose | Default |
| --- | --- | --- |
| `MODRIC_TOIL_WSS_URL` | Toil WebSocket URL (required) | — |
| `MODRIC_TOIL_API_KEY` | registration API key (required) | — |
| `MODRIC_AGENT_NAME` | machine name | hostname |
| `MODRIC_AGENT_LABELS` | `key=value, …` selectors | (empty) |
| `MODRIC_AGENT_CAPACITY` | max concurrent jobs | `10` |
| `MODRIC_AGENT_UPGRADE_CHANNEL` / `MODRIC_AGENT_AUTO_UPGRADE` | upgrade channel / toggle | `stable` / `true` |
| `MODRIC_AGENT_HOME` | install dir | per-OS path above |
| `MODRIC_AGENT_TARBALL_URL` | pin a release / internal mirror | latest release |
| `FORCE_CONFIG` | overwrite an existing `config.ini` | (keep) |

The steps below describe a manual/from-source setup instead.

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

## Run as a service (Linux / macOS / Windows)

Install the agent so it starts on boot and is restarted if it exits (including the
auto-upgrade exit code `75`):

```bash
sudo make install-service      # Linux (systemd) / macOS (launchd)
make uninstall-service

# equivalently, directly:
python -m app.main service install      # install + start
python -m app.main service start|stop|status|uninstall
```

- **Linux** — a systemd unit at `/etc/systemd/system/modric-agent.service` (`Restart=always`).
- **macOS** — a launchd job (`~/Library/LaunchAgents/com.microstrategy.modric-agent.plist`,
  or `/Library/LaunchDaemons` when run as root), `KeepAlive`.
- **Windows** — a Scheduled Task that runs at startup (run from an elevated prompt). For
  auto-restart on crash, run the agent under NSSM instead.

The service runs the current Python with `conf/config.ini` (override the path with
`MODRIC_AGENT_CONFIG`).

## Logging

The agent logs to a rotating file **and** the console at level **INFO** by default. Configure
via `conf/config.ini` (or env):

```ini
[logging]
file = logs/agent.log
level = INFO          # DEBUG / INFO / WARNING / ERROR
```

`MODRIC_AGENT_LOG_FILE` / `MODRIC_AGENT_LOG_LEVEL` override these (and are rendered into the
container config by `app/bootstrap.py`).

## Self-upgrade: publishing a new version

Agents upgrade by downloading a **published wheel**, not by pulling git. A `git push` alone
does nothing to running agents. To ship new code:

1. **Bump** `version` in `pyproject.toml` (must be higher — Toil only offers a newer version).
2. **Build + publish** the wheel at an HTTPS URL and note its sha256:
   ```bash
   make build        # builds dist/*.whl and prints the sha256
   ```
   Or push a tag (`git tag v1.1.0 && git push --tags`) — `.github/workflows/release.yml`
   builds the wheel + `SHA256SUMS` and attaches them to a GitHub Release.
3. **Point Toil at it** — in the Toil server's `conf/config.ini [soil]`:
   `latest_version`, `upgrade_artifact_url` (the wheel URL), `upgrade_artifact_sha256`,
   and a matching `upgrade_channel`.
4. Agents with `auto_upgrade = true` and the matching channel pick it up within a few minutes.

### What happens on the agent (and how it restarts)

`app/core/updater.py` drains in-flight work, downloads + sha256-verifies the wheel, launches a
**detached installer**, and the agent **exits with code 75**. The installer waits for the old
process to exit, runs `pip install --upgrade <wheel>`, then **restarts the agent**:

- It calls `app/service.py::restart_after_upgrade()`, which restarts the OS service it's managed
  by (`systemctl restart` / `launchctl kickstart -k` / `schtasks /run`).
- The supervisor's own policy is the **fallback** (systemd `Restart=always`, launchd `KeepAlive`,
  container `--restart`). The systemd unit uses `KillMode=process` so the detached installer
  survives the agent's exit.

So **the agent must be installed as a service (or run under a restart-enabled supervisor/container)**
for self-upgrade to relaunch it — see "Run as a service" above. Running it by hand will simply exit
75 on upgrade and stop, leaving you to start it again. Also note: the wheel install only takes effect
for **pip-installed** deployments; a raw `git clone` + `python -m app.main` won't be replaced by the
wheel (use a deploy that pulls + restarts instead).

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
