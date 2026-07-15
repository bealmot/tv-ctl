import asyncio
import base64
import json
import socket
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import websockets

from tv_ctl import cdp
from tv_ctl.config import Config


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


async def _fake_cdp_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method == "Network.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Network.requestWillBeSent",
                "params": {"requestId": "1", "request": {"url": "http://x/ok", "method": "GET"}},
            }))
            await ws.send(json.dumps({
                "method": "Network.responseReceived",
                "params": {"requestId": "1", "response": {"status": 200, "mimeType": "text/html"}},
            }))
            await ws.send(json.dumps({
                "method": "Network.requestWillBeSent",
                "params": {"requestId": "2", "request": {"url": "http://x/blocked", "method": "GET"}},
            }))
        elif method == "Fetch.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Fetch.requestPaused",
                "params": {"requestId": "f1", "networkId": "2", "request": {"url": "http://x/blocked"}},
            }))
        elif method in ("Fetch.continueRequest", "Fetch.failRequest"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))


async def _fake_cdp_handler_fetch_event_first(ws):
    """Reproduces the ordering seen live against the real TV: Fetch.requestPaused
    for a blocked request arrives before the matching Network.requestWillBeSent."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method == "Network.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Fetch.enable":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Fetch.requestPaused",
                "params": {"requestId": "f1", "networkId": "2", "request": {"url": "http://x/blocked"}},
            }))
        elif method == "Fetch.failRequest":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Network.requestWillBeSent",
                "params": {"requestId": "2", "request": {"url": "http://x/blocked", "method": "GET"}},
            }))
            await ws.send(json.dumps({
                "method": "Network.loadingFailed",
                "params": {"requestId": "2", "errorText": "net::ERR_BLOCKED_BY_CLIENT.Inspector"},
            }))


def test_capture_network_blocks_when_fetch_event_precedes_network_event():
    async def run():
        async with websockets.serve(_fake_cdp_handler_fetch_event_first, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.capture_network_async(f"ws://127.0.0.1:{port}", duration=1.0, block_pattern="blocked")

    entries = asyncio.run(run())
    assert len(entries) == 1
    assert entries[0]["blocked"] is True


async def _run_against_fake_server(duration, block_pattern):
    async with websockets.serve(_fake_cdp_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await cdp.capture_network_async(f"ws://127.0.0.1:{port}", duration, block_pattern)


def test_capture_network_records_requests_and_responses():
    entries = asyncio.run(_run_against_fake_server(duration=1.0, block_pattern=None))

    by_url = {e["url"]: e for e in entries}
    assert by_url["http://x/ok"]["status"] == 200
    assert by_url["http://x/ok"]["mimeType"] == "text/html"
    assert by_url["http://x/blocked"]["blocked"] is False


def test_capture_network_blocks_matching_pattern():
    entries = asyncio.run(_run_against_fake_server(duration=1.0, block_pattern="blocked"))

    by_url = {e["url"]: e for e in entries}
    assert by_url["http://x/blocked"]["blocked"] is True
    assert by_url["http://x/ok"]["blocked"] is False


async def _fake_debugger_handler_instrumentation(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method in ("Debugger.enable", "Page.enable", "Debugger.setInstrumentationBreakpoint"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Page.reload":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
            await ws.send(json.dumps({
                "method": "Debugger.paused",
                "params": {
                    "reason": "instrumentation",
                    "callFrames": [{
                        "functionName": "main",
                        "url": "http://x/app.js",
                        "location": {"scriptId": "1", "lineNumber": 5, "columnNumber": 0},
                        "scopeChain": [{
                            "type": "local",
                            "object": {"objectId": "obj1"},
                        }],
                    }],
                },
            }))
        elif method == "Runtime.getProperties":
            await ws.send(json.dumps({
                "id": msg_id,
                "result": {"result": [
                    {"name": "x", "value": {"value": 42}},
                    {"name": "y", "value": {"description": "Object"}},
                ]},
            }))
        elif method == "Debugger.resume":
            await ws.send(json.dumps({"id": msg_id, "result": {}}))


async def _fake_debugger_handler_no_pause(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        await ws.send(json.dumps({"id": msg_id, "result": {}}))


def test_capture_breakpoint_reports_stack_and_locals_on_instrumentation_pause():
    async def run():
        async with websockets.serve(_fake_debugger_handler_instrumentation, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.capture_breakpoint_async(
                f"ws://127.0.0.1:{port}", timeout=2.0, url_pattern=None, line=None, column=0
            )

    result = asyncio.run(run())
    assert result["hit"] is True
    assert result["reason"] == "instrumentation"
    assert result["callFrames"][0]["functionName"] == "main"
    assert result["locals"] == {"x": 42, "y": "Object"}


def test_capture_breakpoint_uses_url_pattern_and_line_when_given():
    seen_methods = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            seen_methods.append(msg["method"])
            if msg["method"] == "Debugger.setBreakpointByUrl":
                assert msg["params"]["urlRegex"] == "app\\.js$"
                assert msg["params"]["lineNumber"] == 12
            await ws.send(json.dumps({"id": msg["id"], "result": {}}))

    async def run():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.capture_breakpoint_async(
                f"ws://127.0.0.1:{port}", timeout=1.0, url_pattern=r"app\.js$", line=12, column=0
            )

    result = asyncio.run(run())
    assert result == {"hit": False}
    assert "Debugger.setBreakpointByUrl" in seen_methods
    assert "Debugger.setInstrumentationBreakpoint" not in seen_methods


def test_capture_breakpoint_reports_not_hit_on_timeout():
    async def run():
        async with websockets.serve(_fake_debugger_handler_no_pause, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.capture_breakpoint_async(
                f"ws://127.0.0.1:{port}", timeout=1.0, url_pattern=None, line=None, column=0
            )

    result = asyncio.run(run())
    assert result == {"hit": False}


async def _fake_storage_handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method in ("DOMStorage.enable", "IndexedDB.enable"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(json.dumps({
                "id": msg_id,
                "result": {"result": {"type": "string", "value": "http://x"}},
            }))
        elif method == "DOMStorage.getDOMStorageItems":
            is_local = msg["params"]["storageId"]["isLocalStorage"]
            entries = [["theme", "dark"]] if is_local else [["session-flag", "1"]]
            await ws.send(json.dumps({"id": msg_id, "result": {"entries": entries}}))
        elif method == "IndexedDB.requestDatabaseNames":
            await ws.send(json.dumps({"id": msg_id, "result": {"databaseNames": ["appdb"]}}))
        elif method == "IndexedDB.requestDatabase":
            await ws.send(json.dumps({
                "id": msg_id,
                "result": {"databaseWithObjectStores": {
                    "name": "appdb",
                    "version": 2,
                    "objectStores": [{"name": "items", "keyPath": {"type": "string", "string": "id"}, "autoIncrement": False, "indexes": []}],
                }},
            }))
        elif method == "IndexedDB.requestData":
            await ws.send(json.dumps({
                "id": msg_id,
                "result": {
                    "objectStoreDataEntries": [{
                        "key": {"type": "number", "value": 1},
                        "primaryKey": {"type": "number", "value": 1},
                        "value": {
                            "type": "object",
                            "preview": {"properties": [
                                {"name": "id", "value": "1"},
                                {"name": "title", "value": "hello"},
                            ]},
                        },
                    }],
                    "hasMore": False,
                },
            }))


def test_inspect_storage_reads_dom_storage_and_indexeddb():
    async def run():
        async with websockets.serve(_fake_storage_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.inspect_storage_async(f"ws://127.0.0.1:{port}", max_records=20)

    result = asyncio.run(run())

    assert result["origin"] == "http://x"
    assert result["localStorage"] == {"theme": "dark"}
    assert result["sessionStorage"] == {"session-flag": "1"}
    assert result["indexedDB"]["appdb"]["version"] == 2
    store = result["indexedDB"]["appdb"]["objectStores"][0]
    assert store["name"] == "items"
    assert store["records"] == [{"id": "1", "title": "hello"}]
    assert store["hasMore"] is False


async def _fake_storage_handler_stuck_database(ws):
    """One database (stuck-db) never responds to requestDatabase, mirroring
    the real 'blocked pending-delete' hang found live against the TV. The
    other database must still be reported."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method in ("DOMStorage.enable", "IndexedDB.enable"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(json.dumps({"id": msg_id, "result": {"result": {"type": "string", "value": "http://x"}}}))
        elif method == "DOMStorage.getDOMStorageItems":
            await ws.send(json.dumps({"id": msg_id, "result": {"entries": []}}))
        elif method == "IndexedDB.requestDatabaseNames":
            await ws.send(json.dumps({"id": msg_id, "result": {"databaseNames": ["stuck-db", "healthy-db"]}}))
        elif method == "IndexedDB.requestDatabase":
            if msg["params"]["databaseName"] == "stuck-db":
                continue  # never respond
            await ws.send(json.dumps({
                "id": msg_id,
                "result": {"databaseWithObjectStores": {"name": "healthy-db", "version": 1, "objectStores": []}},
            }))


def test_inspect_storage_isolates_a_database_that_never_responds():
    async def run():
        async with websockets.serve(_fake_storage_handler_stuck_database, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.inspect_storage_async(f"ws://127.0.0.1:{port}", max_records=20, call_timeout=0.5)

    result = asyncio.run(run())

    assert "error" in result["indexedDB"]["stuck-db"]
    assert result["indexedDB"]["healthy-db"]["version"] == 1


async def _fake_storage_handler_database_listing_never_responds(ws):
    """Mirrors a real quirk found live: after Storage.clearDataForOrigin on a
    database that had a blocked pending-delete, IndexedDB.requestDatabaseNames
    itself started hanging for that origin (not just one database)."""
    async for raw in ws:
        msg = json.loads(raw)
        msg_id = msg["id"]
        method = msg["method"]

        if method in ("DOMStorage.enable", "IndexedDB.enable"):
            await ws.send(json.dumps({"id": msg_id, "result": {}}))
        elif method == "Runtime.evaluate":
            await ws.send(json.dumps({"id": msg_id, "result": {"result": {"type": "string", "value": "http://x"}}}))
        elif method == "DOMStorage.getDOMStorageItems":
            is_local = msg["params"]["storageId"]["isLocalStorage"]
            entries = [["theme", "dark"]] if is_local else []
            await ws.send(json.dumps({"id": msg_id, "result": {"entries": entries}}))
        elif method == "IndexedDB.requestDatabaseNames":
            continue  # never respond


def test_inspect_storage_reports_error_and_keeps_dom_storage_when_listing_hangs():
    async def run():
        async with websockets.serve(_fake_storage_handler_database_listing_never_responds, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.inspect_storage_async(f"ws://127.0.0.1:{port}", max_records=20, call_timeout=0.5)

    result = asyncio.run(run())

    assert result["localStorage"] == {"theme": "dark"}
    assert "error" in result["indexedDB"]


def test_capture_screencast_writes_frames_acks_and_stops(tmp_path):
    received = {"acks": [], "stopped": False, "start_params": None}

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "Page.startScreencast":
                received["start_params"] = msg["params"]
                for i, ts in ((1, 100.0), (2, 100.5)):
                    await ws.send(json.dumps({
                        "method": "Page.screencastFrame",
                        "params": {
                            "data": base64.b64encode(f"frame{i}".encode()).decode(),
                            "sessionId": i,
                            "metadata": {"timestamp": ts},
                        },
                    }))
            elif method == "Page.screencastFrameAck":
                received["acks"].append(msg["params"]["sessionId"])
            elif method == "Page.stopScreencast":
                received["stopped"] = True

    async def run():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await cdp.capture_screencast_async(
                f"ws://127.0.0.1:{port}", str(tmp_path), duration=0.5, quality=70, max_width=800
            )

    frames = asyncio.run(run())

    assert [f["timestamp"] for f in frames] == [100.0, 100.5]
    assert open(frames[0]["path"], "rb").read() == b"frame1"
    assert open(frames[1]["path"], "rb").read() == b"frame2"
    assert [p.rsplit("/", 1)[-1] for p in (f["path"] for f in frames)] == ["00001.jpg", "00002.jpg"]
    assert received["acks"] == [1, 2]  # every frame acked, or the TV would stall
    assert received["stopped"] is True
    assert received["start_params"]["format"] == "jpeg"
    assert received["start_params"]["quality"] == 70
    assert received["start_params"]["maxWidth"] == 800


def test_get_page_target_raises_when_no_page_targets(monkeypatch):
    import urllib.request as urllib_request

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([{"type": "background_page"}]).encode()

    monkeypatch.setattr(urllib_request, "urlopen", lambda *a, **k: FakeResponse())

    with pytest.raises(cdp.CdpError, match="no page target"):
        cdp.get_page_target(12345)


def test_devtools_tunnel_raises_if_ssh_exits_immediately(cfg):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 1
    fake_proc.stderr.read.return_value = b"Warning: remote port forwarding failed"

    with patch("subprocess.Popen", return_value=fake_proc):
        with pytest.raises(cdp.CdpError, match="devtools tunnel"):
            with cdp.devtools_tunnel(cfg):
                pass


def test_devtools_tunnel_raises_if_port_never_opens(cfg):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("socket.create_connection", side_effect=OSError("refused")):
        with pytest.raises(cdp.CdpError, match="did not come up"):
            with cdp.devtools_tunnel(cfg, connect_timeout=0.3):
                pass

    fake_proc.terminate.assert_called()
