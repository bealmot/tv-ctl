"""Record smooth motion off the TV's current page via CDP screencast.

Where the luna one-shot capture gives a single still of the whole screen, this
streams the app page's compositor frames (see `cdp.capture_screencast_async`)
and stitches them into a video that plays at true speed — the delivery the
`serve`/`encode` path wanted for actual motion rather than a step slideshow.

Motion needs something to happen while we record. You can drive the TV yourself
during the capture window, or pass a `keys` script: those D-pad presses fire on
a background thread while the async frame loop runs. That thread model is
deliberate — `commands.send_key` is a blocking SSH call with nothing to do with
the CDP websocket, so it stays entirely out of the capture event loop (and out
of cdp.py, which keeps no dependency on commands).
"""

import asyncio
import os
import shutil
import tempfile
import threading

from . import cdp, commands, video
from .config import Config


def _drive_keys(cfg: Config, keys: list[str], delay: float, interval: float, stop: threading.Event) -> None:
    """Fire `keys` in order, `delay` before the first and `interval` between,
    bailing immediately if `stop` is set (capture ended). `stop.wait` doubles
    as the sleep so shutdown is prompt."""
    if stop.wait(delay):
        return
    for key in keys:
        if stop.is_set():
            return
        try:
            commands.send_key(cfg, key)
        except Exception:
            # A single dropped keypress shouldn't abort an in-flight recording;
            # the frames captured so far are still worth keeping.
            pass
        if stop.wait(interval):
            return


def record(
    cfg: Config,
    out_dir: str,
    duration: float = 5.0,
    keys: list[str] | None = None,
    key_delay: float = 0.5,
    key_interval: float = 0.6,
    quality: int = 80,
    max_width: int | None = None,
    max_height: int | None = None,
    every_nth: int = 1,
) -> list[dict]:
    """Record the current page for `duration` seconds into `out_dir`, optionally
    driving `keys` during the window. Returns the captured frame list
    (`[{"path", "timestamp"}, ...]`). Does not encode — the caller passes the
    result to `video.encode_timed`."""
    stop = threading.Event()
    driver: threading.Thread | None = None

    with cdp.devtools_tunnel(cfg) as local_port:
        target = cdp.get_page_target(local_port)
        ws_url = cdp._target_ws_url(target, local_port)

        if keys:
            driver = threading.Thread(
                target=_drive_keys,
                args=(cfg, keys, key_delay, key_interval, stop),
                daemon=True,
            )
            driver.start()

        try:
            frames = asyncio.run(
                cdp.capture_screencast_async(
                    ws_url, out_dir, duration,
                    quality=quality, max_width=max_width, max_height=max_height, every_nth=every_nth,
                )
            )
        finally:
            stop.set()
            if driver is not None:
                driver.join(timeout=2.0)

    return frames


def record_to_video(
    cfg: Config,
    out_path: str,
    duration: float = 5.0,
    keys: list[str] | None = None,
    frames_dir: str | None = None,
    fps: int = 30,
    key_delay: float = 0.5,
    key_interval: float = 0.6,
    quality: int = 80,
    max_width: int | None = None,
    every_nth: int = 1,
) -> str:
    """Record a screencast and encode it to `out_path`. Frames land in
    `frames_dir` (a temp dir alongside out_path if not given). Returns
    out_path."""
    keep = frames_dir is not None
    work = frames_dir or tempfile.mkdtemp(prefix="tv-ctl-screencast-")
    try:
        frames = record(
            cfg, work, duration=duration, keys=keys,
            key_delay=key_delay, key_interval=key_interval,
            quality=quality, max_width=max_width, every_nth=every_nth,
        )
        if not frames:
            raise video.VideoError(
                "screencast captured no frames — is an app actually on screen? "
                "(screencast records page content, so wake/launch first)"
            )
        video.encode_timed(frames, out_path, fps=fps)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return out_path
