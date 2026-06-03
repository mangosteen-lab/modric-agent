import configparser
import os
from pathlib import Path


def load_config(path: str | None = None) -> dict:
    config_path = Path(path or os.getenv("MODRIC_AGENT_CONFIG", "conf/config.ini"))
    cfg = configparser.ConfigParser()
    if not cfg.read(config_path):
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Copy conf/config.example.ini to conf/config.ini and update it for this machine."
        )

    agent_section = cfg["agent"] if cfg.has_section("agent") else cfg["soil"]
    return {
        "wss_url": cfg["toil"]["wss_url"],
        "api_key": cfg["toil"]["api_key"],
        "name": agent_section.get("name", ""),
        "capacity": int(agent_section.get("capacity", 10)),
        "version": int(agent_section.get("version", 1)),
    }
