"""Apply operator config edits (from Toil's machines panel) to config.ini.

Only non-`[toil]` sections may be changed — the Toil connection (wss_url/api_key)
stays locked. Edits merge into the existing file: keys that aren't provided keep
their current value, so a partial edit can't wipe an unrelated field. Values are
validated so a bad edit is rejected before it's written and the agent restarts.
"""
import configparser
from pathlib import Path

# Sections an operator may edit. `toil` is deliberately excluded.
EDITABLE_SECTIONS = ("agent", "rest", "logging")
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigUpdateError(ValueError):
    """Raised when an edit references a locked section or carries an invalid value."""


def _validate(section: str, key: str, value: str) -> None:
    from app.config.loader import parse_labels

    if section == "agent":
        if key == "capacity":
            try:
                if int(value) <= 0:
                    raise ValueError
            except ValueError:
                raise ConfigUpdateError("capacity must be a positive integer") from None
        elif key == "auto_upgrade":
            if value.strip().lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise ConfigUpdateError("auto_upgrade must be a boolean")
        elif key == "labels":
            if value.strip() and not parse_labels(value):
                raise ConfigUpdateError("labels must be comma-separated key=value pairs")
    elif section == "rest":
        if key == "port":
            try:
                if not (0 < int(value) < 65536):
                    raise ValueError
            except ValueError:
                raise ConfigUpdateError("rest port must be 1-65535") from None
    elif section == "logging":
        if key == "level" and value.strip().upper() not in LOG_LEVELS:
            raise ConfigUpdateError(f"logging level must be one of {sorted(LOG_LEVELS)}")


def write_config(
    path: str | Path, updates: dict[str, dict[str, object]]
) -> dict[str, dict[str, str]]:
    """Merge `updates` (section -> {key: value}) into the ini at `path`.

    Returns the applied {section: {key: value}} (only the keys actually written).
    Empty-string values are skipped ("leave unchanged"). Raises ConfigUpdateError for
    a locked section or an invalid value — nothing is written in that case.
    """
    path = Path(path)
    cfg = configparser.ConfigParser()
    cfg.read(path)

    applied: dict[str, dict[str, str]] = {}
    for section, keys in (updates or {}).items():
        if section not in EDITABLE_SECTIONS:
            raise ConfigUpdateError(f"section [{section}] is not editable")
        for key, raw in (keys or {}).items():
            value = "" if raw is None else str(raw)
            if value == "":
                continue  # leave unchanged
            _validate(section, key, value)
            if not cfg.has_section(section):
                cfg.add_section(section)
            cfg.set(section, key, value)
            applied.setdefault(section, {})[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        cfg.write(f)
    return applied
