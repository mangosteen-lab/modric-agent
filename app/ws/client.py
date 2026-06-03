import asyncio
import json
import logging
import platform
import socket
import time

import psutil
import websockets

from app.core.command_mgr import CCommandMgr

logger = logging.getLogger("modric_agent.ws")

RECONNECT_BASE = 1.0
RECONNECT_MAX  = 60.0


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
                 name: str, capacity: int, version: int,
                 command_mgr: CCommandMgr | None = None):
        self.wss_url  = wss_url
        self.api_key  = api_key
        self.name     = name
        self.capacity = capacity
        self.version  = version
        self.machine_id: str | None    = None
        self.session_token: str | None = None
        self._cmd_mgr = command_mgr or CCommandMgr(capacity=capacity)

    async def run(self):
        """Outer reconnect loop. Never returns."""
        delay = RECONNECT_BASE
        while True:
            try:
                async with websockets.connect(self.wss_url) as ws:
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
            "type":     "REGISTER",
            "version":  self.version,
            "name":     self.name,
            "capacity": self.capacity,
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

            elif msg_type == "PING":
                sysinfo = _collect_sysinfo()
                await ws.send(json.dumps({
                    "type":       "PONG",
                    "ts":         time.time(),
                    "machine_id": self.machine_id,
                    **sysinfo,
                }))

            elif msg_type == "EXECUTE":
                asyncio.create_task(self._handle_execute(ws, msg))

            elif msg_type == "KILL":
                command_id = msg.get("command_id")
                if command_id:
                    self._cmd_mgr.kill(command_id)

            elif msg_type == "UPGRADE_REQUIRED":
                logger.error("Toil requires min version %s. Please upgrade Modric Agent.",
                             msg.get("min_version"))
                return

            elif msg_type == "ERROR":
                logger.error("Toil error: %s", msg.get("message"))

    async def _handle_execute(self, ws, msg: dict):
        command_id     = msg["command_id"]
        script_type    = msg["script_type"]
        script_content = msg["script_content"]
        args           = msg.get("args", [])
        timeout        = msg.get("timeout", 600)

        await ws.send(json.dumps({
            "type":       "COMMAND_STARTED",
            "command_id": command_id,
            "machine_id": self.machine_id,
        }))

        loop = asyncio.get_event_loop()
        exit_code = await loop.run_in_executor(
            None,
            self._run_and_stream_sync,
            ws, loop, command_id, script_type, script_content, args, timeout,
        )

        await ws.send(json.dumps({
            "type":       "COMMAND_DONE",
            "command_id": command_id,
            "machine_id": self.machine_id,
            "exit_code":  exit_code,
            "status":     "COMPLETED" if exit_code == 0 else "FAILED",
        }))

    def _run_and_stream_sync(self, ws, loop, command_id, script_type,
                              script_content, args, timeout):
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
        )
