import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import psutil

logger = logging.getLogger("modric_agent.updater")

RESTART_EXIT_CODE = 75

# Agent repo root: app/core/updater.py -> repo root.
_AGENT_ROOT = Path(__file__).resolve().parents[2]

# Suffixes we treat as a source archive (extract in place) rather than a wheel.
_SOURCE_SUFFIXES = (".tar.gz", ".tgz", ".zip")

# Code paths copied out of a source archive into the agent root. Everything else in
# the agent dir — conf/config.ini, .venv/, logs/, state/ — is left untouched so a
# source upgrade never disturbs local config or the venv.
_SOURCE_PAYLOAD = ("app", "pyproject.toml", "uv.lock", "StartModricAgent.bat",
                   "conf/config.example.ini")


class UpgradeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CUpgradeRequest:
    version: str
    url: str
    sha256: str | None = None
    force: bool = False

    @classmethod
    def from_message(cls, msg: dict):
        version = str(msg.get("version") or msg.get("latest_version") or "")
        url = str(msg.get("url") or msg.get("artifact_url") or "")
        sha256 = msg.get("sha256")
        return cls(
            version=version,
            url=url,
            sha256=str(sha256).lower() if sha256 else None,
            force=bool(msg.get("force", False)),
        )


class CUpgradeManager:
    def __init__(self, enabled: bool = True, artifact_dir: str | None = None):
        self.enabled = enabled
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    def _check_ready(self, request: CUpgradeRequest) -> None:
        # A manual "Upgrade agent" (request.force) overrides the auto_upgrade gate.
        if not self.enabled and not request.force:
            raise UpgradeError("auto upgrade is disabled")
        if not request.version:
            raise UpgradeError("upgrade version is missing")

    def stage_and_launch(self, request: CUpgradeRequest) -> Path:
        self._check_ready(request)
        artifact_path = self._download(request.url)
        if request.sha256:
            self._verify_sha256(artifact_path, request.sha256)
        try:
            self._launch_installer(artifact_path, request.version)
        except Exception as exc:
            raise UpgradeError(f"failed to launch upgrade installer: {exc}") from exc
        return artifact_path

    def stage_and_apply_source(self, request: CUpgradeRequest) -> Path:
        """Download a source archive and apply it **in-process**, so the new files are
        on disk before the agent exits to be relaunched. Unlike the wheel path (which
        must pip-install after the old process dies, via a detached installer), this
        avoids the race where a supervisor restarts the agent — on old files — before a
        detached installer finishes extracting + `uv sync`."""
        self._check_ready(request)
        artifact_path = self._download(request.url)
        if request.sha256:
            self._verify_sha256(artifact_path, request.sha256)
        _apply_source_archive(artifact_path, _AGENT_ROOT)
        return artifact_path

    def _download(self, url: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise UpgradeError("upgrade artifact must use https")

        suffix = Path(parsed.path).suffix or ".whl"
        artifact_dir = self.artifact_dir or Path(tempfile.mkdtemp(prefix="modric-agent-upgrade-"))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"modric-agent-upgrade{suffix}"

        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                with artifact_path.open("wb") as artifact_file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        artifact_file.write(chunk)
        except Exception as exc:
            raise UpgradeError(f"failed to download upgrade artifact: {exc}") from exc

        return artifact_path

    def _verify_sha256(self, artifact_path: Path, expected_sha256: str):
        digest = hashlib.sha256()
        with artifact_path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)

        actual_sha256 = digest.hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            raise UpgradeError("upgrade artifact sha256 mismatch")

    def _launch_installer(self, artifact_path: Path, target_version: str):
        cmd = [
            sys.executable,
            "-m",
            "app.core.updater",
            "--artifact",
            str(artifact_path),
            "--pid",
            str(os.getpid()),
            "--target-version",
            target_version,
        ]
        # The installer is detached and outlives us, so its output can't go to the
        # agent's own stdout. Send it to logs/upgrade.log (not /dev/null) so a failed
        # pip install / uv sync is diagnosable instead of silently swallowed.
        log_path = _AGENT_ROOT / "logs" / "upgrade.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            out = open(log_path, "a", encoding="utf-8")
        except Exception:
            out = subprocess.DEVNULL
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                close_fds=os.name != "nt",
            )
        finally:
            if out is not subprocess.DEVNULL:
                out.close()


def is_source_artifact_url(url: str) -> bool:
    """True if the upgrade URL points at a source archive (applied in-process) rather
    than a wheel (installed via the detached installer)."""
    path = urllib.parse.urlparse(url or "").path.lower()
    return any(path.endswith(suffix) for suffix in _SOURCE_SUFFIXES)


def _wait_for_process_exit(pid: int, timeout: float = 120.0):
    if pid <= 0:
        return

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    try:
        proc.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        raise UpgradeError(f"agent process {pid} did not exit within {timeout:.0f}s")


def _is_source_archive(artifact_path: Path) -> bool:
    name = artifact_path.name.lower()
    return any(name.endswith(suffix) for suffix in _SOURCE_SUFFIXES)


def _install_artifact(artifact_path: Path, agent_root: Path | None = None):
    if not artifact_path.exists():
        raise UpgradeError(f"upgrade artifact not found: {artifact_path}")

    if _is_source_archive(artifact_path):
        _apply_source_archive(artifact_path, agent_root or _AGENT_ROOT)
        return

    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", str(artifact_path)]
    subprocess.check_call(cmd)


def _extract_archive(artifact_path: Path, dest: Path) -> None:
    name = artifact_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(artifact_path) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(artifact_path) as tf:
            tf.extractall(dest, filter="data")


def _archive_source_root(extract_dir: Path) -> Path:
    """The directory holding the code inside an extracted archive. GitHub archives
    nest everything under a single top-level dir (e.g. modric-agent-<ref>/); a flat
    archive extracts the payload directly. Detect by looking for the `app` package."""
    if (extract_dir / "app").is_dir():
        return extract_dir
    children = [c for c in extract_dir.iterdir() if c.is_dir()]
    if len(children) == 1 and (children[0] / "app").is_dir():
        return children[0]
    for child in children:
        if (child / "app").is_dir():
            return child
    raise UpgradeError("source archive does not contain an 'app' package")


def _apply_source_archive(artifact_path: Path, agent_root: Path) -> None:
    """Extract a source tarball/zip and copy the code payload over the agent root,
    preserving local state (conf/config.ini, .venv, logs, state). Then `uv sync` so
    the venv matches the new pyproject before the supervisor relaunches the agent."""
    with tempfile.TemporaryDirectory(prefix="modric-agent-src-") as tmp:
        extract_dir = Path(tmp)
        _extract_archive(artifact_path, extract_dir)
        src_root = _archive_source_root(extract_dir)

        for rel in _SOURCE_PAYLOAD:
            src = src_root / rel
            if not src.exists():
                continue  # optional payload item absent in this archive
            dst = agent_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    logger.info("Applied source upgrade into %s", agent_root)
    try:
        subprocess.check_call(["uv", "sync"], cwd=str(agent_root))
    except Exception as exc:
        # A failed sync isn't fatal here — StartModricAgent.bat runs `uv run` which
        # re-syncs on launch. Log and continue to the restart.
        logger.warning("uv sync after source upgrade failed (will retry on launch): %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--target-version", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        _wait_for_process_exit(args.pid)
        # Give process managers a small window to observe the old process exit.
        time.sleep(0.5)
        _install_artifact(Path(args.artifact))
        logger.info("Installed Modric Agent %s", args.target_version)
        # Restart the agent so the new code is loaded. No-op if it isn't managed as a
        # service (then the supervisor / container restart policy relaunches it).
        try:
            from app.service import restart_after_upgrade
            restart_after_upgrade()
        except Exception as exc:
            logger.warning("Post-upgrade restart skipped: %s", exc)
        return 0
    except Exception as exc:
        logger.error("Upgrade failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
