"""Install/manage the Modric Agent as an OS service so it starts on boot and is
restarted if it exits (including self-upgrade exit code 75).

  python -m app.main service install     # register + start
  python -m app.main service install-interactive  # Windows: run in the console desktop (GUI steps)
  python -m app.main service uninstall
  python -m app.main service start|stop|status

- Linux  : systemd unit at /etc/systemd/system/modric-agent.service (needs root).
- macOS  : launchd plist (root -> /Library/LaunchDaemons, else ~/Library/LaunchAgents).
- Windows: a Scheduled Task that runs at startup (run from an elevated prompt).

The service runs `<this-python> -m app.main` from the agent directory, with
MODRIC_AGENT_CONFIG pointing at the resolved config file.
"""

import getpass
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("modric_agent.service")

SERVICE_NAME = "modric-agent"
LAUNCHD_LABEL = "com.microstrategy.modric-agent"
ROOT = Path(__file__).resolve().parents[1]


def _python() -> str:
    return sys.executable or "python3"


def _config_path() -> str:
    return os.getenv("MODRIC_AGENT_CONFIG") or str(ROOT / "conf" / "config.ini")


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check)


def _write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except PermissionError as exc:
        raise SystemExit(
            f"Permission denied writing {path}. Re-run with sudo / from an "
            f"Administrator prompt."
        ) from exc
    print(f"wrote {path}")


def _impl():
    return {"Linux": _Systemd, "Darwin": _Launchd, "Windows": _Windows}.get(platform.system())


def dispatch(action: str) -> None:
    impl = _impl()
    if impl is None:
        raise SystemExit(f"Unsupported OS for service install: {platform.system()}")
    method = getattr(impl(), action.replace("-", "_"), None)
    if method is None:
        raise SystemExit(f"'{action}' is not supported on {platform.system()}")
    method()


def restart_after_upgrade() -> None:
    """Best-effort: if the agent is managed as an OS service, restart it so the freshly
    installed code is loaded. Called by the upgrade installer after pip-installing the
    new wheel (the supervisor's auto-restart is a fallback if this fails)."""
    impl = _impl()
    if impl is None:
        return
    inst = impl()
    try:
        if not inst.installed():
            logger.info("Agent is not installed as a service; supervisor (if any) will relaunch")
            return
        inst.restart()
        logger.info("Restarted the agent service with the upgraded code")
    except Exception as exc:
        logger.warning("Post-upgrade service restart failed: %s", exc)


# ---------------------------------------------------------------------------
# Linux — systemd
# ---------------------------------------------------------------------------

class _Systemd:
    unit = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")

    def install(self) -> None:
        user = os.getenv("SUDO_USER") or getpass.getuser()
        _write(self.unit, f"""[Unit]
Description=Modric Agent (Soil worker)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={ROOT}
Environment=MODRIC_AGENT_CONFIG={_config_path()}
ExecStart={_python()} -m app.main
Restart=always
RestartSec=5
# Self-upgrade exits 75 after launching a detached installer. KillMode=process lets that
# installer survive the main process exiting (default control-group would kill it); the
# installer then pip-installs and restarts this unit. Restart=always is the crash fallback.
KillMode=process

[Install]
WantedBy=multi-user.target
""")
        _run(["systemctl", "daemon-reload"], check=True)
        _run(["systemctl", "enable", "--now", SERVICE_NAME], check=True)
        print(f"Installed and started '{SERVICE_NAME}'. "
              f"Logs: journalctl -u {SERVICE_NAME} -f  (and {ROOT}/logs/agent.log)")

    def uninstall(self) -> None:
        _run(["systemctl", "disable", "--now", SERVICE_NAME])
        try:
            self.unit.unlink()
            print(f"removed {self.unit}")
        except FileNotFoundError:
            pass
        _run(["systemctl", "daemon-reload"])

    def start(self) -> None:
        _run(["systemctl", "start", SERVICE_NAME], check=True)

    def stop(self) -> None:
        _run(["systemctl", "stop", SERVICE_NAME], check=True)

    def status(self) -> None:
        _run(["systemctl", "status", SERVICE_NAME])

    def restart(self) -> None:
        _run(["systemctl", "restart", SERVICE_NAME], check=True)

    def installed(self) -> bool:
        return self.unit.exists()


# ---------------------------------------------------------------------------
# macOS — launchd
# ---------------------------------------------------------------------------

class _Launchd:
    @property
    def plist(self) -> Path:
        if os.geteuid() == 0:
            return Path("/Library/LaunchDaemons") / f"{LAUNCHD_LABEL}.plist"
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

    def install(self) -> None:
        logs = ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        _write(self.plist, f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{_python()}</string>
    <string>-m</string>
    <string>app.main</string>
  </array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict><key>MODRIC_AGENT_CONFIG</key><string>{_config_path()}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs / 'agent.out.log'}</string>
  <key>StandardErrorPath</key><string>{logs / 'agent.err.log'}</string>
</dict>
</plist>
""")
        _run(["launchctl", "unload", str(self.plist)])  # ignore if not loaded
        _run(["launchctl", "load", "-w", str(self.plist)], check=True)
        print(f"Installed and loaded '{LAUNCHD_LABEL}'. Logs: {ROOT}/logs/agent.log")

    def uninstall(self) -> None:
        _run(["launchctl", "unload", "-w", str(self.plist)])
        try:
            self.plist.unlink()
            print(f"removed {self.plist}")
        except FileNotFoundError:
            pass

    def start(self) -> None:
        _run(["launchctl", "load", "-w", str(self.plist)], check=True)

    def stop(self) -> None:
        _run(["launchctl", "unload", str(self.plist)], check=True)

    def status(self) -> None:
        _run(["launchctl", "list", LAUNCHD_LABEL])

    def _target(self) -> str:
        if os.geteuid() == 0:
            return f"system/{LAUNCHD_LABEL}"
        return f"gui/{os.getuid()}/{LAUNCHD_LABEL}"

    def restart(self) -> None:
        # kickstart -k force-restarts (kills the current instance and starts fresh),
        # so the upgraded code is loaded even if KeepAlive already relaunched the old one.
        _run(["launchctl", "kickstart", "-k", self._target()], check=True)

    def installed(self) -> bool:
        return self.plist.exists()


# ---------------------------------------------------------------------------
# Windows — Scheduled Task (run at startup, dependency-free)
# ---------------------------------------------------------------------------

class _Windows:
    def _command(self) -> str:
        # pythonw avoids a console window for the long-running process.
        py = _python()
        pyw = py.replace("python.exe", "pythonw.exe")
        if not Path(pyw).exists():
            pyw = py
        return (f'cmd /c "cd /d \"{ROOT}\" && set MODRIC_AGENT_CONFIG={_config_path()} '
                f'&& \"{pyw}\" -m app.main"')

    def install(self) -> None:
        _run(["schtasks", "/create", "/tn", SERVICE_NAME, "/tr", self._command(),
              "/sc", "onstart", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"], check=True)
        _run(["schtasks", "/run", "/tn", SERVICE_NAME])
        print(f"Installed scheduled task '{SERVICE_NAME}' (runs at startup). "
              f"Logs: {ROOT}\\logs\\agent.log\n"
              "For auto-restart on crash, run it under NSSM instead.")

    def uninstall(self) -> None:
        _run(["schtasks", "/end", "/tn", SERVICE_NAME])
        _run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"])

    def install_interactive(self) -> None:
        # A Scheduled Task / Windows service runs in the non-interactive Session 0,
        # which has no desktop — so steps that open a GUI (e.g. a Java UI app) fail.
        # Instead, run the agent inside a real logged-in *console* desktop: remove the
        # Session-0 task, then drop a launcher in the current user's Startup folder that
        # runs StartModricAgent.bat on logon. Pair with Autologon so the box logs into
        # the console unattended on boot (see the printed steps below).
        if self.installed():
            self.uninstall()
        startup = (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows"
                   / "Start Menu" / "Programs" / "Startup")
        launcher = ROOT / "StartModricAgent.bat"
        if not launcher.exists():
            raise SystemExit(f"Missing {launcher} — expected it in the agent directory.")
        # %~dp0 inside StartModricAgent.bat resolves to its own (agent) directory even
        # when invoked from Startup, so the stub just calls it by absolute path.
        _write(startup / "StartModricAgent.bat",
               f'@echo off\r\ncall "{launcher}"\r\n')
        print(
            "\nInstalled the interactive Startup launcher. To make it fully unattended:\n"
            "  1. Configure Autologon for the worker account so the box logs into the\n"
            "     CONSOLE desktop on boot (Sysinternals Autologon64.exe).\n"
            "  2. Reboot. The agent starts in the console desktop on logon and GUI\n"
            "     steps get a real desktop.\n"
            "  3. Do NOT RDP into that same account afterwards — it steals the desktop\n"
            "     from the console. If you must, run `tscon <id> /dest:console` before\n"
            "     disconnecting to hand the desktop back."
        )

    def start(self) -> None:
        _run(["schtasks", "/run", "/tn", SERVICE_NAME], check=True)

    def stop(self) -> None:
        _run(["schtasks", "/end", "/tn", SERVICE_NAME], check=True)

    def status(self) -> None:
        _run(["schtasks", "/query", "/tn", SERVICE_NAME, "/v", "/fo", "LIST"])

    def restart(self) -> None:
        # The onstart task won't relaunch on exit, so the installer drives the restart.
        _run(["schtasks", "/end", "/tn", SERVICE_NAME])
        _run(["schtasks", "/run", "/tn", SERVICE_NAME], check=True)

    def installed(self) -> bool:
        return subprocess.run(
            ["schtasks", "/query", "/tn", SERVICE_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
