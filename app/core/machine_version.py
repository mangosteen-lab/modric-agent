"""The machine_version: a user-defined version number for *the machine*, distinct
from the agent software version (see app/core/version.py).

It is a `YYYYMMDDXX` integer (e.g. 2026070101) that a deploy/upgrade job sets on
the machine at runtime through the agent's local REST API. The agent stores it and
reports it to Toil in every heartbeat, so Toil can decide whether the machine is
up to date (surfaced to job steps as the `MACHINE_<idx>_VERSION` runtime var).

`0` is the reserved "unset" value: a machine that has never been versioned reads 0.
The value is persisted to a small JSON file so it survives an agent restart; the
file is absent on a brand-new machine, so the first start reads 0.
"""
import json
import logging
import threading
from datetime import date
from pathlib import Path

logger = logging.getLogger("modric_agent.machine_version")

# Reserved "not versioned yet" value.
UNSET_MACHINE_VERSION = 0


class InvalidMachineVersionError(ValueError):
    """Raised when a value is not 0 and not a valid YYYYMMDDXX version."""


def validate_machine_version(value: object) -> int:
    """Coerce `value` to a valid machine_version int or raise.

    Accepts the reserved 0, or a 10-digit ``YYYYMMDDXX`` where ``YYYYMMDD`` is a
    real calendar date and ``XX`` is a 00-99 same-day sequence number.
    """
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise InvalidMachineVersionError(f"machine_version must be an integer, got {value!r}")

    if ivalue == UNSET_MACHINE_VERSION:
        return UNSET_MACHINE_VERSION
    if ivalue < 0:
        raise InvalidMachineVersionError("machine_version must not be negative")

    text = str(ivalue)
    if len(text) != 10:
        raise InvalidMachineVersionError(
            f"machine_version must be 10 digits (YYYYMMDDXX), got {text!r}"
        )
    year, month, day = int(text[0:4]), int(text[4:6]), int(text[6:8])
    try:
        date(year, month, day)  # validates month/day ranges for the year
    except ValueError as exc:
        raise InvalidMachineVersionError(f"machine_version has an invalid date: {exc}") from exc
    return ivalue


class MachineVersionStore:
    """Thread-safe holder for the machine_version, persisted to `path`.

    Read from the WebSocket loop (to fill heartbeats) and written from the REST
    server thread, so all access is guarded by a lock.
    """

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._value = self._load()

    def _load(self) -> int:
        if not self._path or not self._path.exists():
            return UNSET_MACHINE_VERSION
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return validate_machine_version(data.get("machine_version", UNSET_MACHINE_VERSION))
        except Exception as exc:
            logger.warning("Could not read machine_version from %s: %s; defaulting to 0",
                           self._path, exc)
            return UNSET_MACHINE_VERSION

    def get(self) -> int:
        with self._lock:
            return self._value

    def set(self, value: object) -> int:
        """Validate, store, and persist a new machine_version. Returns the stored int."""
        validated = validate_machine_version(value)
        with self._lock:
            self._value = validated
            self._persist(validated)
        logger.info("machine_version set to %s", validated)
        return validated

    def _persist(self, value: int) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"machine_version": value}), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not persist machine_version to %s: %s", self._path, exc)
