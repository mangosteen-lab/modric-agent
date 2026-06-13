import argparse
import asyncio
import logging

from app.config.loader import load_config
from app.core.command_mgr import CCommandMgr
from app.logging_config import configure_logging
from app.ws.client import SoilWSClient

logger = logging.getLogger("modric_agent")


def run_agent() -> None:
    cfg = load_config()
    log_path = configure_logging(cfg["log_file"], cfg["log_level"])
    cmd_mgr = CCommandMgr(capacity=cfg["capacity"])
    client = SoilWSClient(
        wss_url=cfg["wss_url"],
        api_key=cfg["api_key"],
        name=cfg["name"],
        capacity=cfg["capacity"],
        auto_upgrade=cfg["auto_upgrade"],
        upgrade_channel=cfg["upgrade_channel"],
        labels=cfg["labels"],
        command_mgr=cmd_mgr,
    )
    logger.info("Starting Modric Agent - connecting to %s (logging to %s)",
                cfg["wss_url"], log_path)
    asyncio.run(client.run())


def main() -> None:
    parser = argparse.ArgumentParser(prog="modric-agent", description="Modric Soil worker")
    sub = parser.add_subparsers(dest="command")
    svc = sub.add_parser("service", help="install/manage the agent as an OS service")
    svc.add_argument("action", choices=["install", "uninstall", "start", "stop", "status"])
    args = parser.parse_args()

    if args.command == "service":
        from app.service import dispatch
        dispatch(args.action)
        return
    run_agent()


if __name__ == "__main__":
    main()
