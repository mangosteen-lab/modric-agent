import threading
from typing import Callable

from app.core.run_script import CRunScriptCommand


class CCommandMgr:

    def __init__(self, capacity: int = 10):
        self._capacity = capacity
        self._lock     = threading.Lock()
        self._slots    = threading.BoundedSemaphore(value=capacity)
        self._commands: dict[str, CRunScriptCommand] = {}

    def run_and_stream(self, command_id: str, script_type: int,
                       script_content: str, args: list, timeout: int,
                       log_callback: Callable) -> int:
        self._slots.acquire()
        cmd = CRunScriptCommand(
            command_id=command_id,
            script_type=script_type,
            script_content=script_content,
            args=args,
            timeout=timeout,
            log_callback=log_callback,
        )
        with self._lock:
            self._commands[command_id] = cmd
        try:
            return cmd.run()
        finally:
            with self._lock:
                self._commands.pop(command_id, None)
            self._slots.release()

    def kill(self, command_id: str):
        with self._lock:
            cmd = self._commands.get(command_id)
        if cmd:
            cmd.kill()
