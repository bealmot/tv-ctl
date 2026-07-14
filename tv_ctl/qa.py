import asyncio
import json
import os
import time
from datetime import datetime

import websockets
import yaml

from . import commands
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


_STEP_KINDS = ("key", "wait", "assert")


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
                f"'key', 'wait', or 'assert', found {present or 'none'}"
            )

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

            async def call(method, params=None, timeout=5.0):
                nonlocal next_id
                msg_id = next_id
                next_id += 1
                await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                async with asyncio.timeout(timeout):
                    while msg_id not in pending:
                        raw = await ws.recv()
                        msg = json.loads(raw)
                        if "id" in msg:
                            pending[msg["id"]] = msg
                        else:
                            notifications.append(msg)
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

                filename = f"{i:02d}-{step['label']}.png"
                local_path = os.path.join(run_dir, filename)
                screenshot_paths.append(commands.screenshot(cfg, local_path))

            return screenshot_paths


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

    has_asserts = any("assert" in step for step in cartridge["steps"])
    if has_asserts:
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
