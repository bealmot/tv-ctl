import asyncio
import json
import os
import time
from datetime import datetime

import websockets
import yaml

from . import commands, screencast, video
from .cdp import devtools_tunnel, get_page_target, _target_ws_url
from .config import Config
from .fuzz import HEALTH_JS
from .luna import LunaError


class CartridgeError(Exception):
    pass


class CartridgeAssertionError(CartridgeError):
    """A cartridge's `assert` step didn't match reality — the whole point of
    giving cartridges assertions instead of just screenshots: a regression
    fails the run instead of waiting for a human to notice something looks
    off in a folder of PNGs."""


_STEP_KINDS = ("key", "wait", "assert", "screencast")
_SCREENCAST_FORMATS = ("webp", "mp4", "webm")


def _screencast_config(step: dict, cartridge_path: str) -> dict:
    """Normalize and validate a `screencast` step's value. Accepts a mapping of
    capture options, a bare number (seconds of `duration`), or null (defaults)."""
    raw = step["screencast"]
    if raw is None:
        raw = {}
    elif isinstance(raw, bool):  # YAML `true`/`false` — not a duration
        raise CartridgeError(f"{cartridge_path} step '{step['label']}': 'screencast' must be a number or a mapping")
    elif isinstance(raw, (int, float)):
        raw = {"duration": raw}
    if not isinstance(raw, dict):
        raise CartridgeError(
            f"{cartridge_path} step '{step['label']}': 'screencast' must be a number (seconds) or a mapping, "
            f"got {type(raw).__name__}"
        )

    fmt = raw.get("format", "webp")
    if fmt not in _SCREENCAST_FORMATS:
        raise CartridgeError(
            f"{cartridge_path} step '{step['label']}': screencast format {fmt!r} not one of {_SCREENCAST_FORMATS}"
        )

    keys = raw.get("keys")
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    elif keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        raise CartridgeError(
            f"{cartridge_path} step '{step['label']}': screencast 'keys' must be a list of key names or a "
            f"comma-separated string"
        )

    return {
        "duration": float(raw.get("duration", 5.0)),
        "keys": keys,
        "format": fmt,
        "fps": int(raw.get("fps", 30)),
        "quality": int(raw.get("quality", 80)),
        "max_width": raw.get("max_width"),
        "key_delay": float(raw.get("key_delay", 0.5)),
        "key_interval": float(raw.get("key_interval", 0.6)),
    }


def _load_cartridge(cartridge_path: str) -> dict:
    if not os.path.exists(cartridge_path):
        raise CartridgeError(f"cartridge not found: {cartridge_path}")
    with open(cartridge_path) as f:
        cartridge = yaml.safe_load(f)

    if not isinstance(cartridge, dict):
        raise CartridgeError(f"cartridge {cartridge_path} must be a YAML mapping, got {type(cartridge).__name__}")

    if "appId" not in cartridge:
        raise CartridgeError(f"cartridge {cartridge_path} missing required field: appId")
    if "steps" not in cartridge or not cartridge["steps"]:
        raise CartridgeError(f"cartridge {cartridge_path} has no steps")

    for i, step in enumerate(cartridge["steps"]):
        if not isinstance(step, dict):
            raise CartridgeError(f"cartridge {cartridge_path} step {i} must be a mapping, got {type(step).__name__}")
        if "label" not in step:
            raise CartridgeError(f"cartridge {cartridge_path} step {i} missing required field: label")
        present = [k for k in _STEP_KINDS if k in step]
        if len(present) != 1:
            raise CartridgeError(
                f"cartridge {cartridge_path} step {i} ('{step['label']}') must have exactly one of "
                f"'key', 'wait', 'assert', or 'screencast', found {present or 'none'}"
            )
        if "screencast" in step:
            _screencast_config(step, cartridge_path)  # validate now, before launching anything

    return cartridge


def _default_run_dir(cartridge_path: str) -> str:
    cartridge_dir = os.path.dirname(os.path.abspath(cartridge_path))
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return os.path.join(cartridge_dir, "runs", timestamp)


def _check_assertion(health: dict, expect: dict, label: str, cartridge_path: str, console_errors: list) -> None:
    if "focused_count" in expect and health["focusedCount"] != expect["focused_count"]:
        raise CartridgeAssertionError(
            f"{cartridge_path} step '{label}': expected focused_count={expect['focused_count']}, "
            f"got {health['focusedCount']} ({health['focused']})"
        )
    if "focused_text_contains" in expect:
        wanted = expect["focused_text_contains"]
        if not any(wanted in f["text"] for f in health["focused"]):
            raise CartridgeAssertionError(
                f"{cartridge_path} step '{label}': expected a focused element containing {wanted!r}, "
                f"got {[f['text'] for f in health['focused']]}"
            )
    if "drawer_open" in expect and health["drawerOpen"] != expect["drawer_open"]:
        raise CartridgeAssertionError(
            f"{cartridge_path} step '{label}': expected drawer_open={expect['drawer_open']}, "
            f"got {health['drawerOpen']}"
        )
    if expect.get("no_console_errors") and console_errors:
        raise CartridgeAssertionError(
            f"{cartridge_path} step '{label}': expected no console errors, "
            f"got {len(console_errors)} (first: {console_errors[0]})"
        )


async def _run_cartridge_with_cdp(cfg: Config, cartridge: dict, cartridge_path: str, run_dir: str) -> list[str]:
    with devtools_tunnel(cfg) as local_port:
        target = get_page_target(local_port)
        ws_url = _target_ws_url(target, local_port)

        async with websockets.connect(ws_url, max_size=20_000_000) as ws:
            next_id = 1
            pending: dict[int, dict] = {}
            notifications: list[dict] = []

            async def send_only(method, params=None):
                nonlocal next_id
                msg_id = next_id
                next_id += 1
                await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                return msg_id

            def route(msg):
                # Shared sink for messages a screencast capture reads off the
                # wire but doesn't own: command replies and console errors still
                # land where `call`/assert checks expect them.
                if "id" in msg:
                    pending[msg["id"]] = msg
                else:
                    notifications.append(msg)

            async def call(method, params=None, timeout=5.0):
                msg_id = await send_only(method, params)
                async with asyncio.timeout(timeout):
                    while msg_id not in pending:
                        route(json.loads(await ws.recv()))
                return pending.pop(msg_id)

            await call("Runtime.enable")

            screenshot_paths = []
            for i, step in enumerate(cartridge["steps"], start=1):
                if "key" in step:
                    commands.send_key(cfg, step["key"])
                elif "wait" in step:
                    await asyncio.sleep(step["wait"])
                elif "assert" in step:
                    resp = await call("Runtime.evaluate", {"expression": HEALTH_JS, "returnByValue": True})
                    result = resp.get("result", {}).get("result", {})
                    if "value" not in result:
                        raise CartridgeAssertionError(
                            f"{cartridge_path} step '{step['label']}': health probe failed: {result}"
                        )
                    health = json.loads(result["value"])
                    console_errors = [
                        n for n in notifications
                        if n.get("method") == "Runtime.exceptionThrown"
                        or (n.get("method") == "Runtime.consoleAPICalled" and n.get("params", {}).get("type") == "error")
                    ]
                    _check_assertion(health, step["assert"], step["label"], cartridge_path, console_errors)
                elif "screencast" in step:
                    screenshot_paths.append(
                        await _capture_screencast_step(cfg, ws, send_only, route, step, i, cartridge_path, run_dir)
                    )
                    continue  # the clip (plus its frames dir) IS this step's capture — no trailing screenshot

                filename = f"{i:02d}-{step['label']}.png"
                local_path = os.path.join(run_dir, filename)
                screenshot_paths.append(commands.screenshot(cfg, local_path))

            return screenshot_paths


async def _capture_screencast_step(cfg, ws, send_only, route, step, i, cartridge_path, run_dir) -> str:
    """Record a motion clip inline during the run, over the run's own CDP
    socket. Writes `NN-label.frames/` (JPEGs + a frames.json stopwatch manifest)
    and encodes `NN-label.<fmt>` next to it. Returns the clip path."""
    sc = _screencast_config(step, cartridge_path)
    base = f"{i:02d}-{step['label']}"
    frames_dir = os.path.join(run_dir, base + ".frames")
    out_path = os.path.join(run_dir, f"{base}.{sc['format']}")

    frames = await screencast.capture_on_ws(
        cfg, ws, send_only, frames_dir, sc["duration"],
        keys=sc["keys"], key_delay=sc["key_delay"], key_interval=sc["key_interval"],
        quality=sc["quality"], max_width=sc["max_width"], on_other=route,
    )
    if not frames:
        raise CartridgeError(
            f"{cartridge_path} step '{step['label']}': screencast captured no frames — "
            f"is the app's page actually on screen?"
        )
    try:
        video.encode_timed(frames, out_path, fps=sc["fps"])
    except video.VideoError as e:
        raise CartridgeError(
            f"{cartridge_path} step '{step['label']}': {e} (raw frames kept in {frames_dir})"
        )
    return out_path


def run_cartridge(cfg: Config, cartridge_path: str, run_dir: str | None = None) -> list[str]:
    cartridge = _load_cartridge(cartridge_path)
    run_dir = run_dir or _default_run_dir(cartridge_path)
    os.makedirs(run_dir, exist_ok=True)

    try:
        commands.close(cfg, cartridge["appId"])
    except LunaError as e:
        # closeByAppId on an app that's already stopped returns errorText
        # "app is not running" (verified live against the real TV) rather
        # than success — that's the desired end state, not a failure.
        if "not running" not in str(e):
            raise
    commands.launch(cfg, cartridge["appId"])

    needs_cdp = any("assert" in step or "screencast" in step for step in cartridge["steps"])
    if needs_cdp:
        return asyncio.run(_run_cartridge_with_cdp(cfg, cartridge, cartridge_path, run_dir))

    # No CDP connection needed at all when a cartridge is pure key/wait steps
    # (the original, screenshot-only cartridges) — keeps those exactly as
    # cheap and dependency-free as before.
    screenshot_paths = []
    for i, step in enumerate(cartridge["steps"], start=1):
        if "key" in step:
            commands.send_key(cfg, step["key"])
        elif "wait" in step:
            time.sleep(step["wait"])

        filename = f"{i:02d}-{step['label']}.png"
        local_path = os.path.join(run_dir, filename)
        screenshot_paths.append(commands.screenshot(cfg, local_path))

    return screenshot_paths
