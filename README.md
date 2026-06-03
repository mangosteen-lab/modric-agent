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
version = 1
name = MY-MACHINE
capacity = 10

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

## Test and Lint

```bash
make test
make lint
```

## Notes

- The worker initiates the outbound WebSocket connection, so external machines do not need inbound firewall rules.
- Local credentials belong in `conf/config.ini`, which is ignored by git.
- The protocol still uses the `/ws/soil` endpoint and internal Soil message names for compatibility with Toil.
