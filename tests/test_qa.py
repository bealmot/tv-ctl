import json
import os
import time
from unittest.mock import patch

import pytest
import websockets
import yaml

from tv_ctl import qa
from tv_ctl.config import Config
from tv_ctl.luna import LunaError


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


@pytest.fixture
def cartridge_path(tmp_path):
    cartridge = {
        "name": "hbchannel-smoke",
        "appId": "org.webosbrew.hbchannel",
        "steps": [
            {"key": "ENTER", "label": "open-library"},
            {"wait": 0.01, "label": "settle"},
            {"key": "DOWN", "label": "scroll-to-second-item"},
        ],
    }
    path = tmp_path / "hbchannel-smoke.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))
    return str(path)


def test_run_cartridge_launches_app_first(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch") as mock_launch, \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_launch.assert_called_once_with(cfg, "org.webosbrew.hbchannel")


def test_run_cartridge_closes_app_before_launching(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    call_order = []
    with patch("tv_ctl.qa.commands.close", side_effect=lambda cfg, app_id: call_order.append("close")) as mock_close, \
         patch("tv_ctl.qa.commands.launch", side_effect=lambda cfg, app_id: call_order.append("launch")) as mock_launch, \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_close.assert_called_once_with(cfg, "org.webosbrew.hbchannel")
    mock_launch.assert_called_once_with(cfg, "org.webosbrew.hbchannel")
    assert call_order == ["close", "launch"]


def test_run_cartridge_continues_when_close_fails_because_not_running(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close", side_effect=LunaError(
             "luna-send call failed for uri=luna://com.webos.applicationManager/closeByAppId: app is not running"
         )), \
         patch("tv_ctl.qa.commands.launch") as mock_launch, \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        result = qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_launch.assert_called_once_with(cfg, "org.webosbrew.hbchannel")
    assert len(result) == 3


def test_run_cartridge_reraises_close_error_that_isnt_not_running(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close", side_effect=LunaError("ssh command failed (exit 255): connection refused")), \
         patch("tv_ctl.qa.commands.launch") as mock_launch, \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        with pytest.raises(LunaError, match="connection refused"):
            qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_launch.assert_not_called()


def test_run_cartridge_sends_keys_for_key_steps(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key") as mock_send_key, \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    calls = [c.args[1] for c in mock_send_key.call_args_list]
    assert calls == ["ENTER", "DOWN"]


def test_run_cartridge_sleeps_for_wait_steps(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path), \
         patch("tv_ctl.qa.time.sleep") as mock_sleep:
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_sleep.assert_called_once_with(0.01)


def test_run_cartridge_screenshots_after_every_step_with_labeled_filenames(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path) as mock_screenshot:
        result = qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    assert len(result) == 3
    assert os.path.basename(result[0]) == "01-open-library.png"
    assert os.path.basename(result[1]) == "02-settle.png"
    assert os.path.basename(result[2]) == "03-scroll-to-second-item.png"
    for path in result:
        assert os.path.dirname(path) == run_dir


def test_run_cartridge_creates_run_dir_if_missing(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "nested" / "run")
    assert not os.path.exists(run_dir)

    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    assert os.path.isdir(run_dir)


def test_run_cartridge_defaults_run_dir_to_timestamped_dir_next_to_cartridge(cfg, cartridge_path):
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        result = qa.run_cartridge(cfg, cartridge_path)

    cartridge_dir = os.path.dirname(cartridge_path)
    for path in result:
        assert os.path.dirname(path).startswith(cartridge_dir)


def test_run_cartridge_default_run_dir_is_runs_next_to_cartridge_not_nested(cfg, cartridge_path, tmp_path):
    # cartridge_path lives directly in tmp_path (e.g. a repo's qa/ directory);
    # default output should be <that dir>/runs/<timestamp>, not an extra
    # nested convention-named directory like qa/qa/runs or .tv-qa/runs.
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
        result = qa.run_cartridge(cfg, cartridge_path)

    run_dir = os.path.dirname(result[0])
    parent, timestamp_dir = os.path.split(run_dir)
    cartridge_dir, runs_segment = os.path.split(parent)
    assert runs_segment == "runs"
    assert cartridge_dir == str(tmp_path)


def test_run_cartridge_raises_cartridge_error_on_missing_file(cfg, tmp_path):
    with pytest.raises(qa.CartridgeError, match="not found"):
        qa.run_cartridge(cfg, str(tmp_path / "missing.tv-qa.yaml"))


def test_run_cartridge_raises_cartridge_error_on_step_missing_label(cfg, tmp_path):
    cartridge = {"name": "bad", "appId": "x.y.z", "steps": [{"key": "HOME"}]}
    path = tmp_path / "bad.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))

    with pytest.raises(qa.CartridgeError, match="label"):
        qa.run_cartridge(cfg, str(path))


def test_run_cartridge_raises_cartridge_error_on_step_with_both_key_and_wait(cfg, tmp_path):
    cartridge = {
        "name": "bad",
        "appId": "x.y.z",
        "steps": [{"key": "ENTER", "wait": 1.0, "label": "bad-step"}],
    }
    path = tmp_path / "bad.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))

    with pytest.raises(qa.CartridgeError, match="exactly one"):
        qa.run_cartridge(cfg, str(path))


def test_run_cartridge_raises_cartridge_error_on_non_mapping_yaml(cfg, tmp_path):
    path = tmp_path / "bad.tv-qa.yaml"
    path.write_text("- a\n- b\n")

    with pytest.raises(qa.CartridgeError, match="must be a YAML mapping"):
        qa.run_cartridge(cfg, str(path))


def test_run_cartridge_raises_cartridge_error_on_step_not_mapping(cfg, tmp_path):
    cartridge = {
        "name": "bad",
        "appId": "x.y.z",
        "steps": ["HOME"],
    }
    path = tmp_path / "bad.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))

    with pytest.raises(qa.CartridgeError, match="must be a mapping"):
        qa.run_cartridge(cfg, str(path))


def test_run_cartridge_raises_cartridge_error_on_step_with_key_and_assert(cfg, tmp_path):
    cartridge = {
        "name": "bad",
        "appId": "x.y.z",
        "steps": [{"key": "ENTER", "assert": {"focused_count": 1}, "label": "bad-step"}],
    }
    path = tmp_path / "bad.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))

    with pytest.raises(qa.CartridgeError, match="exactly one"):
        qa.run_cartridge(cfg, str(path))


# ── assert steps (CDP-backed) ────────────────────────────────────────────────

def _health_response(msg_id, *, focused_count=1, drawer_open=False, texts=None):
    texts = texts or ["Blade Runner 2049"]
    focused = [{"tag": "DIV", "cls": "card focused", "text": t} for t in texts[:focused_count]]
    value = json.dumps({
        "focusedCount": focused_count,
        "focused": focused,
        "activeElement": "MAIN.shell",
        "drawerOpen": drawer_open,
    })
    return json.dumps({"id": msg_id, "result": {"result": {"type": "string", "value": value}}})


def _assert_cartridge_path(tmp_path, assert_expect, focused_count=1, texts=None, drawer_open=False):
    cartridge = {
        "name": "assert-smoke",
        "appId": "org.androux.privateer",
        "steps": [
            {"key": "RIGHT", "label": "move"},
            {"assert": assert_expect, "label": "check"},
        ],
    }
    path = tmp_path / "assert-smoke.tv-qa.yaml"
    path.write_text(yaml.safe_dump(cartridge))
    return str(path)


def _run_assert_cartridge(cfg, path, handler):
    # qa.run_cartridge() drives its own asyncio.run() internally when a
    # cartridge has assert steps, so this helper must call it from a thread
    # with no event loop already running — the fake CDP server therefore
    # lives in its own background thread/loop rather than sharing this one
    # (mirrors why nesting asyncio.run() inside asyncio.run() is rejected).
    import asyncio
    import threading

    ready = threading.Event()
    stop = threading.Event()
    state = {}

    def server_thread():
        async def serve():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                state["port"] = server.sockets[0].getsockname()[1]
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.02)

        asyncio.run(serve())

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    port = state["port"]

    try:
        with patch("tv_ctl.qa.devtools_tunnel") as mock_tunnel, \
             patch("tv_ctl.qa.get_page_target", return_value={"webSocketDebuggerUrl": "ws://x/devtools/page/1"}), \
             patch("tv_ctl.qa._target_ws_url", return_value=f"ws://127.0.0.1:{port}"), \
             patch("tv_ctl.qa.commands.close"), \
             patch("tv_ctl.qa.commands.launch"), \
             patch("tv_ctl.qa.commands.send_key"), \
             patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path):
            mock_tunnel.return_value.__enter__ = lambda self: port
            mock_tunnel.return_value.__exit__ = lambda self, *a: None
            return qa.run_cartridge(cfg, path)
    finally:
        stop.set()
        thread.join(timeout=2)


async def _healthy_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        if msg["method"] == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif msg["method"] == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=1, texts=["Blade Runner 2049"]))


def test_run_cartridge_assert_focused_count_passes_when_matching(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"focused_count": 1})
    result = _run_assert_cartridge(cfg, path, _healthy_handler)
    assert len(result) == 2


def test_run_cartridge_assert_focused_count_fails_when_mismatched(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"focused_count": 2})
    with pytest.raises(qa.CartridgeAssertionError, match="focused_count=2"):
        _run_assert_cartridge(cfg, path, _healthy_handler)


def test_run_cartridge_assert_focused_text_contains_passes(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"focused_text_contains": "Blade Runner"})
    result = _run_assert_cartridge(cfg, path, _healthy_handler)
    assert len(result) == 2


def test_run_cartridge_assert_focused_text_contains_fails_on_wrong_item(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"focused_text_contains": "Akira"})
    with pytest.raises(qa.CartridgeAssertionError, match="Akira"):
        _run_assert_cartridge(cfg, path, _healthy_handler)


async def _drawer_open_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        if msg["method"] == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif msg["method"] == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=1, drawer_open=True))


def test_run_cartridge_assert_drawer_open_passes(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"drawer_open": True})
    result = _run_assert_cartridge(cfg, path, _drawer_open_handler)
    assert len(result) == 2


def test_run_cartridge_assert_drawer_open_fails_when_closed(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"drawer_open": True})
    with pytest.raises(qa.CartridgeAssertionError, match="drawer_open"):
        _run_assert_cartridge(cfg, path, _healthy_handler)


async def _panicking_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        if msg["method"] == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Runtime.exceptionThrown",
                "params": {"exceptionDetails": {"text": "Uncaught panicked at ..."}},
            }))
        elif msg["method"] == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=1))


def test_run_cartridge_assert_no_console_errors_fails_when_panic_occurred(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"no_console_errors": True})
    with pytest.raises(qa.CartridgeAssertionError, match="console error"):
        _run_assert_cartridge(cfg, path, _panicking_handler)


def test_run_cartridge_assert_no_console_errors_passes_when_clean(cfg, tmp_path):
    path = _assert_cartridge_path(tmp_path, {"no_console_errors": True})
    result = _run_assert_cartridge(cfg, path, _healthy_handler)
    assert len(result) == 2


def test_run_cartridge_without_asserts_never_opens_a_cdp_tunnel(cfg, cartridge_path, tmp_path):
    run_dir = str(tmp_path / "run")
    with patch("tv_ctl.qa.commands.close"), \
         patch("tv_ctl.qa.commands.launch"), \
         patch("tv_ctl.qa.commands.send_key"), \
         patch("tv_ctl.qa.commands.screenshot", side_effect=lambda cfg, path: path), \
         patch("tv_ctl.qa.devtools_tunnel") as mock_tunnel:
        qa.run_cartridge(cfg, cartridge_path, run_dir=run_dir)

    mock_tunnel.assert_not_called()
