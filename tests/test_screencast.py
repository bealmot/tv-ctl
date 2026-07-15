import contextlib
import threading
from unittest.mock import patch

import pytest

from tv_ctl import screencast
from tv_ctl.config import Config
from tv_ctl.video import VideoError


@pytest.fixture
def cfg():
    return Config(host="203.0.113.10", port=22, ssh_key="/home/test/.ssh/tv_webos")


def test_drive_keys_sends_all_in_order(cfg):
    sent = []
    stop = threading.Event()
    with patch("tv_ctl.commands.send_key", side_effect=lambda c, k: sent.append(k)):
        screencast._drive_keys(cfg, ["RIGHT", "DOWN", "ENTER"], delay=0, interval=0, stop=stop)
    assert sent == ["RIGHT", "DOWN", "ENTER"]


def test_drive_keys_bails_when_already_stopped(cfg):
    sent = []
    stop = threading.Event()
    stop.set()
    with patch("tv_ctl.commands.send_key", side_effect=lambda c, k: sent.append(k)):
        screencast._drive_keys(cfg, ["RIGHT", "DOWN"], delay=0, interval=0, stop=stop)
    assert sent == []


def test_drive_keys_survives_a_failing_keypress(cfg):
    sent = []

    def flaky(c, k):
        if k == "DOWN":
            raise RuntimeError("ssh blip")
        sent.append(k)

    stop = threading.Event()
    with patch("tv_ctl.commands.send_key", side_effect=flaky):
        screencast._drive_keys(cfg, ["RIGHT", "DOWN", "ENTER"], delay=0, interval=0, stop=stop)
    # DOWN blew up but RIGHT and ENTER still went through
    assert sent == ["RIGHT", "ENTER"]


@contextlib.contextmanager
def _fake_tunnel(*a, **k):
    yield 12345


def test_record_returns_frames_and_drives_keys(cfg):
    captured_frames = [{"path": "/x/00001.jpg", "timestamp": 1.0}]
    sent = []

    async def fake_capture(ws_url, out_dir, duration, **kwargs):
        return captured_frames

    with patch("tv_ctl.cdp.devtools_tunnel", _fake_tunnel), \
         patch("tv_ctl.cdp.get_page_target", return_value={"webSocketDebuggerUrl": "ws://x/devtools/page/1"}), \
         patch("tv_ctl.cdp._target_ws_url", return_value="ws://127.0.0.1:12345/devtools/page/1"), \
         patch("tv_ctl.cdp.capture_screencast_async", side_effect=fake_capture), \
         patch("tv_ctl.commands.send_key", side_effect=lambda c, k: sent.append(k)):
        frames = screencast.record(cfg, "/tmp/out", duration=0.2, keys=["RIGHT", "ENTER"], key_delay=0, key_interval=0)

    assert frames == captured_frames
    assert sent == ["RIGHT", "ENTER"]


def test_record_to_video_encodes_and_cleans_temp_frames(cfg, tmp_path):
    frames = [{"path": str(tmp_path / "00001.jpg"), "timestamp": 1.0}]
    made_dirs = {}

    def fake_record(cfg_, work, **kwargs):
        made_dirs["work"] = work
        return frames

    with patch("tv_ctl.screencast.record", side_effect=fake_record), \
         patch("tv_ctl.video.encode_timed", return_value="out.mp4") as enc:
        out = screencast.record_to_video(cfg, "out.mp4", duration=1.0)

    assert out == "out.mp4"
    enc.assert_called_once()
    # temp working dir was cleaned up (frames_dir not given)
    assert not __import__("os").path.exists(made_dirs["work"])


def test_record_to_video_keeps_frames_dir_when_given(cfg, tmp_path):
    keep = tmp_path / "frames"
    keep.mkdir()  # record() (mocked here) would normally create it
    frames = [{"path": str(keep / "00001.jpg"), "timestamp": 1.0}]

    with patch("tv_ctl.screencast.record", return_value=frames), \
         patch("tv_ctl.video.encode_timed", return_value=str(tmp_path / "out.mp4")):
        screencast.record_to_video(cfg, str(tmp_path / "out.mp4"), frames_dir=str(keep))

    # a caller-named frames dir is preserved, not deleted
    assert keep.exists()


def test_record_to_video_raises_when_no_frames_captured(cfg, tmp_path):
    with patch("tv_ctl.screencast.record", return_value=[]), \
         patch("tv_ctl.video.encode_timed") as enc:
        with pytest.raises(VideoError, match="no frames"):
            screencast.record_to_video(cfg, str(tmp_path / "out.mp4"))
    enc.assert_not_called()
