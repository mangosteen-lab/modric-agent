import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config.loader import load_config
from app.config.writer import ConfigUpdateError, write_config
from app.core.updater import _apply_source_archive, _install_artifact, _is_source_archive

# --- source-tarball upgrade -----------------------------------------------

def _make_source_tarball(path: Path, prefix: str = "modric-agent-1.0.1/"):
    """A minimal 'new version' archive: app/ package + pyproject.toml, nested under a
    top-level dir like a GitHub source archive."""
    stage = path.parent / "stage"
    (stage / "app" / "core").mkdir(parents=True, exist_ok=True)
    (stage / "app" / "__init__.py").write_text("# new\n")
    (stage / "app" / "core" / "version.py").write_text("VERSION = '1.0.1'\n")
    (stage / "pyproject.toml").write_text("[project]\nname='modric-agent'\nversion='1.0.1'\n")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(stage / "app", arcname=prefix + "app")
        tf.add(stage / "pyproject.toml", arcname=prefix + "pyproject.toml")


def test_is_source_archive():
    assert _is_source_archive(Path("x.tar.gz"))
    assert _is_source_archive(Path("x.tgz"))
    assert _is_source_archive(Path("x.zip"))
    assert not _is_source_archive(Path("x.whl"))


def test_apply_source_archive_updates_code_preserves_config(tmp_path):
    # An existing agent dir with local config + venv that must survive the upgrade.
    agent_root = tmp_path / "agent"
    (agent_root / "app").mkdir(parents=True)
    (agent_root / "app" / "__init__.py").write_text("# old\n")
    (agent_root / "conf").mkdir()
    (agent_root / "conf" / "config.ini").write_text("[toil]\napi_key=secret\n")
    (agent_root / ".venv").mkdir()
    (agent_root / ".venv" / "marker").write_text("keep")

    archive = tmp_path / "modric-agent-1.0.1.tar.gz"
    _make_source_tarball(archive)

    with patch("app.core.updater.subprocess.check_call") as sync:  # skip real `uv sync`
        _install_artifact(archive, agent_root=agent_root)
    sync.assert_called_once()  # uv sync invoked

    assert (agent_root / "app" / "__init__.py").read_text() == "# new\n"
    assert (agent_root / "app" / "core" / "version.py").exists()      # new file pulled in
    assert (agent_root / "conf" / "config.ini").read_text() == "[toil]\napi_key=secret\n"
    assert (agent_root / ".venv" / "marker").read_text() == "keep"    # venv untouched


def test_apply_source_archive_flat_and_zip(tmp_path):
    agent_root = tmp_path / "agent"
    (agent_root / "app").mkdir(parents=True)
    # Flat zip (no nesting dir).
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("app/__init__.py", "# zipped\n")
        zf.writestr("pyproject.toml", "[project]\nname='modric-agent'\nversion='2'\n")
    with patch("app.core.updater.subprocess.check_call"):
        _apply_source_archive(archive, agent_root)
    assert (agent_root / "app" / "__init__.py").read_text() == "# zipped\n"


# --- config writer + backward compatibility --------------------------------

def _write_ini(path: Path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


def test_write_config_merges_and_preserves_toil(tmp_path):
    ini = _write_ini(tmp_path / "config.ini",
                     "[agent]\nname = OLD\ncapacity = 4\nlabels = template=WIN_ABA\n\n"
                     "[toil]\nwss_url = wss://x\napi_key = secret\n")
    applied = write_config(
        ini, {"agent": {"name": "NEW", "labels": "template=LINUX_ABA, os=linux"}})

    assert applied == {"agent": {"name": "NEW", "labels": "template=LINUX_ABA, os=linux"}}
    cfg = load_config(str(ini))
    assert cfg["name"] == "NEW"
    assert cfg["labels"] == {"template": "LINUX_ABA", "os": "linux"}
    assert cfg["capacity"] == 4                    # untouched key preserved
    assert cfg["api_key"] == "secret"              # [toil] preserved


def test_write_config_empty_value_leaves_unchanged(tmp_path):
    ini = _write_ini(tmp_path / "config.ini",
                     "[agent]\nname = KEEP\n[toil]\nwss_url = w\napi_key = k\n")
    write_config(ini, {"agent": {"name": ""}})
    assert load_config(str(ini))["name"] == "KEEP"


def test_write_config_rejects_toil_and_bad_values(tmp_path):
    ini = _write_ini(tmp_path / "config.ini", "[toil]\nwss_url = w\napi_key = k\n")
    with pytest.raises(ConfigUpdateError):
        write_config(ini, {"toil": {"api_key": "hacked"}})
    with pytest.raises(ConfigUpdateError):
        write_config(ini, {"agent": {"capacity": "0"}})
    with pytest.raises(ConfigUpdateError):
        write_config(ini, {"logging": {"level": "LOUD"}})


def test_legacy_config_loads_with_defaults(tmp_path):
    """A pre-existing minimal config.ini (no rest/machine_version keys) still loads,
    with every newer key falling back to its default — the backward-compat guarantee."""
    ini = _write_ini(tmp_path / "config.ini",
                     "[agent]\nname = LEGACY\ncapacity = 2\n\n"
                     "[toil]\nwss_url = wss://x\napi_key = k\n")
    cfg = load_config(str(ini))
    assert cfg["name"] == "LEGACY"
    assert cfg["rest_host"] == "127.0.0.1"
    assert cfg["rest_port"] == 8765
    assert cfg["machine_version_file"] == "state/machine_version.json"
    assert cfg["auto_upgrade"] is True
