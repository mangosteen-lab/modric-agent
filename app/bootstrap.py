"""Container entrypoint: render conf/config.ini from environment variables, then
run the agent.

Pass configuration via env (12-factor style):

    MODRIC_TOIL_WSS_URL        ws(s)://toil-host/ws/soil     (required to render)
    MODRIC_TOIL_API_KEY        the account api key           (required to render)
    MODRIC_AGENT_NAME          machine name        (default: container hostname)
    MODRIC_AGENT_CAPACITY      max concurrent commands       (default: 10)
    MODRIC_AGENT_AUTO_UPGRADE  true/false          (default: false in containers)
    MODRIC_AGENT_UPGRADE_CHANNEL                              (default: stable)
    MODRIC_AGENT_LABELS        "key=value, key2=value2"      (default: empty)
    MODRIC_AGENT_CONFIG        where to write/read the ini   (default: conf/config.ini)

If MODRIC_TOIL_WSS_URL / MODRIC_TOIL_API_KEY are not set, nothing is rendered and
the agent uses an existing config file (e.g. a mounted conf/config.ini).
"""
import configparser
import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger("modric_agent.bootstrap")


def render_config() -> bool:
    """Render the config file from env. Returns True if a file was written."""
    wss_url = os.getenv("MODRIC_TOIL_WSS_URL")
    api_key = os.getenv("MODRIC_TOIL_API_KEY")
    if not (wss_url and api_key):
        return False

    cfg = configparser.ConfigParser()
    cfg["agent"] = {
        "name": os.getenv("MODRIC_AGENT_NAME") or socket.gethostname(),
        "capacity": os.getenv("MODRIC_AGENT_CAPACITY", "10"),
        # containers should redeploy rather than self-upgrade, so default off
        "auto_upgrade": os.getenv("MODRIC_AGENT_AUTO_UPGRADE", "false"),
        "upgrade_channel": os.getenv("MODRIC_AGENT_UPGRADE_CHANNEL", "stable"),
        "labels": os.getenv("MODRIC_AGENT_LABELS", ""),
    }
    cfg["toil"] = {"wss_url": wss_url, "api_key": api_key}

    path = Path(os.getenv("MODRIC_AGENT_CONFIG", "conf/config.ini"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            cfg.write(f)
    except OSError as exc:
        raise SystemExit(
            f"Could not write rendered config to {path}: {exc}. "
            "When configuring via environment variables, do not also mount a "
            "read-only file at that path."
        ) from exc

    logger.info(
        "Rendered %s from environment (name=%s, wss_url=%s, labels=%s)",
        path, cfg["agent"]["name"], wss_url, cfg["agent"]["labels"] or "(none)",
    )
    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not render_config():
        logger.info(
            "No MODRIC_TOIL_WSS_URL/MODRIC_TOIL_API_KEY in environment — "
            "using existing config file."
        )
    from app.main import main as run_agent
    run_agent()


if __name__ == "__main__":
    main()
