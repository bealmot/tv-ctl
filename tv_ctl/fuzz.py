import asyncio
import json
import random
import time
from datetime import datetime

import websockets

from . import commands
from .cdp import devtools_tunnel, get_page_target, _target_ws_url
from .config import Config
from .luna import LunaError

# Generic health probe — deliberately app-structure-agnostic so it works on
# any screen without per-screen selectors. ".focused" is this app's own
# convention for "D-pad cursor is here" (PosterCard, drawer-item, and the
# Library header controls all use the same class — see ui/src/components.rs,
# drawer.rs, screens/library.rs). A fuzz run that only sent raw keys and
# screenshotted would need a human to eyeball every frame; this instead asks
# the page itself "how many things currently think they're focused, and is
# the nav rail open" after every single keypress.
HEALTH_JS = """
JSON.stringify((function(){
  var focused = Array.from(document.querySelectorAll('.focused'));
  return {
    focusedCount: focused.length,
    focused: focused.map(function(el){
      return {tag: el.tagName, cls: el.className, text: (el.textContent||'').trim().slice(0,40)};
    }),
    activeElement: document.activeElement
      ? (document.activeElement.tagName + '.' + document.activeElement.className)
      : null,
    drawerOpen: (function(){
      var n = document.querySelector('nav.drawer');
      return n ? n.className.indexOf('open') !== -1 : false;
    })()
  };
})())
"""

# Weighted toward arrows (the bulk of real usage) with occasional Enter/Back —
# not uniform, since a fuzz run that spent 1/6 of its steps on Enter would
# spend most of its budget launching detail pages instead of exercising grid
# navigation.
KEY_WEIGHTS: list[tuple[str, int]] = [
    ("UP", 20), ("DOWN", 20), ("LEFT", 20), ("RIGHT", 20),
    ("ENTER", 12), ("BACK", 8),
]


def _pick_action(rng: random.Random) -> str:
    total = sum(w for _, w in KEY_WEIGHTS)
    r = rng.uniform(0, total)
    upto = 0.0
    for key, weight in KEY_WEIGHTS:
        upto += weight
        if r <= upto:
            return key
    return KEY_WEIGHTS[-1][0]


async def _fuzz_async(
    cfg: Config,
    ws_url: str,
    steps: int,
    seed: int,
    settle: float,
    start_path: str | None = None,
    warmup: float = 3.0,
    allow_no_focus: bool = False,
) -> dict:
    rng = random.Random(seed)
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

        if start_path is not None:
            # Not every screen is reachable by D-pad from wherever the app
            # happened to land (several screens in this app have no content
            # nav at all — see allow_no_focus below), so a route sweep needs
            # to land directly on each screen via a real navigation rather
            # than walking the nav rail from Home every time.
            await call("Page.enable")
            origin_resp = await call("Runtime.evaluate", {"expression": "location.origin", "returnByValue": True})
            origin = origin_resp["result"]["result"]["value"]
            await call("Page.navigate", {"url": origin + start_path})
            await asyncio.sleep(warmup)

        history: list[dict] = []
        anomalies: list[dict] = []
        last_health: dict | None = None

        for i in range(steps):
            action = _pick_action(rng)
            commands.send_key(cfg, action)
            await asyncio.sleep(settle)

            try:
                resp = await call("Runtime.evaluate", {"expression": HEALTH_JS, "returnByValue": True})
            except asyncio.TimeoutError:
                anomalies.append({"step": i, "action": action, "kind": "eval_timeout"})
                history.append({"step": i, "action": action, "health": None})
                continue

            result = resp.get("result", {}).get("result", {})
            if "value" not in result:
                # Most likely a navigation landed exactly during the probe, or
                # the page threw evaluating the expression itself.
                anomalies.append({"step": i, "action": action, "kind": "eval_no_value", "detail": result})
                history.append({"step": i, "action": action, "health": None})
                continue

            health = json.loads(result["value"])
            history.append({"step": i, "action": action, "health": health})

            if health["focusedCount"] == 0:
                # Expected on screens with no D-pad content nav at all (this
                # app has several — only the nav rail, or nothing, responds
                # to arrows there). The caller knows which screens those are;
                # this generic tool doesn't hardcode app-specific routes, so
                # it takes the caller's word via allow_no_focus rather than
                # flagging every keypress on those screens as an anomaly.
                if not allow_no_focus:
                    anomalies.append({"step": i, "action": action, "kind": "no_focused_element", "detail": health})
            elif health["focusedCount"] > 1 and not health["drawerOpen"]:
                # >1 simultaneously-.focused element is expected while the nav
                # rail is open (it seats a drawer-item cursor while the last
                # poster stays marked focused, by design — see drawer.rs).
                # Outside that, this is exactly the dual-focus class of bug
                # this harness was built to catch (androux-ifdaz).
                anomalies.append({"step": i, "action": action, "kind": "multiple_focused_elements", "detail": health})

            last_health = health

        console_errors = [
            n for n in notifications
            if n.get("method") == "Runtime.exceptionThrown"
            or (n.get("method") == "Runtime.consoleAPICalled" and n.get("params", {}).get("type") == "error")
        ]

        return {
            "history": history,
            "anomalies": anomalies,
            "console_errors": console_errors,
            "final_health": last_health,
        }


def fuzz(
    cfg: Config,
    app_id: str,
    steps: int = 100,
    seed: int | None = None,
    settle: float = 0.35,
    start_path: str | None = None,
    warmup: float = 3.0,
    allow_no_focus: bool = False,
) -> dict:
    """Fuzz-test D-pad navigation: launch `app_id` fresh (force-closed first —
    a bare launch on an already-running app just re-foregrounds it rather than
    reloading, verified live on this platform), then fire `steps` random key
    presses weighted toward the arrows. After every single keypress, checks
    that exactly one element on the page is marked `.focused` (the app's own
    D-pad-cursor convention) — catching a frozen/dropped-input state (zero
    focused elements) or a stuck-dual-focus state (androux-ifdaz's class of
    bug) without a human eyeballing every frame.

    `start_path`, if given, navigates there (a real page navigation, not a
    client-side route change) before fuzzing starts — lets a caller target a
    specific screen instead of only ever fuzzing from the app's default
    landing route. `warmup` is how long to wait after that navigation for the
    screen to finish loading before the first keypress.

    `allow_no_focus` silences the "zero focused elements" anomaly for the
    whole run. Some screens in a given app may have no D-pad content
    navigation at all (this tool doesn't know or care which — that's
    app-specific knowledge the caller supplies), and without this flag every
    single keypress on such a screen would be misreported as a freeze.
    Dual-focus and console-error detection still apply regardless.

    Deterministic from `seed` (defaults to the current time if omitted) so a
    failing run is exactly reproducible by re-running with the same seed —
    the whole point of fuzzing over "the TV felt broken" reports is turning
    an unrepeatable vibe into a fixed, replayable sequence.
    """
    if seed is None:
        seed = int(time.time())

    try:
        commands.close(cfg, app_id)
    except LunaError as e:
        if "not running" not in str(e):
            raise
    commands.launch(cfg, app_id)
    time.sleep(4)  # cold app boot

    with devtools_tunnel(cfg) as local_port:
        target = get_page_target(local_port)
        ws_url = _target_ws_url(target, local_port)
        result = asyncio.run(_fuzz_async(
            cfg, ws_url, steps, seed, settle,
            start_path=start_path, warmup=warmup, allow_no_focus=allow_no_focus,
        ))

    result["app_id"] = app_id
    result["seed"] = seed
    result["steps"] = steps
    result["start_path"] = start_path
    result["timestamp"] = datetime.now().isoformat()
    return result
