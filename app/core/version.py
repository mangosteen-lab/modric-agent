import os
import subprocess
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "modric-agent"
DEFAULT_VERSION = "0.0.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]  # app/core/version.py -> repo root


def get_agent_commit() -> str:
    """Short git commit the agent is running, for display in Toil's machines view.

    Resolution order: the MODRIC_AGENT_COMMIT env var (e.g. baked in at build time),
    then `git rev-parse --short HEAD` when running from a checkout. Empty string if
    neither is available (e.g. a pip-installed wheel with no git)."""
    env = os.getenv("MODRIC_AGENT_COMMIT")
    if env:
        return env.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def get_agent_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        # Running from source (not pip-installed) — read version from pyproject.toml
        return _version_from_pyproject() or DEFAULT_VERSION


def _version_from_pyproject() -> str | None:
    try:
        import tomllib  # stdlib in Python 3.11+
        root = Path(__file__).resolve().parents[2]  # app/core/version.py -> repo root
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return data.get("project", {}).get("version")
    except Exception:
        return None


def version_to_code(version: str | int) -> int:
    if isinstance(version, int):
        return version

    parts = str(version).split("+", maxsplit=1)[0].split("-", maxsplit=1)[0].split(".")
    numeric_parts: list[int] = []
    for part in parts[:3]:
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        numeric_parts.append(int(digits or "0"))

    while len(numeric_parts) < 3:
        numeric_parts.append(0)

    major, minor, patch = numeric_parts
    return major * 1_000_000 + minor * 1_000 + patch
