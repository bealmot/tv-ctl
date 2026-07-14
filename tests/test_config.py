import json
import pytest

from tv_ctl import config


def test_load_config_reads_file(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "host": "203.0.113.10",
        "port": 22,
        "ssh_key": "~/.ssh/tv_webos",
    }))

    cfg = config.load_config(str(config_path))

    assert cfg.host == "203.0.113.10"
    assert cfg.port == 22
    assert cfg.ssh_key.endswith("/.ssh/tv_webos")
    assert "~" not in cfg.ssh_key  # expanded


def test_load_config_defaults_port_to_22(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "host": "203.0.113.10",
        "ssh_key": "~/.ssh/tv_webos",
    }))

    cfg = config.load_config(str(config_path))

    assert cfg.port == 22


def test_load_config_missing_file_raises_with_helpful_message(tmp_path):
    missing_path = tmp_path / "does-not-exist.json"

    with pytest.raises(FileNotFoundError) as exc_info:
        config.load_config(str(missing_path))

    assert "tv-ctl config not found" in str(exc_info.value)
    assert str(missing_path) in str(exc_info.value)
