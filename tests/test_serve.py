import threading
import urllib.request
from http.server import HTTPServer

import pytest

from tv_ctl import serve


def test_parse_range_start_end(tmp_path):
    assert serve.parse_range("bytes=0-99", 1000) == (0, 99)


def test_parse_range_open_ended_clamps_to_eof():
    assert serve.parse_range("bytes=500-", 1000) == (500, 999)


def test_parse_range_suffix_returns_last_n_bytes():
    assert serve.parse_range("bytes=-100", 1000) == (900, 999)


def test_parse_range_end_beyond_eof_is_clamped():
    assert serve.parse_range("bytes=0-100000", 1000) == (0, 999)


@pytest.mark.parametrize("header", ["bytes=abc", "bytes=-", "bytes=2000-3000", "nonsense"])
def test_parse_range_rejects_bad_or_unsatisfiable(header):
    with pytest.raises(ValueError):
        serve.parse_range(header, 1000)


def test_content_type_known_and_unknown():
    assert serve.content_type("/x/run.mp4") == "video/mp4"
    assert serve.content_type("run.webm") == "video/webm"
    assert serve.content_type("run.webp") == "image/webp"
    assert serve.content_type("01-step.png") == "image/png"
    assert serve.content_type("mystery.bin") == "application/octet-stream"


def test_render_index_prefers_video_hero(tmp_path):
    (tmp_path / "01-a.png").write_bytes(b"\x89PNG")
    (tmp_path / "02-b.png").write_bytes(b"\x89PNG")
    (tmp_path / "run.mp4").write_bytes(b"\x00\x00")
    body = serve.render_index(str(tmp_path)).decode()
    assert "<video" in body
    assert 'src="run.mp4"' in body
    assert "01-a.png" in body and "02-b.png" in body
    assert "Frames (2)" in body


def test_render_index_falls_back_to_gif_then_note(tmp_path):
    (tmp_path / "01-a.png").write_bytes(b"\x89PNG")
    body = serve.render_index(str(tmp_path)).decode()
    assert "<video" not in body
    assert "frames only" in body  # the no-media note

    (tmp_path / "run.gif").write_bytes(b"GIF89a")
    body2 = serve.render_index(str(tmp_path)).decode()
    assert 'src="run.gif"' in body2


def test_render_index_serves_webp_as_img_hero(tmp_path):
    (tmp_path / "01-a.png").write_bytes(b"\x89PNG")
    (tmp_path / "run.webp").write_bytes(b"RIFF....WEBP")
    body = serve.render_index(str(tmp_path)).decode()
    # animated webp is an <img> hero (no <video>, so no Range needed)
    assert "<video" not in body
    assert '<img class=hero src="run.webp"' in body


def test_render_index_prefers_video_over_webp(tmp_path):
    (tmp_path / "run.webp").write_bytes(b"RIFF....WEBP")
    (tmp_path / "run.mp4").write_bytes(b"\x00\x00")
    body = serve.render_index(str(tmp_path)).decode()
    assert "<video" in body
    assert "run.webp" not in body  # video wins the hero slot


@pytest.fixture
def running_server(tmp_path):
    # A real run dir with a binary "video" file to exercise range serving.
    payload = bytes(range(256)) * 8  # 2048 deterministic bytes
    (tmp_path / "run.mp4").write_bytes(payload)
    (tmp_path / "01-step.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    handler = serve.build_handler(str(tmp_path))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    try:
        yield port, payload
    finally:
        httpd.shutdown()
        t.join(timeout=5)


def test_server_serves_index(running_server):
    port, _ = running_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
        assert resp.status == 200
        assert b"<video" in resp.read()


def test_server_honors_range_request_with_206(running_server):
    port, payload = running_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/run.mp4", headers={"Range": "bytes=10-19"}
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers["Content-Range"] == f"bytes 10-19/{len(payload)}"
        assert resp.headers["Accept-Ranges"] == "bytes"
        body = resp.read()
    assert body == payload[10:20]


def test_server_full_get_advertises_range_support(running_server):
    port, payload = running_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/run.mp4") as resp:
        assert resp.status == 200
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.read() == payload


def test_server_unsatisfiable_range_returns_416(running_server):
    port, payload = running_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/run.mp4", headers={"Range": "bytes=99999-"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 416
    assert exc.value.headers["Content-Range"] == f"bytes */{len(payload)}"


def test_server_404_for_missing_file(running_server):
    port, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope.png")
    assert exc.value.code == 404
