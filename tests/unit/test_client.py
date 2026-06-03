import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.command_mgr import CCommandMgr
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
