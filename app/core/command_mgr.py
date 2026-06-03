import threading
from typing import Callable

from app.core.run_script import CRunScriptCommand


class CommandRejectedError(RuntimeError):
    pass


class CCommandMgr:

    def __init__(self, capacity: int = 10):
        self._capacity = capacity
        self._lock     = threading.Lock()
        self._idle     = threading.Condition(self._lock)
        self._slots    = threading.BoundedSemaphore(value=capacity)
        self._commands: dict[str, CRunScriptCommand] = {}
        self._inflight = 0
        self._accepting = True

    def run_and_stream(self, command_id: str, script_type: int,
                       script_content: str, args: list, timeout: int,
                       log_callback: Callable,
                       started_callback: Callable | None = None) -> int:
        with self._idle:
            if not self._accepting:
                raise CommandRejectedError("agent is draining")
            self._inflight += 1

        acquired = False
        try:
            self._slots.acquire()
            acquired = True
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
            if started_callback:
                started_callback()
            return cmd.run()
        finally:
            with self._idle:
                self._commands.pop(command_id, None)
                self._inflight -= 1
                if self._inflight == 0:
                    self._idle.notify_all()
            if acquired:
                self._slots.release()

    def begin_drain(self):
        with self._idle:
            self._accepting = False

    def resume(self):
        with self._idle:
            self._accepting = True

    def is_accepting(self) -> bool:
        with self._lock:
            return self._accepting

    def active_count(self) -> int:
        with self._lock:
            return self._inflight

    def is_idle(self) -> bool:
        return self.active_count() == 0

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._inflight == 0, timeout=timeout)

    def kill(self, command_id: str):
        with self._lock:
            cmd = self._commands.get(command_id)
        if cmd:
            cmd.kill()
