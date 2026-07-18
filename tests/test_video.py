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


# --- encode_timed / screencast timing --------------------------------------

def test_frame_durations_uses_gaps_clamps_and_reuses_last():
    frames = [{"timestamp": 0.0}, {"timestamp": 0.1}, {"timestamp": 5.0}, {"timestamp": 5.05}]
    # gaps: 0.1, clamp(4.9)->2.0, 0.05, then last reuses prior (0.05)
    assert video._frame_durations(frames) == pytest.approx([0.1, video._MAX_FRAME_DUR, 0.05, 0.05])


def test_frame_durations_falls_back_when_timestamps_missing():
    frames = [{"timestamp": None}, {"timestamp": None}]
    assert video._frame_durations(frames) == [video._DEFAULT_FRAME_DUR, video._DEFAULT_FRAME_DUR]


def _make_jpgs(tmp_path, n=3):
    frames = []
    for i in range(1, n + 1):
        p = tmp_path / f"{i:05d}.jpg"
        p.write_bytes(b"\xff\xd8\xff")  # JPEG SOI marker
        frames.append({"path": str(p), "timestamp": float(i) * 0.1})
    return frames


def test_encode_timed_writes_concat_list_and_builds_command(tmp_path):
    frames = _make_jpgs(tmp_path, n=3)
    out = str(tmp_path / "cast.mp4")
    ok = MagicMock(returncode=0, stderr="")
    with patch("tv_ctl.video.ffmpeg_available", return_value=True), \
         patch("subprocess.run", return_value=ok) as mock_run:
        result = video.encode_timed(frames, out, fps=24)

    assert result == out
    list_path = tmp_path / "frames.txt"
    assert list_path.exists()
    body = list_path.read_text()
    # last frame is repeated so ffmpeg honors its duration
    assert body.count("00003.jpg") == 2
    assert "duration 0.1000" in body

    cmd = mock_run.call_args[0][0]
    assert cmd[:3] == ["ffmpeg", "-y", "-f"]
    assert "concat" in cmd and "-safe" in cmd
    assert cmd[cmd.index("-r") + 1] == "24"
    assert "libx264" in cmd
    assert cmd[-1] == out


def test_encode_timed_raises_without_frames(tmp_path):
    with patch("tv_ctl.video.ffmpeg_available", return_value=True):
        with pytest.raises(VideoError, match="no screencast frames"):
            video.encode_timed([], str(tmp_path / "x.mp4"))


def test_encode_timed_raises_when_ffmpeg_missing(tmp_path):
    frames = _make_jpgs(tmp_path, n=1)
    with patch("tv_ctl.video.ffmpeg_available", return_value=False):
        with pytest.raises(VideoError, match="ffmpeg not found"):
            video.encode_timed(frames, str(tmp_path / "x.mp4"))


# --- animated WebP output (Pillow) -----------------------------------------

def test_encode_run_webp_dispatches_to_pillow_not_ffmpeg(tmp_path):
    run = _make_run(tmp_path, n=3)
    with patch("tv_ctl.video._encode_webp", return_value="run.webp") as webp, \
         patch("subprocess.run") as mock_run:
        out = video.encode_run(run, out_name="run.webp", fps=2.0)

    assert out == "run.webp"
    mock_run.assert_not_called()  # no ffmpeg for webp
    paths, durations, out_path = webp.call_args[0]
    assert len(paths) == 3 and out_path.endswith("run.webp")
    assert durations == [500, 500, 500]  # 1000/2fps held per frame


def test_encode_timed_webp_dispatches_to_pillow_with_ms_durations(tmp_path):
    frames = _make_jpgs(tmp_path, n=3)  # timestamps 0.1, 0.2, 0.3
    with patch("tv_ctl.video._encode_webp", return_value="cast.webp") as webp, \
         patch("subprocess.run") as mock_run:
        out = video.encode_timed(frames, str(tmp_path / "cast.webp"))

    assert out == "cast.webp"
    mock_run.assert_not_called()
    paths, durations_ms, _ = webp.call_args[0]
    assert len(paths) == 3
    assert durations_ms == [100, 100, 100]  # 0.1s gaps -> 100ms


def test_encode_run_webp_does_not_require_ffmpeg(tmp_path):
    run = _make_run(tmp_path, n=2)
    with patch("tv_ctl.video.ffmpeg_available", return_value=False), \
         patch("tv_ctl.video._encode_webp", return_value="run.webp") as webp:
        video.encode_run(run, out_name="run.webp")
    webp.assert_called_once()


def test_encode_run_webp_end_to_end_is_animated(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    for i in range(1, 4):
        Image.new("RGB", (64, 40), (i * 40, 0, 0)).save(tmp_path / f"{i:02d}-s.png")
    out = video.encode_run(str(tmp_path), out_name="run.webp", fps=4)
    got = Image.open(out)
    assert got.format == "WEBP"
    assert got.is_animated and got.n_frames == 3


def _webp_anmf_durations(path):
    """Per-frame durations (ms) read straight from the WebP ANMF chunks —
    Pillow's info['duration'] read-back is unreliable for WebP, so verify the
    encoded bytes instead."""
    import struct
    data = open(path, "rb").read()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    i, durs = 12, []
    while i + 8 <= len(data):
        fourcc = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        payload = data[i + 8:i + 8 + size]
        if fourcc == b"ANMF":
            durs.append(payload[12] | (payload[13] << 8) | (payload[14] << 16))
        i += 8 + size + (size & 1)  # chunks are even-padded
    return durs


def test_encode_timed_webp_end_to_end_preserves_timing(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    frames = []
    for i, ts in enumerate([0.25, 0.55, 0.95], start=1):  # gaps 300ms, 400ms, reuse 400ms
        p = tmp_path / f"{i:05d}.jpg"
        Image.new("RGB", (64, 40), (0, i * 40, 0)).save(p)
        frames.append({"path": str(p), "timestamp": ts})
    out = video.encode_timed(frames, str(tmp_path / "cast.webp"))
    got = Image.open(out)
    assert got.format == "WEBP" and got.is_animated and got.n_frames == 3
    assert _webp_anmf_durations(out) == [300, 400, 400]  # real capture timing, not a fixed rate
