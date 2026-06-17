@echo off
REM Launch the Modric Agent inside the interactive desktop (console) session so
REM steps that open a GUI -- e.g. a Java UI app -- have a real desktop. A Windows
REM service / Scheduled Task runs in the non-interactive Session 0 (no desktop),
REM which makes such steps fail; running here from the logged-in console fixes it.
REM
REM `make install-interactive` drops a Startup launcher that calls this file by its
REM absolute path; %~dp0 still resolves to this (agent) directory, so it works both
REM in place and from Startup. Pair with Autologon so the box logs into the console
REM unattended on boot.
REM This file is also the agent's supervisor: self-upgrade exits with code 75
REM after launching a detached installer, and a crash exits non-zero -- in both
REM cases the loop below relaunches it (the role systemd Restart=always / the
REM scheduled task's post-upgrade restart play in the other launch modes).
cd /d "%~dp0"
if not defined MODRIC_AGENT_CONFIG set "MODRIC_AGENT_CONFIG=%~dp0conf\config.ini"

:loop
uv run python -m app.main
set "rc=%ERRORLEVEL%"
if "%rc%"=="75" (
    REM Self-upgrade: the detached installer is pip-installing the new wheel now.
    REM Wait for it to finish before relaunching, so we don't load half-installed
    REM code or hit a Windows file lock, then loop into the new version.
    timeout /t 30 /nobreak >nul
) else (
    REM Crash / normal exit: brief pause, then relaunch (mirrors Restart=always).
    timeout /t 5 /nobreak >nul
)
goto loop
