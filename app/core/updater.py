import argparse
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psutil

logger = logging.getLogger("modric_agent.updater")

RESTART_EXIT_CODE = 75


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

    def stage_and_launch(self, request: CUpgradeRequest) -> Path:
        if not self.enabled:
            raise UpgradeError("auto upgrade is disabled")
        if not request.version:
            raise UpgradeError("upgrade version is missing")

        artifact_path = self._download(request.url)
        if request.sha256:
            self._verify_sha256(artifact_path, request.sha256)
        try:
            self._launch_installer(artifact_path, request.version)
        except Exception as exc:
            raise UpgradeError(f"failed to launch upgrade installer: {exc}") from exc
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
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
        )


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


def _install_artifact(artifact_path: Path):
    if not artifact_path.exists():
        raise UpgradeError(f"upgrade artifact not found: {artifact_path}")

    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", str(artifact_path)]
    subprocess.check_call(cmd)


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
        return 0
    except Exception as exc:
        logger.error("Upgrade failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
