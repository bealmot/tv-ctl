import json
from unittest.mock import patch

from tv_ctl import cli
from tv_ctl.cdp import CdpError
from tv_ctl.config import Config
from tv_ctl.luna import LunaError
from tv_ctl.qa import CartridgeError


FAKE_CFG = Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


def test_send_key_dispatches_to_commands_send_key(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.send_key", return_value={"returnValue": True}) as mock_send_key:
        exit_code = cli.main(["send-key", "HOME"])

    mock_send_key.assert_called_once_with(FAKE_CFG, "HOME")
    assert exit_code == 0


def test_launch_dispatches_to_commands_launch():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.launch", return_value={"returnValue": True}) as mock_launch:
        exit_code = cli.main(["launch", "org.webosbrew.hbchannel"])

    mock_launch.assert_called_once_with(FAKE_CFG, "org.webosbrew.hbchannel")
    assert exit_code == 0


def test_screenshot_dispatches_with_explicit_outfile():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.screenshot", return_value="/tmp/out.png") as mock_screenshot:
        exit_code = cli.main(["screenshot", "/tmp/out.png"])

    mock_screenshot.assert_called_once_with(FAKE_CFG, "/tmp/out.png")
    assert exit_code == 0


def test_wake_and_power_state_and_input_status_dispatch():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.wake", return_value={"returnValue": True}) as mock_wake:
        assert cli.main(["wake"]) == 0
    mock_wake.assert_called_once_with(FAKE_CFG)

    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.power_state", return_value={"returnValue": True}) as mock_ps:
        assert cli.main(["power-state"]) == 0
    mock_ps.assert_called_once_with(FAKE_CFG)

    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.input_status", return_value={"returnValue": True}) as mock_is:
        assert cli.main(["input-status"]) == 0
    mock_is.assert_called_once_with(FAKE_CFG)


def test_main_prints_error_and_returns_1_on_luna_error(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.wake", side_effect=LunaError("ssh command failed")):
        exit_code = cli.main(["wake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error: ssh command failed" in captured.err


def test_main_lets_programmer_errors_propagate():
    # Errors like TypeError (not in our expected tuple) should still propagate
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.wake", side_effect=TypeError("unexpected programming error")):
        import pytest
        with pytest.raises(TypeError, match="unexpected programming error"):
            cli.main(["wake"])


def test_screenshot_dispatches_with_default_timestamped_outfile():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.screenshot", return_value="/tmp/tv-screenshot-20250101T120000.png") as mock_screenshot:
        exit_code = cli.main(["screenshot"])

    assert exit_code == 0
    # Verify the mock was called with a timestamped filename
    assert mock_screenshot.call_count == 1
    args = mock_screenshot.call_args[0]
    assert args[0] == FAKE_CFG
    outfile = args[1]
    assert outfile.startswith("/tmp/tv-screenshot-")
    assert outfile.endswith(".png")


def test_qa_run_dispatches_to_qa_run_cartridge():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.qa.run_cartridge", return_value=["/tmp/run/01-x.png"]) as mock_run:
        exit_code = cli.main(["qa-run", "cartridge.yaml"])

    mock_run.assert_called_once_with(FAKE_CFG, "cartridge.yaml", run_dir=None)
    assert exit_code == 0


def test_qa_run_passes_explicit_run_dir():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.qa.run_cartridge", return_value=[]) as mock_run:
        cli.main(["qa-run", "cartridge.yaml", "--run-dir", "/tmp/custom"])

    mock_run.assert_called_once_with(FAKE_CFG, "cartridge.yaml", run_dir="/tmp/custom")


def test_main_prints_error_and_returns_1_on_cartridge_error(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.qa.run_cartridge", side_effect=CartridgeError("bad cartridge")):
        exit_code = cli.main(["qa-run", "somefile.yaml"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error: bad cartridge" in captured.err


def test_main_prints_error_and_returns_1_on_config_file_not_found(capsys):
    with patch("tv_ctl.cli.config.load_config", side_effect=FileNotFoundError("tv-ctl config not found at /home/test/.config/tv-ctl/config.json. Create it with ...")):
        exit_code = cli.main(["wake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error:" in captured.err
    assert "tv-ctl config not found" in captured.err


def test_main_prints_error_and_returns_1_on_key_error(capsys):
    with patch("tv_ctl.cli.config.load_config", side_effect=KeyError("host")):
        exit_code = cli.main(["wake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error:" in captured.err


def test_main_prints_error_and_returns_1_on_json_decode_error(capsys):
    import json
    with patch("tv_ctl.cli.config.load_config", side_effect=json.JSONDecodeError("Expecting value", "", 0)):
        exit_code = cli.main(["wake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error:" in captured.err


def test_get_volume_dispatches_to_commands_get_volume():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.get_volume", return_value={"returnValue": True, "volume": 20}) as mock_get:
        exit_code = cli.main(["get-volume"])

    mock_get.assert_called_once_with(FAKE_CFG)
    assert exit_code == 0


def test_set_volume_parses_int_level():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.set_volume", return_value={"returnValue": True}) as mock_set:
        exit_code = cli.main(["set-volume", "42"])

    mock_set.assert_called_once_with(FAKE_CFG, 42)
    assert exit_code == 0


def test_get_setting_with_no_keys_passes_none():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.get_setting", return_value={"returnValue": True}) as mock_get:
        exit_code = cli.main(["get-setting", "picture"])

    mock_get.assert_called_once_with(FAKE_CFG, "picture", None)
    assert exit_code == 0


def test_get_setting_with_keys_passes_list():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.get_setting", return_value={"returnValue": True}) as mock_get:
        exit_code = cli.main(["get-setting", "picture", "backlight", "contrast"])

    mock_get.assert_called_once_with(FAKE_CFG, "picture", ["backlight", "contrast"])
    assert exit_code == 0


def test_set_setting_dispatches_to_commands_set_setting():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.set_setting", return_value={"returnValue": True}) as mock_set:
        exit_code = cli.main(["set-setting", "picture", "backlight", "99"])

    mock_set.assert_called_once_with(FAKE_CFG, "picture", "backlight", "99")
    assert exit_code == 0


def test_net_log_dispatches_with_defaults(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.net_log", return_value=[{"url": "http://x/ok"}]) as mock_net:
        exit_code = cli.main(["net-log"])

    mock_net.assert_called_once_with(FAKE_CFG, duration=10.0, block_pattern=None)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "http://x/ok" in captured.out


def test_net_log_passes_duration_and_block_pattern():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.net_log", return_value=[]) as mock_net:
        exit_code = cli.main(["net-log", "--duration", "5", "--block", "ads"])

    mock_net.assert_called_once_with(FAKE_CFG, duration=5.0, block_pattern="ads")
    assert exit_code == 0


def test_net_log_writes_to_outfile(tmp_path):
    outfile = str(tmp_path / "log.json")
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.net_log", return_value=[{"url": "http://x/ok"}]):
        exit_code = cli.main(["net-log", outfile])

    assert exit_code == 0
    with open(outfile) as f:
        assert json.load(f) == [{"url": "http://x/ok"}]


def test_break_dispatches_with_defaults():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.break_at", return_value={"hit": False}) as mock_break:
        exit_code = cli.main(["break"])

    mock_break.assert_called_once_with(FAKE_CFG, timeout=15.0, url_pattern=None, line=None, column=0)
    assert exit_code == 0


def test_break_dispatches_with_url_pattern_and_line():
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.break_at", return_value={"hit": True}) as mock_break:
        exit_code = cli.main(["break", "--url-pattern", r"app\.js$", "--line", "12", "--timeout", "5"])

    mock_break.assert_called_once_with(FAKE_CFG, timeout=5.0, url_pattern=r"app\.js$", line=12, column=0)
    assert exit_code == 0


def test_break_requires_url_pattern_and_line_together(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG):
        exit_code = cli.main(["break", "--url-pattern", r"app\.js$"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "--url-pattern and --line must be given together" in captured.err


def test_break_writes_to_outfile(tmp_path):
    outfile = str(tmp_path / "break.json")
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.break_at", return_value={"hit": True, "reason": "instrumentation"}):
        exit_code = cli.main(["break", outfile])

    assert exit_code == 0
    with open(outfile) as f:
        assert json.load(f) == {"hit": True, "reason": "instrumentation"}


def test_storage_dispatches_with_defaults(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.storage", return_value={"origin": "http://x"}) as mock_storage:
        exit_code = cli.main(["storage"])

    mock_storage.assert_called_once_with(FAKE_CFG, max_records=20, call_timeout=5.0)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "http://x" in captured.out


def test_storage_passes_max_records_and_writes_outfile(tmp_path):
    outfile = str(tmp_path / "storage.json")
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.storage", return_value={"origin": "http://x"}) as mock_storage:
        exit_code = cli.main(["storage", "--max-records", "5", outfile])

    mock_storage.assert_called_once_with(FAKE_CFG, max_records=5, call_timeout=5.0)
    assert exit_code == 0
    with open(outfile) as f:
        assert json.load(f) == {"origin": "http://x"}


def test_main_falls_back_to_type_name_when_exception_has_no_message(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.storage", side_effect=TimeoutError()):
        exit_code = cli.main(["storage"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error: TimeoutError (no further detail)" in captured.err


def test_main_prints_error_and_returns_1_on_cdp_error(capsys):
    with patch("tv_ctl.cli.config.load_config", return_value=FAKE_CFG), \
         patch("tv_ctl.cli.commands.net_log", side_effect=CdpError("devtools tunnel failed")):
        exit_code = cli.main(["net-log"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "tv-ctl: error: devtools tunnel failed" in captured.err
