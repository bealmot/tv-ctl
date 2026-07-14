import subprocess
from unittest.mock import patch, MagicMock

import pytest

from tv_ctl import commands, luna
from tv_ctl.config import Config


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


def test_wake_calls_turn_on_screen(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True, "state": "Active"}) as mock_call:
        result = commands.wake(cfg)

    mock_call.assert_called_once_with(cfg, "luna://com.webos.service.tvpower/power/turnOnScreen")
    assert result["state"] == "Active"


def test_power_state_calls_get_power_state(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True, "state": "Screen Saver"}) as mock_call:
        result = commands.power_state(cfg)

    mock_call.assert_called_once_with(cfg, "luna://com.webos.service.tvpower/power/getPowerState")
    assert result["state"] == "Screen Saver"


def test_send_key_passes_key_payload(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.send_key(cfg, "HOME")

    mock_call.assert_called_once_with(
        cfg, "luna://com.webos.service.networkinput/sendSpecialKey", {"key": "HOME"}
    )


def test_launch_passes_app_id_payload(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.launch(cfg, "org.webosbrew.hbchannel")

    mock_call.assert_called_once_with(
        cfg, "luna://com.webos.applicationManager/launch", {"id": "org.webosbrew.hbchannel"}
    )


def test_close_passes_app_id_payload(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True, "appId": "org.webosbrew.hbchannel"}) as mock_call:
        commands.close(cfg, "org.webosbrew.hbchannel")

    mock_call.assert_called_once_with(
        cfg, "luna://com.webos.applicationManager/closeByAppId", {"id": "org.webosbrew.hbchannel"}
    )


def test_input_status_calls_get_all_input_status(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True, "devices": []}) as mock_call:
        result = commands.input_status(cfg)

    mock_call.assert_called_once_with(cfg, "luna://com.webos.service.eim/getAllInputStatus")
    assert result["devices"] == []


def test_net_log_delegates_to_cdp_capture_network(cfg):
    with patch(
        "tv_ctl.commands.cdp.capture_network",
        return_value=[{"url": "http://x/ok", "status": 200}],
    ) as mock_capture:
        result = commands.net_log(cfg, duration=5.0, block_pattern="ads")

    mock_capture.assert_called_once_with(cfg, duration=5.0, block_pattern="ads")
    assert result == [{"url": "http://x/ok", "status": 200}]


def test_break_at_delegates_to_cdp_capture_breakpoint(cfg):
    with patch(
        "tv_ctl.commands.cdp.capture_breakpoint",
        return_value={"hit": True, "reason": "instrumentation"},
    ) as mock_capture:
        result = commands.break_at(cfg, timeout=5.0, url_pattern=r"app\.js$", line=12, column=2)

    mock_capture.assert_called_once_with(cfg, timeout=5.0, url_pattern=r"app\.js$", line=12, column=2)
    assert result == {"hit": True, "reason": "instrumentation"}


def test_storage_delegates_to_cdp_inspect_storage(cfg):
    with patch(
        "tv_ctl.commands.cdp.inspect_storage",
        return_value={"origin": "http://x", "localStorage": {}, "sessionStorage": {}, "indexedDB": {}},
    ) as mock_inspect:
        result = commands.storage(cfg, max_records=5, call_timeout=2.0)

    mock_inspect.assert_called_once_with(cfg, max_records=5, call_timeout=2.0)
    assert result["origin"] == "http://x"


def test_screenshot_captures_then_scps_to_local_path(cfg, tmp_path):
    local_path = str(tmp_path / "shot.png")
    scp_result = MagicMock(returncode=0, stderr="")

    with patch(
        "tv_ctl.commands.luna.call", return_value={"returnValue": True, "capturedWidth": 960}
    ) as mock_call, patch("subprocess.run", return_value=scp_result) as mock_run:
        result = commands.screenshot(cfg, local_path)

    assert result == local_path
    capture_call = mock_call.call_args
    assert capture_call[0][1] == "luna://com.webos.service.capture/executeOneShot"
    assert capture_call[0][2]["format"] == "PNG"

    scp_cmd = mock_run.call_args[0][0]
    assert scp_cmd[0] == "scp"
    assert cfg.ssh_key in scp_cmd
    assert local_path in scp_cmd


def test_screenshot_raises_if_scp_fails(cfg, tmp_path):
    local_path = str(tmp_path / "shot.png")
    scp_result = MagicMock(returncode=1, stderr="No such file or directory")

    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}), \
         patch("subprocess.run", return_value=scp_result):
        with pytest.raises(luna.LunaError, match="scp failed"):
            commands.screenshot(cfg, local_path)


def test_screenshot_raises_luna_error_if_scp_times_out(cfg, tmp_path):
    local_path = str(tmp_path / "shot.png")

    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="scp", timeout=15)):
        with pytest.raises(luna.LunaError, match="scp .* timed out"):
            commands.screenshot(cfg, local_path)


def test_get_volume_calls_audio_service(cfg):
    with patch(
        "tv_ctl.commands.luna.call",
        return_value={"returnValue": True, "volume": 20, "muteStatus": False},
    ) as mock_call:
        result = commands.get_volume(cfg)

    mock_call.assert_called_once_with(cfg, "luna://com.webos.audio/getVolume")
    assert result["volume"] == 20


def test_set_volume_passes_level_payload(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.set_volume(cfg, 42)

    mock_call.assert_called_once_with(cfg, "luna://com.webos.audio/setVolume", {"volume": 42})


def test_get_setting_without_keys_omits_keys_field(cfg):
    with patch(
        "tv_ctl.commands.luna.call", return_value={"returnValue": True, "settings": {}}
    ) as mock_call:
        commands.get_setting(cfg, "picture")

    mock_call.assert_called_once_with(
        cfg, "luna://com.webos.settingsservice/getSystemSettings", {"category": "picture"}
    )


def test_get_setting_with_keys_includes_keys_field(cfg):
    with patch(
        "tv_ctl.commands.luna.call", return_value={"returnValue": True, "settings": {}}
    ) as mock_call:
        commands.get_setting(cfg, "picture", ["backlight"])

    mock_call.assert_called_once_with(
        cfg,
        "luna://com.webos.settingsservice/getSystemSettings",
        {"category": "picture", "keys": ["backlight"]},
    )


def test_set_setting_coerces_int_value(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.set_setting(cfg, "picture", "backlight", "99")

    mock_call.assert_called_once_with(
        cfg,
        "luna://com.webos.settingsservice/setSystemSettings",
        {"category": "picture", "settings": {"backlight": 99}},
    )


def test_set_setting_coerces_bool_value(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.set_setting(cfg, "general", "someFlag", "true")

    mock_call.assert_called_once_with(
        cfg,
        "luna://com.webos.settingsservice/setSystemSettings",
        {"category": "general", "settings": {"someFlag": True}},
    )


def test_set_setting_leaves_unrecognized_value_as_string(cfg):
    with patch("tv_ctl.commands.luna.call", return_value={"returnValue": True}) as mock_call:
        commands.set_setting(cfg, "sound", "soundMode", "standard")

    mock_call.assert_called_once_with(
        cfg,
        "luna://com.webos.settingsservice/setSystemSettings",
        {"category": "sound", "settings": {"soundMode": "standard"}},
    )
