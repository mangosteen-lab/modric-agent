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
cd /d "%~dp0"
if not defined MODRIC_AGENT_CONFIG set "MODRIC_AGENT_CONFIG=%~dp0conf\config.ini"
uv run python -m app.main
