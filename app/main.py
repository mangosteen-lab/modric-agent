import asyncio
import logging

from app.config.loader import load_config
from app.core.command_mgr import CCommandMgr
from app.ws.client import SoilWSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("modric_agent")


def main():
    cfg = load_config()
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
    logger.info("Starting Modric Agent - connecting to %s", cfg["wss_url"])
    asyncio.run(client.run())


if __name__ == "__main__":
    main()
