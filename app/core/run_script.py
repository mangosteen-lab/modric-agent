import os
import subprocess
import sys
import tempfile
import threading
import time

from app.core.command import EScriptType

# Coalesce output before sending: a chatty build emits thousands of lines; one
# ws.send per line floods the connection (and the server's per-chunk persist),
# which can stall keepalive pings. Flush on size or a short interval instead.
_LOG_FLUSH_BYTES = 16384
_LOG_FLUSH_SECONDS = 0.5

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
                buf: list[str] = []
                buf_len = 0
                last_flush = time.monotonic()

                def flush():
                    nonlocal buf, buf_len, offset, last_flush
                    if not buf:
                        return
                    data = "".join(buf)
                    self._log_callback(data, offset)
                    offset += len(data.encode("utf-8"))
                    buf = []
                    buf_len = 0
                    last_flush = time.monotonic()

                for line in self._process.stdout:
                    buf.append(line)
                    buf_len += len(line)
                    if buf_len >= _LOG_FLUSH_BYTES or (time.monotonic() - last_flush) >= _LOG_FLUSH_SECONDS:
                        flush()
                flush()

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
