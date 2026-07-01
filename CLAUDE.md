# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`modric-agent` is the **Soil worker** for the Modric CI system. It connects **outbound** to the Toil
backend over a WebSocket, registers the local machine, receives `EXECUTE`/`KILL` commands, runs
scripts, and streams logs + status back. The backend lives in the sibling `modric` repo
(`modric/Toil/app/ws/soil_ws.py` is the server side of the protocol below).

## Commands

Uses **`uv`** (not a `.venv/bin` layout). All targets in the `Makefile`:
```bash
make sync          # uv sync --extra dev
make run           # uv run python -m app.main
make test          # uv run pytest tests -v
make lint          # uv run ruff check .   (line-length 100, E/F/I)
uv run pytest tests/test_x.py::test_name   # single test (asyncio_mode = auto)
```
Config comes from `conf/config.ini` (copy `conf/config.example.ini`) or `MODRIC_AGENT_CONFIG`.
Container builds (`Dockerfile-py`) render `config.ini` from `MODRIC_*` env vars via `app/bootstrap.py`.

## Architecture

`app/main.py` → `app/ws/client.py` opens the WebSocket to `[toil] wss_url`, authenticates with
`[toil] api_key`, and runs the receive loop. Flow:

1. **Register** — send the machine's name, capacity, OS, version, and labels (used by Toil for
   label-selector machine reservation). Stays connected with periodic heartbeats.
2. **Execute** — on an `EXECUTE`, `app/core/command_mgr.py` (`CCommandMgr`) admits the command up to
   `[agent] capacity`, then `app/core/run_script.py` (`CRunScriptCommand`) writes the script to a temp
   file and runs it by `EScriptType` (`app/core/command.py`: BAT / PYTHON / SHELL / POWERSHELL),
   streaming stdout/stderr back as log `CHUNK`s and finishing with `COMMAND_DONE` (exit code + status).
3. **Kill** — terminates a running command by id.
4. **Self-upgrade** — `app/core/updater.py` handles an upgrade request when `[agent] auto_upgrade`
   is on; a successful upgrade exits with code **75** so a supervisor/`--restart` relaunches the agent.
   The artifact may be a **wheel** (pip-installed) or a **source tarball/zip** (extracted over the
   agent dir — preserving `conf/config.ini`, `.venv`, `logs`, `state` — then `uv sync`). Toil's
   machines panel can trigger a **forced** upgrade per machine (`force` bypasses the auto_upgrade gate;
   `make release-tarball` builds the source archive to host + point `[soil] upgrade_artifact_url` at).
5. **Config edit** — Toil pushes `SET_CONFIG` (operator "Edit" in the machines panel); the agent merges
   the non-`[toil]` keys into `config.ini` via `app/config/writer.py`, acks
   `CONFIG_UPDATED`/`CONFIG_REJECTED`, then drains and exits **75** to re-REGISTER with the new values.
   **Every config change must stay backward-compatible: keep new keys optional with a default in
   `app/config/loader.py` (`.get(key, default)`) so an existing `config.ini` keeps working.**

## Protocol contract (keep in sync with Toil)

The message types and field names (`REGISTER`, `EXECUTE`, `CHUNK`, `COMMAND_DONE`, `KILL`,
`SET_MACHINE_VERSION`, `SET_CONFIG`, command ids, script types) are a contract with
`modric/Toil/app/ws/soil_ws.py` and `modric/Toil/app/core/remote_execute.py`. Change both sides
together.

## machine_version

`app/core/machine_version.py` holds the **machine_version**: a user-defined `YYYYMMDDXX` integer
(e.g. `2026070101`, `0` = unset) for *the machine*, distinct from the agent software version
(`app/core/version.py`). It is persisted to `[agent] machine_version_file` (absent on a fresh machine
⇒ first start reads `0`), reported to Toil in every REGISTER/PONG heartbeat, and exposed to job steps
as Toil's `MACHINE_<idx>_VERSION` built-in. Two update paths, both landing in the same
`MachineVersionStore`: (1) a deploy/upgrade step running on the machine calls the agent's **local REST
API** (`app/rest/server.py`, stdlib `http.server` on a daemon thread, loopback `[rest] host:port`,
default `127.0.0.1:8765`): `GET/PUT /machine-version`; (2) an operator edits it in Toil's machines
panel, which pushes a `SET_MACHINE_VERSION` message over the WebSocket — the agent persists it and
replies `MACHINE_VERSION_UPDATED`/`MACHINE_VERSION_REJECTED` (`app/ws/client.py`).

## Notes

- The agent connects outbound only, apart from the loopback machine_version REST API above. Use a Toil
  URL reachable from the machine (not `localhost` inside a container).
- The Linux container image ships `bash`, `curl`, `git`, `jq`; `.bat` steps require a Windows host.
- End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
