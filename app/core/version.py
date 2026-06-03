from importlib import metadata

PACKAGE_NAME = "modric-agent"
DEFAULT_VERSION = "0.0.0"


def get_agent_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return DEFAULT_VERSION


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
