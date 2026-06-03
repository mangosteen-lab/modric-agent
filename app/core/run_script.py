import os
import subprocess
import sys
import tempfile
import threading

from app.core.command import EScriptType

EXT_MAP = {
    EScriptType.BAT:        ".bat",
    EScriptType.PYTHON:     ".py",
    EScriptType.SHELL:      ".sh",
    EScriptType.POWERSHELL: ".ps1",
}

CMD_MAP = {
    EScriptType.BAT:        lambda f: [f],
    EScriptType.PYTHON:     lambda f: [sys.executable, f],
    EScriptType.SHELL:      lambda f: ["bash", f],
    EScriptType.POWERSHELL: lambda f: ["powershell", "-ExecutionPolicy", "Bypass", "-File", f],
}


class CRunScriptCommand:

    def __init__(self, command_id: str, script_type: int,
                 script_content: str, args: list, timeout: int,
                 log_callback):
        self.command_id     = command_id
        self.script_type    = EScriptType(script_type)
        self.script_content = script_content
        self.args           = args
        self.timeout        = timeout
        self._log_callback  = log_callback
        self._process: subprocess.Popen | None = None
        self._killed        = False

    def run(self) -> int:
        ext = EXT_MAP[self.script_type]
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                         mode="w", encoding="utf-8") as f:
            f.write(self.script_content)
            script_path = f.name

        try:
            cmd = CMD_MAP[self.script_type](script_path) + list(self.args)
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            offset = 0

            def stream_output():
                nonlocal offset
                if not self._process or not self._process.stdout:
                    return
                for line in self._process.stdout:
                    self._log_callback(line, offset)
                    offset += len(line.encode("utf-8"))

            stream_thread = threading.Thread(target=stream_output, daemon=True)
            stream_thread.start()
            try:
                self._process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.kill()
                self._process.wait()
            stream_thread.join(timeout=1)
            return self._process.returncode if not self._killed else -1
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def kill(self):
        self._killed = True
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass
