import threading
import time

import pytest

from app.core.command_mgr import CCommandMgr, CommandRejectedError


@pytest.fixture
def mgr():
    return CCommandMgr(capacity=2)

def test_run_python_script(mgr):
    log_lines = []
    def on_log(chunk, offset):
        log_lines.append(chunk)

    exit_code, timed_out = mgr.run_and_stream(
        command_id="c-1",
        script_type=2,        # PYTHON
        script_content="print('hello modric-agent')",
        args=[],
        timeout=30,
        log_callback=on_log,
    )
    assert exit_code == 0
    assert timed_out is False
    assert any("hello modric-agent" in line for line in log_lines)

def test_kill_running_command(mgr):
    results = {}
    def run():
        results["exit"] = mgr.run_and_stream(
            command_id="c-2",
            script_type=2,   # PYTHON
            script_content="import time; time.sleep(30)",
            args=[], timeout=60, log_callback=lambda c, o: None)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)
    mgr.kill("c-2")
    t.join(timeout=5)
    # killed process returns non-zero exit code, but a manual kill is not a timeout
    assert results.get("exit") is not None
    exit_code, timed_out = results["exit"]
    assert exit_code != 0
    assert timed_out is False

def test_timeout_kills_running_command(mgr):
    started = time.monotonic()
    exit_code, timed_out = mgr.run_and_stream(
        command_id="c-timeout",
        script_type=2,
        script_content="import time; print('started', flush=True); time.sleep(30)",
        args=[],
        timeout=1,
        log_callback=lambda c, o: None,
    )

    assert exit_code != 0
    assert timed_out is True
    assert time.monotonic() - started < 5

def test_capacity_limits_parallel_commands():
    mgr = CCommandMgr(capacity=1)
    events = []

    def run(command_id):
        mgr.run_and_stream(
            command_id=command_id,
            script_type=2,
            script_content="import time; print('running', flush=True); time.sleep(0.5)",
            args=[],
            timeout=5,
            log_callback=lambda c, o: events.append((command_id, time.monotonic())),
        )

    first = threading.Thread(target=run, args=("c-first",), daemon=True)
    second = threading.Thread(target=run, args=("c-second",), daemon=True)

    first.start()
    time.sleep(0.1)
    second.start()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(events) == 2
    assert events[1][1] - events[0][1] >= 0.3

def test_drain_rejects_new_commands(mgr):
    mgr.begin_drain()

    with pytest.raises(CommandRejectedError):
        mgr.run_and_stream(
            command_id="c-drain",
            script_type=2,
            script_content="print('should not run')",
            args=[],
            timeout=5,
            log_callback=lambda c, o: None,
        )

    mgr.resume()
    assert mgr.is_accepting()

def test_active_count_includes_queued_commands():
    mgr = CCommandMgr(capacity=1)

    first = threading.Thread(
        target=lambda: mgr.run_and_stream(
            command_id="c-first",
            script_type=2,
            script_content="import time; time.sleep(0.5)",
            args=[],
            timeout=5,
            log_callback=lambda c, o: None,
        ),
        daemon=True,
    )
    second = threading.Thread(
        target=lambda: mgr.run_and_stream(
            command_id="c-second",
            script_type=2,
            script_content="print('queued')",
            args=[],
            timeout=5,
            log_callback=lambda c, o: None,
        ),
        daemon=True,
    )

    first.start()
    time.sleep(0.1)
    second.start()
    time.sleep(0.1)

    assert mgr.active_count() == 2
    assert not mgr.is_idle()

    first.join(timeout=3)
    second.join(timeout=3)

    assert mgr.wait_until_idle(timeout=1)
