import asyncio
import json
import logging
import platform
import socket
import time
from collections.abc import Callable

import psutil
import websockets

from app.core.command_mgr import CCommandMgr, CommandRejectedError
from app.core.updater import RESTART_EXIT_CODE, CUpgradeManager, CUpgradeRequest
from app.core.version import get_agent_version, version_to_code

logger = logging.getLogger("modric_agent.ws")

RECONNECT_BASE = 1.0
RECONNECT_MAX  = 60.0


def _raise_system_exit(code: int):
    raise SystemExit(code)


def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def _collect_sysinfo() -> dict:
    system = platform.system()
    disk_path = "C:\\" if system == "Windows" else "/"
    try:
        disk_pct = psutil.disk_usage(disk_path).percent
    except Exception:
        disk_pct = None
    return {
        "hostname":       socket.gethostname(),
        "os":             system.lower(),
        "ip":             _get_ip(),
        "cpu_percent":    psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent":   disk_pct,
    }


class SoilWSClient:

    def __init__(self, wss_url: str, api_key: str,
                 name: str, capacity: int, version: str | int | None = None,
                 auto_upgrade: bool = True, upgrade_channel: str = "stable",
                 labels: dict[str, str] | None = None,
                 command_mgr: CCommandMgr | None = None,
                 upgrade_mgr: CUpgradeManager | None = None,
                 exit_func: Callable[[int], None] | None = None):
        self.wss_url  = wss_url
        self.api_key  = api_key
        self.name     = name
        self.capacity = capacity
        self.labels   = labels or {}
        raw_version = version if version is not None else get_agent_version()
        self.version  = str(raw_version)
        self.version_code = version_to_code(raw_version)
        self.auto_upgrade = auto_upgrade
        self.upgrade_channel = upgrade_channel
        self.machine_id: str | None    = None
        self.session_token: str | None = None
        self._cmd_mgr = command_mgr or CCommandMgr(capacity=capacity)
        self._upgrade_mgr = upgrade_mgr or CUpgradeManager(enabled=auto_upgrade)
        self._exit_func = exit_func or _raise_system_exit
        self._upgrade_task: asyncio.Task | None = None
        # COMMAND_DONE results that couldn't be sent because the socket dropped;
        # re-delivered after the next successful REGISTER.
        self._pending_results: dict[str, dict] = {}

    async def run(self):
        """Outer reconnect loop. Never returns."""
        delay = RECONNECT_BASE
        while True:
            try:
                # ping_timeout is generous so a transient backlog (heavy build
                # output / server persistence) doesn't drop the connection.
                async with websockets.connect(
                    self.wss_url,
                    ping_interval=20,
                    ping_timeout=60,
                    max_queue=256,
                ) as ws:
                    logger.info("Connected to Toil at %s", self.wss_url)
                    delay = RECONNECT_BASE
                    await self._do_session(ws)
            except Exception as exc:
                logger.warning("Disconnected: %s. Reconnecting in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)

    async def _do_session(self, ws):
        sysinfo = _collect_sysinfo()
        msg: dict = {
            "type":            "REGISTER",
            "version":         self.version,
            "version_code":    self.version_code,
            "auto_upgrade":    self.auto_upgrade,
            "upgrade_channel": self.upgrade_channel,
            "name":            self.name,
            "capacity":        self.capacity,
            "labels":          self.labels,
            **sysinfo,
        }
        if self.session_token:
            msg["session_token"] = self.session_token
        else:
            msg["api_key"] = self.api_key

        await ws.send(json.dumps(msg))

        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "REGISTERED":
                self.machine_id    = msg["machine_id"]
                self.session_token = msg.get("session_token") or None
                logger.info("Registered as machine_id=%s", self.machine_id)
                if self._pending_results:
                    await self._flush_pending_results(ws)

            elif msg_type == "PING":
                sysinfo = _collect_sysinfo()
                await ws.send(json.dumps({
                    "type":       "PONG",
                    "ts":         time.time(),
                    "machine_id": self.machine_id,
                    **sysinfo,
                }))

            elif msg_type == "EXECUTE":
                if not self._cmd_mgr.is_accepting():
                    await self._send_command_rejected(ws, msg, "agent is upgrading")
                else:
                    asyncio.create_task(self._handle_execute(ws, msg))

            elif msg_type == "KILL":
                command_id = msg.get("command_id")
                if command_id:
                    self._cmd_mgr.kill(command_id)

            elif msg_type == "UPGRADE_REQUIRED":
                if msg.get("url") or msg.get("artifact_url"):
                    await self._schedule_upgrade(ws, msg)
                else:
                    logger.error("Toil requires min version %s. Please upgrade Modric Agent.",
                                 msg.get("min_version"))
                    return

            elif msg_type == "UPGRADE_AVAILABLE":
                await self._schedule_upgrade(ws, msg)

            elif msg_type == "ERROR":
                logger.error("Toil error: %s", msg.get("message"))

    async def _handle_execute(self, ws, msg: dict):
        command_id     = msg["command_id"]
        script_type    = msg["script_type"]
        script_content = msg["script_content"]
        args           = msg.get("args", [])
        timeout        = msg.get("timeout", 600)

        try:
            loop = asyncio.get_event_loop()
            exit_code = await loop.run_in_executor(
                None,
                self._run_and_stream_sync,
                ws, loop, command_id, script_type, script_content, args, timeout,
            )
        except CommandRejectedError as exc:
            await self._send_command_rejected(ws, msg, str(exc))
            return
        except Exception:
            logger.exception("Command %s crashed in the agent", command_id)
            return

        result = {
            "type":       "COMMAND_DONE",
            "command_id": command_id,
            "machine_id": self.machine_id,
            "exit_code":  exit_code,
            "status":     "COMPLETED" if exit_code == 0 else "FAILED",
        }
        try:
            await ws.send(json.dumps(result))
        except websockets.ConnectionClosed:
            # Connection dropped while the command ran. Buffer the result and
            # re-deliver it after the next reconnect (the server keeps the command
            # pending across reconnects), so the step's outcome isn't lost.
            self._pending_results[command_id] = result
            logger.warning(
                "Connection closed before COMMAND_DONE for %s (exit=%s); "
                "buffered for re-delivery on reconnect", command_id, exit_code,
            )

    async def _flush_pending_results(self, ws):
        """Re-deliver COMMAND_DONE results buffered while disconnected."""
        for command_id, result in list(self._pending_results.items()):
            result["machine_id"] = self.machine_id
            try:
                await ws.send(json.dumps(result))
            except websockets.ConnectionClosed:
                return  # try again on the next reconnect
            self._pending_results.pop(command_id, None)
            logger.info("Re-delivered result for command %s after reconnect", command_id)

    def _run_and_stream_sync(self, ws, loop, command_id, script_type,
                              script_content, args, timeout):
        def started_callback():
            future = asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({
                    "type":       "COMMAND_STARTED",
                    "command_id": command_id,
                    "machine_id": self.machine_id,
                })), loop
            )
            future.result(timeout=10)

        def log_callback(chunk: str, offset: int):
            asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({
                    "type":       "LOG_CHUNK",
                    "command_id": command_id,
                    "machine_id": self.machine_id,
                    "data":       chunk,
                    "offset":     offset,
                })), loop
            )

        return self._cmd_mgr.run_and_stream(
            command_id=command_id,
            script_type=script_type,
            script_content=script_content,
            args=args,
            timeout=timeout,
            log_callback=log_callback,
            started_callback=started_callback,
        )

    async def _send_command_rejected(self, ws, msg: dict, reason: str):
        await ws.send(json.dumps({
            "type":       "COMMAND_REJECTED",
            "command_id": msg.get("command_id"),
            "machine_id": self.machine_id,
            "reason":     reason,
        }))

    async def _schedule_upgrade(self, ws, msg: dict):
        if not self.auto_upgrade:
            await ws.send(json.dumps({
                "type":       "UPGRADE_SKIPPED",
                "machine_id": self.machine_id,
                "reason":     "auto upgrade is disabled",
            }))
            return

        if self._upgrade_task and not self._upgrade_task.done():
            await ws.send(json.dumps({
                "type":       "UPGRADE_PENDING",
                "machine_id": self.machine_id,
            }))
            return

        request = CUpgradeRequest.from_message(msg)
        self._cmd_mgr.begin_drain()
        self._upgrade_task = asyncio.create_task(self._upgrade_when_idle(ws, request))

    async def _upgrade_when_idle(self, ws, request: CUpgradeRequest):
        active_count = self._cmd_mgr.active_count()
        if active_count:
            await ws.send(json.dumps({
                "type":         "UPGRADE_DEFERRED",
                "machine_id":   self.machine_id,
                "target_version": request.version,
                "active_count": active_count,
            }))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._cmd_mgr.wait_until_idle)

        try:
            await ws.send(json.dumps({
                "type":           "UPGRADE_STARTED",
                "machine_id":     self.machine_id,
                "target_version": request.version,
            }))
            await loop.run_in_executor(None, self._upgrade_mgr.stage_and_launch, request)
            await ws.send(json.dumps({
                "type":           "UPGRADE_RESTARTING",
                "machine_id":     self.machine_id,
                "target_version": request.version,
            }))
            self._exit_func(RESTART_EXIT_CODE)
        except Exception as exc:
            self._cmd_mgr.resume()
            logger.error("Auto upgrade failed: %s", exc)
            await ws.send(json.dumps({
                "type":           "UPGRADE_FAILED",
                "machine_id":     self.machine_id,
                "target_version": request.version,
                "reason":         str(exc),
            }))
