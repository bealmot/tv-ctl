import subprocess
from unittest.mock import patch, MagicMock

import pytest

from tv_ctl import luna
from tv_ctl.config import Config


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


def _mock_run(stdout="", stderr="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


def test_call_builds_correct_ssh_command_and_parses_response(cfg):
    fake_response = '{\n    "returnValue": true,\n    "state": "Active"\n}\n'
    with patch("subprocess.run", return_value=_mock_run(stdout=fake_response)) as mock_run:
        result = luna.call(cfg, "luna://com.webos.service.tvpower/power/getPowerState")

    assert result == {"returnValue": True, "state": "Active"}

    args = mock_run.call_args[0][0]
    assert args[0] == "ssh"
    assert "-tt" in args
    assert "-i" in args
    assert cfg.ssh_key in args
    assert "-p" in args
    assert str(cfg.port) in args
    assert f"root@{cfg.host}" in args
    remote_cmd = args[-1]
    assert "luna-send" in remote_cmd
    assert "luna://com.webos.service.tvpower/power/getPowerState" in remote_cmd


def test_call_sends_payload_as_json(cfg):
    fake_response = '{"returnValue": true}'
    with patch("subprocess.run", return_value=_mock_run(stdout=fake_response)) as mock_run:
        luna.call(cfg, "luna://com.webos.service.networkinput/sendSpecialKey", {"key": "HOME"})

    remote_cmd = mock_run.call_args[0][0][-1]
    assert '"key": "HOME"' in remote_cmd or '"key":"HOME"' in remote_cmd


def test_call_raises_on_nonzero_exit(cfg):
    with patch("subprocess.run", return_value=_mock_run(stderr="connection refused", returncode=255)):
        with pytest.raises(luna.LunaError, match="ssh command failed"):
            luna.call(cfg, "luna://com.webos.service.tvpower/power/getPowerState")


def test_call_raises_on_unparseable_output(cfg):
    with patch("subprocess.run", return_value=_mock_run(stdout="not json at all")):
        with pytest.raises(luna.LunaError, match="no JSON found"):
            luna.call(cfg, "luna://com.webos.service.tvpower/power/getPowerState")


def test_call_raises_on_return_value_false(cfg):
    fake_response = '{"returnValue": false, "errorText": "cannot find launch point"}'
    with patch("subprocess.run", return_value=_mock_run(stdout=fake_response)):
        with pytest.raises(luna.LunaError, match="cannot find launch point"):
            luna.call(cfg, "luna://com.webos.service.homelaunchpoints/addQCard")


def test_call_raises_luna_error_on_timeout(cfg):
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
        with pytest.raises(luna.LunaError, match="timed out"):
            luna.call(cfg, "luna://com.webos.service.tvpower/power/getPowerState")


def test_call_safely_quotes_payload_containing_apostrophe(cfg):
    fake_response = '{"returnValue": true}'
    with patch("subprocess.run", return_value=_mock_run(stdout=fake_response)) as mock_run:
        luna.call(cfg, "luna://com.webos.service.networkinput/sendSpecialKey", {"text": "don't stop"})

    remote_cmd = mock_run.call_args[0][0][-1]
    # The remote command must be safely shell-quoted — verify it round-trips
    # through shlex parsing without the apostrophe breaking the quoting.
    import shlex as shlex_module
    parts = shlex_module.split(remote_cmd)
    assert "luna-send" in parts
    payload_arg = parts[-1]
    assert "don't stop" in payload_arg
