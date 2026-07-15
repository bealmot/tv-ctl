from unittest.mock import patch, MagicMock

import pytest

from tv_ctl import video
from tv_ctl.video import VideoError


def _make_run(tmp_path, n=3):
    for i in range(1, n + 1):
        (tmp_path / f"{i:02d}-step.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # a non-frame PNG that must NOT be swept into the video
    (tmp_path / "notes.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(tmp_path)


def test_frame_paths_orders_by_step_and_skips_non_frames(tmp_path):
    run = _make_run(tmp_path, n=3)
    frames = video.frame_paths(run)
    assert [p.rsplit("/", 1)[-1] for p in frames] == ["01-step.png", "02-step.png", "03-step.png"]


def test_encode_run_raises_when_ffmpeg_missing(tmp_path):
    run = _make_run(tmp_path)
    with patch("tv_ctl.video.ffmpeg_available", return_value=False):
        with pytest.raises(VideoError, match="ffmpeg not found"):
            video.encode_run(run)


def test_encode_run_raises_when_no_frames(tmp_path):
    with patch("tv_ctl.video.ffmpeg_available", return_value=True):
        with pytest.raises(VideoError, match="no .* frames"):
            video.encode_run(str(tmp_path))


def test_encode_run_builds_h264_command_and_returns_path(tmp_path):
    run = _make_run(tmp_path)
    ok = MagicMock(returncode=0, stderr="")
    with patch("tv_ctl.video.ffmpeg_available", return_value=True), \
         patch("subprocess.run", return_value=ok) as mock_run:
        out = video.encode_run(run, out_name="run.mp4", fps=2.0)

    assert out.endswith("/run.mp4")
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert "-framerate" in cmd and cmd[cmd.index("-framerate") + 1] == "2.0"
    assert "libx264" in cmd
    assert "-movflags" in cmd  # faststart for progressive playback
    assert cmd[-1] == out


def test_encode_run_selects_vp9_for_webm(tmp_path):
    run = _make_run(tmp_path)
    ok = MagicMock(returncode=0, stderr="")
    with patch("tv_ctl.video.ffmpeg_available", return_value=True), \
         patch("subprocess.run", return_value=ok) as mock_run:
        video.encode_run(run, out_name="run.webm")

    cmd = mock_run.call_args[0][0]
    assert "libvpx-vp9" in cmd
    assert "libx264" not in cmd


def test_encode_run_raises_on_ffmpeg_failure(tmp_path):
    run = _make_run(tmp_path)
    fail = MagicMock(returncode=1, stderr="Error: something\nheight not divisible by 2")
    with patch("tv_ctl.video.ffmpeg_available", return_value=True), \
         patch("subprocess.run", return_value=fail):
        with pytest.raises(VideoError, match="height not divisible by 2"):
            video.encode_run(run)
