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

    log_file = "logs/agent.log"
    log_level = "INFO"
    if cfg.has_section("logging"):
        log_file = cfg["logging"].get("file", log_file)
        log_level = cfg["logging"].get("level", log_level)
    log_file = os.getenv("MODRIC_AGENT_LOG_FILE", log_file)
    log_level = os.getenv("MODRIC_AGENT_LOG_LEVEL", log_level)

    # Local REST API (machine_version management) — loopback by default since it is
    # meant for callers running on the machine itself.
    rest_host = "127.0.0.1"
    rest_port = 8765
    if cfg.has_section("rest"):
        rest_host = cfg["rest"].get("host", rest_host)
        rest_port = cfg["rest"].getint("port", rest_port)
    rest_host = os.getenv("MODRIC_AGENT_REST_HOST", rest_host)
    rest_port = int(os.getenv("MODRIC_AGENT_REST_PORT", rest_port))

    # Where the machine_version is persisted (absent => first start reads 0).
    machine_version_file = agent_section.get("machine_version_file", "state/machine_version.json")
    machine_version_file = os.getenv("MODRIC_AGENT_MACHINE_VERSION_FILE", machine_version_file)

    return {
        "wss_url": cfg["toil"]["wss_url"],
        "api_key": cfg["toil"]["api_key"],
        "name": agent_section.get("name", ""),
        "capacity": int(agent_section.get("capacity", 10)),
        "auto_upgrade": agent_section.getboolean("auto_upgrade", fallback=True),
        "upgrade_channel": agent_section.get("upgrade_channel", "stable"),
        "labels": parse_labels(agent_section.get("labels", "")),
        "log_file": log_file,
        "log_level": log_level,
        "rest_host": rest_host,
        "rest_port": rest_port,
        "machine_version_file": machine_version_file,
    }


def parse_labels(raw: str) -> dict[str, str]:
    """Parse 'key=value, key2=value2' into a dict. Whitespace tolerant."""
    labels: dict[str, str] = {}
    for part in raw.replace("\n", ",").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key:
            labels[key] = value
    return labels
