import logging
import logging.handlers
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_CONFIGURED = False


def read_log_tail(path: str | Path, max_bytes: int) -> tuple[str, int, bool]:
    """Read up to the last `max_bytes` of a log file.

    Returns (text, total_size, truncated). Missing file -> ("", 0, False). Used to
    serve a machine's agent log to Toil without shipping an unbounded amount.
    """
    p = Path(path)
    if not p.exists():
        return "", 0, False
    size = p.stat().st_size
    with p.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            truncated = True
        else:
            truncated = False
        data = f.read()
    return data.decode("utf-8", errors="replace"), size, truncated


def configure_logging(log_file: str | Path = "logs/agent.log", level: str = "INFO") -> Path:
    """Send agent logs to a rotating file (and the console). Default level INFO. Idempotent."""
    global _CONFIGURED
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    fmt = logging.Formatter(_FORMAT)

    root = logging.getLogger()
    root.setLevel(lvl)

    if not _CONFIGURED:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(file_handler)
        root.addHandler(console)
        _CONFIGURED = True

    return log_path
