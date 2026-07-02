import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.command_mgr import CCommandMgr
from app.core.updater import RESTART_EXIT_CODE
from app.ws.client import SoilWSClient


async def async_iter(items):
    for item in items:
        yield item


def _make_client(**kwargs):
    defaults = dict(
        wss_url="ws://localhost:8000/ws/soil",
        api_key="test-api-key",
        name="WIN-01",
        capacity=10,
        version=1,
        command_mgr=MagicMock(spec=CCommandMgr),
    )
    defaults.update(kwargs)
    return SoilWSClient(**defaults)


class FakeUpgradeManager:
    def __init__(self):
        self.requests = []

    def stage_and_launch(self, request):
        self.requests.append(request)


def _fake_sysinfo():
    return {"hostname": "WIN-01", "os": "windows",
            "ip": "10.0.0.1", "cpu_percent": 5.0,
            "memory_percent": 60.0, "disk_percent": 40.0}


@pytest.mark.asyncio
async def test_register_sends_api_key_on_first_connect():
    client = _make_client()

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    registered_msg = json.dumps({
        "type": "REGISTERED",
        "machine_id": "m-uuid-1",
        "session_token": "session-tok",
    })
    ws.__aiter__ = MagicMock(return_value=async_iter([registered_msg]))

    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    reg = messages_sent[0]
    assert reg["type"] == "REGISTER"
    assert reg["version"] == "1"
    assert reg["version_code"] == 1
    assert reg["auto_upgrade"] is True
    assert reg["upgrade_channel"] == "stable"
    assert reg["api_key"] == "test-api-key"
    assert "session_token" not in reg
    assert reg["hostname"] == "WIN-01"
    assert reg["os"] == "windows"
    assert reg["ip"] == "10.0.0.1"
    assert reg["cpu_percent"] == 5.0
    assert client.machine_id == "m-uuid-1"
    assert client.session_token == "session-tok"


@pytest.mark.asyncio
async def test_register_and_pong_include_machine_version():
    from app.core.machine_version import MachineVersionStore

    store = MachineVersionStore(None)   # in-memory, defaults to 0
    store.set(2026070101)
    client = _make_client(machine_version_store=store)

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    registered_msg = json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"})
    ping_msg = json.dumps({"type": "PING"})
    ws.__aiter__ = MagicMock(return_value=async_iter([registered_msg, ping_msg]))

    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    reg = messages_sent[0]
    assert reg["type"] == "REGISTER"
    assert reg["machine_version"] == 2026070101

    pong = next(m for m in messages_sent if m["type"] == "PONG")
    assert pong["machine_version"] == 2026070101


@pytest.mark.asyncio
async def test_set_machine_version_updates_store_and_acks():
    from app.core.machine_version import MachineVersionStore

    store = MachineVersionStore(None)
    client = _make_client(machine_version_store=store)

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"}),
        json.dumps({"type": "SET_MACHINE_VERSION", "machine_version": 2026070101}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    assert store.get() == 2026070101
    ack = next(m for m in messages_sent if m["type"] == "MACHINE_VERSION_UPDATED")
    assert ack["machine_version"] == 2026070101


@pytest.mark.asyncio
async def test_set_machine_version_rejects_invalid():
    from app.core.machine_version import MachineVersionStore

    store = MachineVersionStore(None)
    client = _make_client(machine_version_store=store)

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"}),
        json.dumps({"type": "SET_MACHINE_VERSION", "machine_version": 42}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    assert store.get() == 0
    assert any(m["type"] == "MACHINE_VERSION_REJECTED" for m in messages_sent)


@pytest.mark.asyncio
async def test_set_config_writes_ini_and_restarts(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[agent]\nname = OLD\ncapacity = 4\n[toil]\nwss_url = w\napi_key = k\n")
    exits = []
    client = _make_client(config_path=str(ini), exit_func=lambda code: exits.append(code))

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"}),
        json.dumps({"type": "SET_CONFIG",
                    "config": {"agent": {"name": "NEW", "labels": "template=LINUX_ABA"}}}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)
        # let the drain-then-restart task run
        for _ in range(20):
            if exits:
                break
            await asyncio.sleep(0.01)

    assert "name = NEW" in ini.read_text()
    assert any(m["type"] == "CONFIG_UPDATED" for m in messages_sent)
    assert exits == [75]   # RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_set_config_rejects_locked_section(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[toil]\nwss_url = w\napi_key = k\n")
    client = _make_client(config_path=str(ini))

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"}),
        json.dumps({"type": "SET_CONFIG", "config": {"toil": {"api_key": "hacked"}}}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    assert "hacked" not in ini.read_text()
    assert any(m["type"] == "CONFIG_REJECTED" for m in messages_sent)


@pytest.mark.asyncio
async def test_forced_upgrade_bypasses_auto_upgrade_off():
    upgrade_mgr = MagicMock()
    cmd_mgr = MagicMock(spec=CCommandMgr)
    cmd_mgr.active_count.return_value = 0
    exits = []
    # auto_upgrade is OFF, but a forced (manual) upgrade must still proceed.
    client = _make_client(command_mgr=cmd_mgr, auto_upgrade=False, upgrade_mgr=upgrade_mgr,
                          exit_func=lambda c: exits.append(c))

    sent = []
    ws = AsyncMock()
    async def capture(msg): sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-1"}),
        json.dumps({"type": "UPGRADE_AVAILABLE", "force": True, "version": "1.0.1",
                    "url": "https://x/modric-agent-1.0.1.tar.gz"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)
        for _ in range(30):
            if exits:
                break
            await asyncio.sleep(0.01)

    assert not any(m["type"] == "UPGRADE_SKIPPED" for m in sent)
    upgrade_mgr.stage_and_apply_source.assert_called_once()
    assert exits == [75]


@pytest.mark.asyncio
async def test_source_upgrade_applies_in_process_then_exits():
    upgrade_mgr = MagicMock()
    cmd_mgr = MagicMock(spec=CCommandMgr)
    cmd_mgr.active_count.return_value = 0
    exits = []
    client = _make_client(command_mgr=cmd_mgr, auto_upgrade=True, upgrade_mgr=upgrade_mgr,
                          exit_func=lambda c: exits.append(c))

    sent = []
    ws = AsyncMock()
    async def capture(msg): sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-1"}),
        json.dumps({"type": "UPGRADE_AVAILABLE", "version": "1.0.1",
                    "url": "https://x/modric-agent-1.0.1.tar.gz"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)
        for _ in range(30):
            if exits:
                break
            await asyncio.sleep(0.01)

    # Source URL => applied in-process, not handed to the detached wheel installer.
    upgrade_mgr.stage_and_apply_source.assert_called_once()
    upgrade_mgr.stage_and_launch.assert_not_called()
    assert exits == [75]


@pytest.mark.asyncio
async def test_wheel_upgrade_uses_detached_installer():
    upgrade_mgr = MagicMock()
    cmd_mgr = MagicMock(spec=CCommandMgr)
    cmd_mgr.active_count.return_value = 0
    exits = []
    client = _make_client(command_mgr=cmd_mgr, auto_upgrade=True, upgrade_mgr=upgrade_mgr,
                          exit_func=lambda c: exits.append(c))

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-1"}),
        json.dumps({"type": "UPGRADE_AVAILABLE", "version": "1.0.1",
                    "url": "https://x/modric_agent-1.0.1-py3-none-any.whl"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)
        for _ in range(30):
            if exits:
                break
            await asyncio.sleep(0.01)

    upgrade_mgr.stage_and_launch.assert_called_once()
    upgrade_mgr.stage_and_apply_source.assert_not_called()
    assert exits == [75]


@pytest.mark.asyncio
async def test_get_agent_log_returns_tail(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("line1\nline2\nline3\n")
    client = _make_client(log_file=str(log))

    sent = []
    ws = AsyncMock()
    async def capture(msg): sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-1"}),
        json.dumps({"type": "GET_AGENT_LOG", "request_id": "r1", "name": "agent"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    reply = next(m for m in sent if m["type"] == "AGENT_LOG")
    assert reply["request_id"] == "r1"
    assert reply["data"] == "line1\nline2\nline3\n"
    assert reply["truncated"] is False


@pytest.mark.asyncio
async def test_get_agent_log_unknown_name_errors(tmp_path):
    client = _make_client(log_file=str(tmp_path / "agent.log"))
    sent = []
    ws = AsyncMock()
    async def capture(msg): sent.append(json.loads(msg))
    ws.send = capture
    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-1"}),
        json.dumps({"type": "GET_AGENT_LOG", "request_id": "r1", "name": "../secret"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    reply = next(m for m in sent if m["type"] == "AGENT_LOG")
    assert "error" in reply and "data" not in reply


@pytest.mark.asyncio
async def test_register_machine_version_defaults_to_zero_without_store():
    client = _make_client()   # no machine_version_store

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    ws.__aiter__ = MagicMock(return_value=async_iter([
        json.dumps({"type": "REGISTERED", "machine_id": "m-uuid-1"}),
    ]))
    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    assert messages_sent[0]["machine_version"] == 0


@pytest.mark.asyncio
async def test_register_uses_session_token_on_reconnect():
    client = _make_client()
    client.session_token = "existing-session-tok"

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    registered_msg = json.dumps({
        "type": "REGISTERED",
        "machine_id": "m-uuid-1",
        "session_token": "new-session-tok",
    })
    ws.__aiter__ = MagicMock(return_value=async_iter([registered_msg]))

    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    reg = messages_sent[0]
    assert reg["session_token"] == "existing-session-tok"
    assert "api_key" not in reg


@pytest.mark.asyncio
async def test_pong_includes_sysinfo():
    client = _make_client()
    client.machine_id = "m-uuid-1"
    client.session_token = "session-tok"

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    ping_msg = json.dumps({"type": "PING", "ts": 12345.0})
    registered_msg = json.dumps({
        "type": "REGISTERED",
        "machine_id": "m-uuid-1",
        "session_token": "session-tok",
    })
    ws.__aiter__ = MagicMock(return_value=async_iter([registered_msg, ping_msg]))

    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)

    pong = next(m for m in messages_sent if m.get("type") == "PONG")
    assert pong["machine_id"] == "m-uuid-1"
    assert pong["cpu_percent"] == 5.0
    assert pong["memory_percent"] == 60.0
    assert pong["disk_percent"] == 40.0
    assert pong["ip"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_upgrade_available_drains_and_restarts_when_idle():
    mgr = CCommandMgr(capacity=1)
    upgrade_mgr = FakeUpgradeManager()
    exit_codes = []
    client = _make_client(
        command_mgr=mgr,
        upgrade_mgr=upgrade_mgr,
        exit_func=exit_codes.append,
    )
    client.machine_id = "m-uuid-1"

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    await client._schedule_upgrade(ws, {
        "type": "UPGRADE_AVAILABLE",
        "version": "1.1.0",
        "url": "https://example.com/modric-agent-1.1.0.whl",
        "sha256": "abc123",
    })
    await client._upgrade_task

    assert not mgr.is_accepting()
    assert len(upgrade_mgr.requests) == 1
    assert upgrade_mgr.requests[0].version == "1.1.0"
    assert exit_codes == [RESTART_EXIT_CODE]
    assert [m["type"] for m in messages_sent] == ["UPGRADE_STARTED", "UPGRADE_RESTARTING"]


@pytest.mark.asyncio
async def test_upgrade_rejects_new_execute_messages_while_draining():
    mgr = CCommandMgr(capacity=1)
    upgrade_mgr = FakeUpgradeManager()
    client = _make_client(
        command_mgr=mgr,
        upgrade_mgr=upgrade_mgr,
        exit_func=lambda code: None,
    )
    client.machine_id = "m-uuid-1"

    messages_sent = []
    ws = AsyncMock()
    async def capture(msg): messages_sent.append(json.loads(msg))
    ws.send = capture

    registered_msg = json.dumps({
        "type": "REGISTERED",
        "machine_id": "m-uuid-1",
        "session_token": "session-tok",
    })
    upgrade_msg = json.dumps({
        "type": "UPGRADE_AVAILABLE",
        "version": "1.1.0",
        "url": "https://example.com/modric-agent-1.1.0.whl",
    })
    execute_msg = json.dumps({
        "type": "EXECUTE",
        "command_id": "c-1",
        "script_type": 2,
        "script_content": "print('should not run')",
    })
    ws.__aiter__ = MagicMock(return_value=async_iter([registered_msg, upgrade_msg, execute_msg]))

    with patch("app.ws.client._collect_sysinfo", return_value=_fake_sysinfo()):
        await client._do_session(ws)
    await client._upgrade_task

    rejected = next(m for m in messages_sent if m.get("type") == "COMMAND_REJECTED")
    assert rejected["command_id"] == "c-1"
    assert rejected["reason"] == "agent is upgrading"


@pytest.mark.asyncio
async def test_handle_execute_buffers_result_when_socket_closed():
    import websockets
    cmd = MagicMock(spec=CCommandMgr)
    cmd.run_and_stream.return_value = (0, False)
    client = _make_client(command_mgr=cmd)
    client.machine_id = "m-1"

    ws = AsyncMock()
    ws.send.side_effect = websockets.ConnectionClosed(None, None)

    await client._handle_execute(ws, {
        "command_id": "c-9", "script_type": 3,
        "script_content": "echo hi", "args": [], "timeout": 5,
    })

    assert "c-9" in client._pending_results
    assert client._pending_results["c-9"]["exit_code"] == 0
    assert client._pending_results["c-9"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_flush_pending_results_redelivers_and_clears():
    client = _make_client()
    client.machine_id = "m-new"
    client._pending_results = {
        "c-9": {"type": "COMMAND_DONE", "command_id": "c-9",
                "machine_id": "m-old", "exit_code": 0, "status": "COMPLETED"},
    }
    ws = AsyncMock()
    await client._flush_pending_results(ws)

    assert client._pending_results == {}
    sent = json.loads(ws.send.await_args.args[0])
    assert sent["command_id"] == "c-9"
    assert sent["machine_id"] == "m-new"   # refreshed to the current session
