import json
import urllib.error
import urllib.request

import pytest

from app.core.machine_version import (
    InvalidMachineVersionError,
    MachineVersionStore,
    validate_machine_version,
)
from app.rest.server import MachineVersionRestServer

# --- validation ------------------------------------------------------------

def test_validate_accepts_valid_yyyymmddxx():
    assert validate_machine_version(2026070101) == 2026070101
    assert validate_machine_version("2026070101") == 2026070101


def test_validate_accepts_zero_as_unset():
    assert validate_machine_version(0) == 0


@pytest.mark.parametrize("bad", [
    123,            # too short
    20260701011,    # too long
    2026139901,     # month 13 / invalid date
    2026023001,     # Feb 30
    -1,             # negative
    "not-a-number",
])
def test_validate_rejects_bad_values(bad):
    with pytest.raises(InvalidMachineVersionError):
        validate_machine_version(bad)


# --- store -----------------------------------------------------------------

def test_store_defaults_to_zero_when_file_absent(tmp_path):
    store = MachineVersionStore(tmp_path / "mv.json")
    assert store.get() == 0


def test_store_set_persists_and_reloads(tmp_path):
    path = tmp_path / "mv.json"
    store = MachineVersionStore(path)
    assert store.set(2026070101) == 2026070101
    assert store.get() == 2026070101
    # A fresh store over the same file restores the persisted value.
    assert MachineVersionStore(path).get() == 2026070101


def test_store_set_rejects_invalid(tmp_path):
    store = MachineVersionStore(tmp_path / "mv.json")
    with pytest.raises(InvalidMachineVersionError):
        store.set(42)
    assert store.get() == 0


def test_store_ignores_corrupt_file(tmp_path):
    path = tmp_path / "mv.json"
    path.write_text("{ not json", encoding="utf-8")
    assert MachineVersionStore(path).get() == 0


# --- REST API --------------------------------------------------------------

@pytest.fixture
def rest_server(tmp_path):
    store = MachineVersionStore(tmp_path / "mv.json")
    server = MachineVersionRestServer(store, host="127.0.0.1", port=0)
    server.start()
    host, port = server._httpd.server_address
    yield store, f"http://127.0.0.1:{port}"
    server.stop()


def _request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_rest_get_returns_current(rest_server):
    store, base = rest_server
    store.set(2026070101)
    code, body = _request(base + "/machine-version")
    assert code == 200
    assert body == {"machine_version": 2026070101}


def test_rest_put_updates(rest_server):
    store, base = rest_server
    code, body = _request(base + "/machine-version", method="PUT",
                          body={"machine_version": 2026070102})
    assert code == 200
    assert body == {"machine_version": 2026070102}
    assert store.get() == 2026070102


def test_rest_put_rejects_invalid(rest_server):
    store, base = rest_server
    code, body = _request(base + "/machine-version", method="PUT",
                          body={"machine_version": 5})
    assert code == 400
    assert "error" in body
    assert store.get() == 0


def test_rest_health(rest_server):
    _store, base = rest_server
    code, body = _request(base + "/health")
    assert code == 200
    assert body["status"] == "ok"
