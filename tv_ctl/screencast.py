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
import json
import os
import shutil
import tempfile
import threading

from . import cdp, commands, video
from .config import Config

TIMINGS_FILENAME = "frames.json"


def _write_timings(out_dir: str, frames: list[dict]) -> str:
    """Persist a per-frame timing manifest next to the JPEGs so the capture can
    be used as a stopwatch, not just watched. Each entry carries the frame's
    CDP capture timestamp and `elapsed_ms` from the first frame — the agent
    finds the frame where a thing starts and where it lands and subtracts.
    Written for every capture; it's only durable when `--keep-frames` keeps the
    directory. Returns the manifest path."""
    base = next((f.get("timestamp") for f in frames if f.get("timestamp") is not None), None)
    entries = []
    for i, f in enumerate(frames):
        t = f.get("timestamp")
        elapsed = round((t - base) * 1000) if (t is not None and base is not None) else None
        entries.append({"i": i, "file": os.path.basename(f["path"]), "t": t, "elapsed_ms": elapsed})
    span = entries[-1]["elapsed_ms"] if entries and entries[-1]["elapsed_ms"] is not None else None
    manifest = {"count": len(entries), "span_ms": span, "frames": entries}
    path = os.path.join(out_dir, TIMINGS_FILENAME)
    with open(path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    return path


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


async def capture_on_ws(
    cfg: Config,
    ws,
    send,
    out_dir: str,
    duration: float = 5.0,
    keys: list[str] | None = None,
    key_delay: float = 0.5,
    key_interval: float = 0.6,
    quality: int = 80,
    max_width: int | None = None,
    max_height: int | None = None,
    every_nth: int = 1,
    on_other=None,
) -> list[dict]:
    """Capture a screencast over an ALREADY-OPEN CDP `ws` shared with the caller
    — the QA runner reuses its one assert-path connection, since a page target
    admits only one devtools client and a second connection would be refused.
    Drives `keys` on a background thread during capture (same model as
    `record`), writes the `frames.json` timing manifest, and returns the frame
    list. `send`/`on_other` are the caller's shared sender and message router."""
    stop = threading.Event()
    driver: threading.Thread | None = None
    if keys:
        driver = threading.Thread(
            target=_drive_keys, args=(cfg, keys, key_delay, key_interval, stop), daemon=True
        )
        driver.start()

    try:
        frames = await cdp._screencast_loop(
            ws, send, out_dir, duration,
            quality=quality, max_width=max_width, max_height=max_height,
            every_nth=every_nth, on_other=on_other,
        )
    finally:
        stop.set()
        if driver is not None:
            driver.join(timeout=2.0)

    if frames:
        _write_timings(out_dir, frames)
    return frames


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

    if frames:
        _write_timings(out_dir, frames)
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
