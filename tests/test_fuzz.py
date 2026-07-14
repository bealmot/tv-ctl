import asyncio
import json
from unittest.mock import patch

import pytest
import websockets

from tv_ctl import fuzz
from tv_ctl.config import Config
from tv_ctl.luna import LunaError


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


def test_pick_action_is_deterministic_for_a_given_seed():
    import random

    rng_a = random.Random(42)
    rng_b = random.Random(42)
    seq_a = [fuzz._pick_action(rng_a) for _ in range(50)]
    seq_b = [fuzz._pick_action(rng_b) for _ in range(50)]
    assert seq_a == seq_b


def test_pick_action_only_returns_known_keys():
    import random

    rng = random.Random(7)
    known = {key for key, _ in fuzz.KEY_WEIGHTS}
    for _ in range(200):
        assert fuzz._pick_action(rng) in known


def _health_response(msg_id, *, focused_count=1, drawer_open=False, texts=None):
    texts = texts or ["Item"]
    focused = [{"tag": "DIV", "cls": "card focused", "text": t} for t in texts[:focused_count]]
    value = json.dumps({
        "focusedCount": focused_count,
        "focused": focused,
        "activeElement": "MAIN.shell",
        "drawerOpen": drawer_open,
    })
    return json.dumps({"id": msg_id, "result": {"result": {"type": "string", "value": value}}})


async def _healthy_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=1))


async def _frozen_handler(ws):
    """Every health check reports zero focused elements — simulates a
    completely stuck D-pad (androux-optrw's class of bug)."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=0))


async def _dual_focus_handler(ws):
    """Every health check reports two focused elements with the drawer
    closed — androux-ifdaz's class of bug (a stale card stuck .focused
    alongside the real one)."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=2, texts=["A", "B"]))


async def _dual_focus_with_drawer_open_handler(ws):
    """Two focused elements but the drawer IS open — expected per drawer.rs
    (a seated drawer-item cursor plus the last-focused poster), not a bug."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=2, drawer_open=True, texts=["A", "B"]))


async def _console_error_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            # A spontaneous notification (no "id") — simulates a real panic
            # logged via console_error_panic_hook, as seen live this session.
            await ws.send(json.dumps({
                "method": "Runtime.exceptionThrown",
                "params": {"exceptionDetails": {"text": "Uncaught panicked at ..."}},
            }))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=1))


def _run_fuzz_against(handler, cfg, steps=5, settle=0.01, seed=1, **kwargs):
    async def run():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch("tv_ctl.fuzz.commands.send_key"):
                return await fuzz._fuzz_async(cfg, f"ws://127.0.0.1:{port}", steps, seed, settle, **kwargs)

    return asyncio.run(run())


async def _no_focus_handler(ws):
    """Simulates a screen with no D-pad content nav at all (e.g. this app's
    Calendar/Settings/Wanted screens) — every health check genuinely reports
    zero focused elements, and that's the correct, expected state."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method == "Runtime.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(_health_response(msg_id, focused_count=0))


def test_fuzz_async_allow_no_focus_silences_the_anomaly(cfg):
    result = _run_fuzz_against(_no_focus_handler, cfg, allow_no_focus=True)
    assert result["anomalies"] == []
    assert len(result["history"]) == 5


def test_fuzz_async_without_allow_no_focus_still_flags_it(cfg):
    result = _run_fuzz_against(_no_focus_handler, cfg, allow_no_focus=False)
    kinds = {a["kind"] for a in result["anomalies"]}
    assert "no_focused_element" in kinds


async def _navigate_capturing_handler(ws, captured: list):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]
        if method in ("Runtime.enable", "Page.enable"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            params = msg.get("params", {})
            if params.get("expression") == "location.origin":
                await ws.send(json.dumps({
                    "id": msg_id,
                    "result": {"result": {"type": "string", "value": "http://tv.local:8095"}},
                }))
            else:
                await ws.send(_health_response(msg_id, focused_count=1))
        elif method == "Page.navigate":
            captured.append(msg["params"]["url"])
            await ws.send(json.dumps({"id": msg_id, "result": {"frameId": "1"}}))


def test_fuzz_async_navigates_to_start_path_before_fuzzing(cfg):
    captured: list[str] = []

    async def handler(ws):
        await _navigate_capturing_handler(ws, captured)

    result = _run_fuzz_against(handler, cfg, start_path="/library", warmup=0.01)
    assert captured == ["http://tv.local:8095/library"]
    assert len(result["history"]) == 5


def test_fuzz_async_skips_navigation_when_no_start_path(cfg):
    result = _run_fuzz_against(_healthy_handler, cfg, start_path=None)
    assert result["anomalies"] == []


def test_fuzz_async_reports_no_anomalies_when_focus_stays_healthy(cfg):
    result = _run_fuzz_against(_healthy_handler, cfg)
    assert result["anomalies"] == []
    assert len(result["history"]) == 5


def test_fuzz_async_flags_no_focused_element_as_an_anomaly(cfg):
    result = _run_fuzz_against(_frozen_handler, cfg)
    kinds = {a["kind"] for a in result["anomalies"]}
    assert "no_focused_element" in kinds
    assert len(result["anomalies"]) == 5


def test_fuzz_async_flags_multiple_focused_elements_when_drawer_closed(cfg):
    result = _run_fuzz_against(_dual_focus_handler, cfg)
    kinds = {a["kind"] for a in result["anomalies"]}
    assert "multiple_focused_elements" in kinds


def test_fuzz_async_does_not_flag_multiple_focused_elements_when_drawer_open(cfg):
    result = _run_fuzz_against(_dual_focus_with_drawer_open_handler, cfg)
    assert result["anomalies"] == []


def test_fuzz_async_captures_console_exceptions(cfg):
    result = _run_fuzz_against(_console_error_handler, cfg)
    assert len(result["console_errors"]) == 1
    assert result["console_errors"][0]["method"] == "Runtime.exceptionThrown"


def test_fuzz_closes_then_launches_before_connecting(cfg):
    call_order = []
    with patch("tv_ctl.fuzz.commands.close", side_effect=lambda cfg, app_id: call_order.append("close")), \
         patch("tv_ctl.fuzz.commands.launch", side_effect=lambda cfg, app_id: call_order.append("launch")), \
         patch("tv_ctl.fuzz.time.sleep"), \
         patch("tv_ctl.fuzz.devtools_tunnel") as mock_tunnel, \
         patch("tv_ctl.fuzz.get_page_target", return_value={"webSocketDebuggerUrl": "ws://x/devtools/page/1"}), \
         patch("tv_ctl.fuzz._target_ws_url", return_value="ws://127.0.0.1:1/devtools/page/1"), \
         patch("tv_ctl.fuzz._fuzz_async"), \
         patch("tv_ctl.fuzz.asyncio.run", return_value={"history": [], "anomalies": [], "console_errors": [], "final_health": None}):
        mock_tunnel.return_value.__enter__ = lambda self: 1
        mock_tunnel.return_value.__exit__ = lambda self, *a: None
        fuzz.fuzz(cfg, "org.androux.privateer", steps=3, seed=1)

    assert call_order == ["close", "launch"]


def test_fuzz_continues_when_close_fails_because_not_running(cfg):
    with patch("tv_ctl.fuzz.commands.close", side_effect=LunaError("app is not running")), \
         patch("tv_ctl.fuzz.commands.launch") as mock_launch, \
         patch("tv_ctl.fuzz.time.sleep"), \
         patch("tv_ctl.fuzz.devtools_tunnel") as mock_tunnel, \
         patch("tv_ctl.fuzz.get_page_target", return_value={"webSocketDebuggerUrl": "ws://x/devtools/page/1"}), \
         patch("tv_ctl.fuzz._target_ws_url", return_value="ws://127.0.0.1:1/devtools/page/1"), \
         patch("tv_ctl.fuzz._fuzz_async"), \
         patch("tv_ctl.fuzz.asyncio.run", return_value={"history": [], "anomalies": [], "console_errors": [], "final_health": None}):
        mock_tunnel.return_value.__enter__ = lambda self: 1
        mock_tunnel.return_value.__exit__ = lambda self, *a: None
        fuzz.fuzz(cfg, "org.androux.privateer", steps=3, seed=1)

    mock_launch.assert_called_once_with(cfg, "org.androux.privateer")


def test_fuzz_reraises_close_error_that_isnt_not_running(cfg):
    with patch("tv_ctl.fuzz.commands.close", side_effect=LunaError("connection refused")), \
         patch("tv_ctl.fuzz.commands.launch") as mock_launch:
        with pytest.raises(LunaError, match="connection refused"):
            fuzz.fuzz(cfg, "org.androux.privateer", steps=3, seed=1)

    mock_launch.assert_not_called()


def test_fuzz_result_includes_metadata(cfg):
    with patch("tv_ctl.fuzz.commands.close"), \
         patch("tv_ctl.fuzz.commands.launch"), \
         patch("tv_ctl.fuzz.time.sleep"), \
         patch("tv_ctl.fuzz.devtools_tunnel") as mock_tunnel, \
         patch("tv_ctl.fuzz.get_page_target", return_value={"webSocketDebuggerUrl": "ws://x/devtools/page/1"}), \
         patch("tv_ctl.fuzz._target_ws_url", return_value="ws://127.0.0.1:1/devtools/page/1"), \
         patch("tv_ctl.fuzz._fuzz_async"), \
         patch("tv_ctl.fuzz.asyncio.run", return_value={"history": [], "anomalies": [], "console_errors": [], "final_health": None}):
        mock_tunnel.return_value.__enter__ = lambda self: 1
        mock_tunnel.return_value.__exit__ = lambda self, *a: None
        result = fuzz.fuzz(cfg, "org.androux.privateer", steps=42, seed=99)

    assert result["app_id"] == "org.androux.privateer"
    assert result["seed"] == 99
    assert result["steps"] == 42
    assert "timestamp" in result
