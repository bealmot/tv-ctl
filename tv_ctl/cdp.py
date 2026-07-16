import asyncio
import base64
import contextlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

import websockets

from .config import Config


class CdpError(Exception):
    pass


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def devtools_tunnel(cfg: Config, remote_port: int = 9998, connect_timeout: float = 5.0):
    """Spawn an SSH port-forward to the TV's Chrome devtools port and tear it
    down on exit. Yields the local port once it's actually accepting
    connections (not just once ssh has started)."""
    local_port = _find_free_port()
    cmd = [
        "ssh",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-i", cfg.ssh_key,
        "-p", str(cfg.port),
        "-L", f"{local_port}:127.0.0.1:{remote_port}",
        "-N", f"root@{cfg.host}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    deadline = time.monotonic() + connect_timeout
    up = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode(errors="replace").strip()
            raise CdpError(f"devtools tunnel to {cfg.host} failed: {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                up = True
                break
        except OSError:
            time.sleep(0.2)

    if not up:
        proc.terminate()
        proc.wait(timeout=5)
        raise CdpError(f"devtools tunnel to {cfg.host} did not come up within {connect_timeout}s")

    try:
        yield local_port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def get_page_target(local_port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{local_port}/json", timeout=5) as resp:
        targets = json.loads(resp.read())
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise CdpError("no page target found on the TV's devtools endpoint")
    return pages[0]


def _target_ws_url(target: dict, local_port: int) -> str:
    ws_path = target["webSocketDebuggerUrl"].split("/devtools/", 1)[1]
    return f"ws://127.0.0.1:{local_port}/devtools/{ws_path}"


async def capture_network_async(ws_url: str, duration: float, block_pattern: str | None) -> list[dict]:
    """Connect to a page target and record request/response activity for
    `duration` seconds. When `block_pattern` is set, matching requests are
    failed via the Fetch domain instead of being allowed through (real
    interception, verified live against the TV's Chrome build — not just
    passive Network-domain observation)."""
    block_re = re.compile(block_pattern) if block_pattern else None
    requests: dict[str, dict] = {}
    blocked_ids: set[str] = set()

    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        next_id = 1

        async def send(method, params=None):
            nonlocal next_id
            msg_id = next_id
            next_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            return msg_id

        await send("Network.enable")
        if block_re:
            await send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})

        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params["requestId"]
                requests[request_id] = {
                    "url": params["request"]["url"],
                    "method": params["request"]["method"],
                    "status": None,
                    "mimeType": None,
                    "blocked": request_id in blocked_ids,
                }
            elif method == "Network.responseReceived":
                entry = requests.get(params["requestId"])
                if entry:
                    entry["status"] = params["response"]["status"]
                    entry["mimeType"] = params["response"]["mimeType"]
            elif method == "Network.loadingFailed":
                entry = requests.get(params["requestId"])
                if entry:
                    entry["error"] = params.get("errorText")
            elif method == "Fetch.requestPaused":
                fetch_request_id = params["requestId"]
                url = params["request"]["url"]
                if block_re and block_re.search(url):
                    await send("Fetch.failRequest", {"requestId": fetch_request_id, "errorReason": "BlockedByClient"})
                    network_id = params.get("networkId")
                    if network_id:
                        blocked_ids.add(network_id)
                        if network_id in requests:
                            requests[network_id]["blocked"] = True
                else:
                    await send("Fetch.continueRequest", {"requestId": fetch_request_id})

    return list(requests.values())


async def capture_screencast_async(
    ws_url: str,
    out_dir: str,
    duration: float,
    quality: int = 80,
    max_width: int | None = None,
    max_height: int | None = None,
    every_nth: int = 1,
) -> list[dict]:
    """Stream `Page.screencastFrame` JPEGs off the current page for `duration`
    seconds, writing each to `out_dir` as NNNNN.jpg. Returns
    `[{"path", "timestamp"}, ...]` in capture order.

    This captures the *page's* compositor output at frame rate — smooth motion,
    unlike the one-shot luna snapshots — but only the web content of the CDP
    target, not native TV overlays. Each frame MUST be acked or the TV's Chrome
    stops sending after a couple of frames (a hard requirement of the protocol,
    not a nicety); we ack before writing so throughput isn't gated on disk.
    Command replies to enable/start/ack aren't correlated — anything that isn't
    a screencastFrame is simply ignored, the same pattern capture_network uses.
    """
    os.makedirs(out_dir, exist_ok=True)
    frames: list[dict] = []

    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        next_id = 1

        async def send(method, params=None):
            nonlocal next_id
            msg_id = next_id
            next_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            return msg_id

        await send("Page.enable")
        start_params: dict = {"format": "jpeg", "quality": quality, "everyNthFrame": max(1, every_nth)}
        if max_width:
            start_params["maxWidth"] = max_width
        if max_height:
            start_params["maxHeight"] = max_height
        await send("Page.startScreencast", start_params)

        deadline = time.monotonic() + duration
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                msg = json.loads(raw)
                if msg.get("method") != "Page.screencastFrame":
                    continue

                params = msg["params"]
                # Ack first — the TV throttles hard if acks lag.
                await send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})

                idx = len(frames) + 1
                path = os.path.join(out_dir, f"{idx:05d}.jpg")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(params["data"]))
                frames.append({"path": path, "timestamp": params.get("metadata", {}).get("timestamp")})
        finally:
            with contextlib.suppress(Exception):
                await send("Page.stopScreencast")

    return frames


def _describe_remote_object(obj: dict):
    if "value" in obj:
        return obj["value"]
    return obj.get("description")


async def _resolve_scope(ws, send, top_frame: dict) -> dict:
    scope_chain = top_frame.get("scopeChain", [])
    local_scope = next((s for s in scope_chain if s["type"] == "local"), None)
    if not local_scope:
        return {}

    object_id = local_scope["object"]["objectId"]
    msg_id = await send("Runtime.getProperties", {"objectId": object_id, "ownProperties": True})
    while True:
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            break
    if "error" in msg:
        return {}
    return {
        prop["name"]: _describe_remote_object(prop["value"])
        for prop in msg["result"]["result"]
        if "value" in prop
    }


async def capture_breakpoint_async(
    ws_url: str,
    timeout: float,
    url_pattern: str | None,
    line: int | None,
    column: int,
) -> dict:
    """Set a breakpoint, trigger a page reload to hit it, report the paused
    call stack + top-frame locals, then resume. When `url_pattern`/`line`
    aren't given, pauses on the first script execution of the reload instead
    (Debugger.setInstrumentationBreakpoint) — reliable even when the caller
    doesn't know a specific executable line in a minified/WASM bundle,
    verified live: guessed line/column breakpoints on this platform's bundles
    frequently never resolve to an executable statement."""
    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        next_id = 1

        async def send(method, params=None):
            nonlocal next_id
            msg_id = next_id
            next_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            return msg_id

        await send("Debugger.enable")
        await send("Page.enable")

        if url_pattern and line is not None:
            await send("Debugger.setBreakpointByUrl", {
                "urlRegex": url_pattern,
                "lineNumber": line,
                "columnNumber": column,
            })
        else:
            await send("Debugger.setInstrumentationBreakpoint", {"instrumentation": "beforeScriptExecution"})

        await send("Page.reload", {"ignoreCache": True})

        deadline = time.monotonic() + timeout
        paused_params = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("method") == "Debugger.paused":
                paused_params = msg["params"]
                break

        if not paused_params:
            return {"hit": False}

        call_frames = paused_params.get("callFrames", [])
        locals_ = await _resolve_scope(ws, send, call_frames[0]) if call_frames else {}

        await send("Debugger.resume")

        return {
            "hit": True,
            "reason": paused_params.get("reason"),
            "callFrames": [
                {
                    "functionName": f.get("functionName"),
                    "url": f.get("url"),
                    "location": f.get("location"),
                }
                for f in call_frames
            ],
            "locals": locals_,
        }


def capture_breakpoint(
    cfg: Config,
    timeout: float = 15.0,
    url_pattern: str | None = None,
    line: int | None = None,
    column: int = 0,
) -> dict:
    with devtools_tunnel(cfg) as local_port:
        target = get_page_target(local_port)
        ws_url = _target_ws_url(target, local_port)
        return asyncio.run(capture_breakpoint_async(ws_url, timeout, url_pattern, line, column))


def _extract_indexeddb_value(remote_object: dict):
    if "value" in remote_object:
        return remote_object["value"]
    preview = remote_object.get("preview")
    if preview:
        return {p["name"]: p["value"] for p in preview.get("properties", [])}
    return remote_object.get("description")


async def inspect_storage_async(ws_url: str, max_records: int = 20, call_timeout: float = 5.0) -> dict:
    """Read localStorage, sessionStorage, and IndexedDB contents for the
    current page's origin. Pure request/response over CDP.

    Every call is individually bounded by `call_timeout`. Verified live: a
    database left in a "blocked" pending-delete state (an open connection
    from a prior session still referencing it) makes IndexedDB.requestDatabase
    hang forever with no error and no protocol-level timeout of its own — a
    real quirk of this platform's Chrome build, not something guessable in
    advance. A timeout on that one database is recorded as an error entry
    so it can't take down the rest of the report."""
    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        next_id = 1
        pending: dict[int, dict] = {}

        async def call(method, params=None):
            nonlocal next_id
            msg_id = next_id
            next_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            async with asyncio.timeout(call_timeout):
                while msg_id not in pending:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    if "id" in msg:
                        pending[msg["id"]] = msg
            return pending.pop(msg_id)

        await call("DOMStorage.enable")
        await call("IndexedDB.enable")

        origin_resp = await call("Runtime.evaluate", {"expression": "location.origin", "returnByValue": True})
        origin = origin_resp["result"]["result"]["value"]

        result = {"origin": origin, "localStorage": {}, "sessionStorage": {}, "indexedDB": {}}

        for key, is_local in (("localStorage", True), ("sessionStorage", False)):
            resp = await call("DOMStorage.getDOMStorageItems", {
                "storageId": {"securityOrigin": origin, "isLocalStorage": is_local}
            })
            entries = resp.get("result", {}).get("entries", [])
            result[key] = {k: v for k, v in entries}

        try:
            names_resp = await call("IndexedDB.requestDatabaseNames", {"securityOrigin": origin})
        except asyncio.TimeoutError:
            result["indexedDB"] = {"error": f"timed out after {call_timeout}s listing databases"}
            return result
        db_names = names_resp.get("result", {}).get("databaseNames", [])

        for db_name in db_names:
            try:
                schema_resp = await call("IndexedDB.requestDatabase", {
                    "securityOrigin": origin, "databaseName": db_name
                })
            except asyncio.TimeoutError:
                result["indexedDB"][db_name] = {"error": f"timed out after {call_timeout}s requesting schema"}
                continue

            schema = schema_resp.get("result", {}).get("databaseWithObjectStores", {})
            stores_out = []

            for store in schema.get("objectStores", []):
                try:
                    data_resp = await call("IndexedDB.requestData", {
                        "securityOrigin": origin,
                        "databaseName": db_name,
                        "objectStoreName": store["name"],
                        "indexName": "",
                        "skipCount": 0,
                        "pageSize": max_records,
                    })
                except asyncio.TimeoutError:
                    stores_out.append({
                        "name": store["name"],
                        "error": f"timed out after {call_timeout}s requesting data",
                    })
                    continue

                data = data_resp.get("result", {})
                records = [
                    _extract_indexeddb_value(entry["value"])
                    for entry in data.get("objectStoreDataEntries", [])
                ]
                stores_out.append({
                    "name": store["name"],
                    "keyPath": store.get("keyPath"),
                    "autoIncrement": store.get("autoIncrement"),
                    "records": records,
                    "hasMore": data.get("hasMore", False),
                })

            result["indexedDB"][db_name] = {"version": schema.get("version"), "objectStores": stores_out}

        return result


def inspect_storage(cfg: Config, max_records: int = 20, call_timeout: float = 5.0) -> dict:
    with devtools_tunnel(cfg) as local_port:
        target = get_page_target(local_port)
        ws_url = _target_ws_url(target, local_port)
        return asyncio.run(inspect_storage_async(ws_url, max_records, call_timeout))


def capture_network(cfg: Config, duration: float = 10.0, block_pattern: str | None = None) -> list[dict]:
    with devtools_tunnel(cfg) as local_port:
        target = get_page_target(local_port)
        ws_url = _target_ws_url(target, local_port)
        return asyncio.run(capture_network_async(ws_url, duration, block_pattern))
