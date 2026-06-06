import configparser

from app.bootstrap import render_config
from app.config.loader import load_config


def _clear_env(monkeypatch):
    for key in [
        "MODRIC_TOIL_WSS_URL", "MODRIC_TOIL_API_KEY", "MODRIC_AGENT_NAME",
        "MODRIC_AGENT_CAPACITY", "MODRIC_AGENT_AUTO_UPGRADE",
        "MODRIC_AGENT_UPGRADE_CHANNEL", "MODRIC_AGENT_LABELS", "MODRIC_AGENT_CONFIG",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_render_config_from_env(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    target = tmp_path / "conf" / "config.ini"
    monkeypatch.setenv("MODRIC_AGENT_CONFIG", str(target))
    monkeypatch.setenv("MODRIC_TOIL_WSS_URL", "ws://toil:8000/ws/soil")
    monkeypatch.setenv("MODRIC_TOIL_API_KEY", "secret-key")
    monkeypatch.setenv("MODRIC_AGENT_NAME", "aba-1")
    monkeypatch.setenv("MODRIC_AGENT_CAPACITY", "4")
    monkeypatch.setenv("MODRIC_AGENT_LABELS", "template=LINUX_ABA, os=linux")

    assert render_config() is True
    assert target.exists()

    # the rendered file is loadable by the agent's own loader
    cfg = load_config(str(target))
    assert cfg["wss_url"] == "ws://toil:8000/ws/soil"
    assert cfg["api_key"] == "secret-key"
    assert cfg["name"] == "aba-1"
    assert cfg["capacity"] == 4
    assert cfg["auto_upgrade"] is False           # container default
    assert cfg["labels"] == {"template": "LINUX_ABA", "os": "linux"}


def test_render_config_defaults_name_to_hostname(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    target = tmp_path / "config.ini"
    monkeypatch.setenv("MODRIC_AGENT_CONFIG", str(target))
    monkeypatch.setenv("MODRIC_TOIL_WSS_URL", "ws://toil:8000/ws/soil")
    monkeypatch.setenv("MODRIC_TOIL_API_KEY", "k")

    assert render_config() is True
    parsed = configparser.ConfigParser()
    parsed.read(target)
    assert parsed["agent"]["name"]                # hostname, never empty
    assert parsed["agent"]["auto_upgrade"] == "false"


def test_no_env_does_not_render(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    target = tmp_path / "config.ini"
    monkeypatch.setenv("MODRIC_AGENT_CONFIG", str(target))
    # neither wss_url nor api_key set -> rely on a mounted file
    assert render_config() is False
    assert not target.exists()
