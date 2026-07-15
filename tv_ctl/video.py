"""Encode a QA run's PNG frames into a browser-playable video.

The QA runner (`qa.run_cartridge`) drops one `NN-label.png` per step into a
run directory. A folder of PNGs is what the *agent* reviews (Claude's vision
reads only the first frame of an animated GIF/video, so per-frame stills stay
the source of truth for automated review). This module is for the *human*:
it stitches those same frames into a single H.264 MP4 (or VP9 WebM) that a
real browser plays inline — the delivery path that actually animates, unlike
an inline image in a chat client. Pair it with `tv_ctl.serve`.

ffmpeg does the encode; we shell out to it the same way the rest of the tool
shells out to ssh/scp. It's an optional, runtime-only dependency — nothing
here imports it, and the QA path never needs it.
"""

import glob
import os
import shutil
import subprocess


class VideoError(Exception):
    pass


# Frames are named "NN-label.png" (see qa.py). Matching on a leading digit
# keeps a stray hand-dropped PNG in the run dir out of the video.
_FRAME_GLOB = "[0-9]*.png"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def frame_paths(run_dir: str) -> list[str]:
    """The run's frames in step order. Lexical sort is correct because the
    zero-padded NN prefix sorts the same as numeric step order."""
    return sorted(glob.glob(os.path.join(run_dir, _FRAME_GLOB)))


def _codec_args(ext: str) -> list[str]:
    if ext == ".webm":
        # VP9, constant-quality. yuv420p so it decodes on the widest set of
        # browsers rather than only desktop Chrome.
        return ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-pix_fmt", "yuv420p"]
    # H.264 baseline-friendly settings. faststart moves the moov atom to the
    # front so playback can begin before the whole file downloads — matters on
    # a phone pulling it over a tailnet link. yuv420p is required for iOS.
    return ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def encode_run(run_dir: str, out_name: str = "run.mp4", fps: float = 1.5) -> str:
    """Encode the run's PNG frames into `run_dir/out_name` and return its path.

    `fps` is the *input* frame rate — each still is held for 1/fps seconds — so
    a QA run reads as a slideshow, not a blur. `.webm` in out_name selects VP9,
    anything else H.264 MP4.

    Raises VideoError if ffmpeg is missing, there are no frames, or ffmpeg
    fails — callers can catch it and fall back to serving the raw frames.
    """
    if not ffmpeg_available():
        raise VideoError(
            "ffmpeg not found on PATH — it's required to encode a run video. "
            "Install ffmpeg, or just view the PNG frames directly."
        )

    frames = frame_paths(run_dir)
    if not frames:
        raise VideoError(f"no {_FRAME_GLOB} frames found in {run_dir}")

    out_path = os.path.join(run_dir, out_name)
    ext = os.path.splitext(out_name)[1].lower()

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-pattern_type", "glob",
        "-i", os.path.join(run_dir, _FRAME_GLOB),
        # Force even dimensions — yuv420p requires them, and an odd-sized panel
        # capture would otherwise make ffmpeg abort. trunc (not pad) keeps the
        # image itself untouched, just dropping a stray last row/column.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        *_codec_args(ext),
        out_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise VideoError("ffmpeg timed out after 120s encoding the run video")
    except FileNotFoundError:
        # Raced away between the which() check and exec, or a broken shim.
        raise VideoError("ffmpeg not found on PATH — it's required to encode a run video")

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "no stderr"
        raise VideoError(f"ffmpeg failed encoding the run video: {detail}")

    return out_path
