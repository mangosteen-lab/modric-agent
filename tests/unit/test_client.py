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
