"""Serve a QA run directory as a browser page over the LAN/tailnet.

Why this exists: an animated GIF (or video) does *not* animate inline in the
Claude mobile app — the chat renderer decodes a single frame, same as the
vision model. A real browser does animate it. So the way to actually *watch* a
QA run from a phone is to point a browser at it. This runs a tiny static server
on the machine that already runs tv-ctl (already on your tailnet, already next
to the TV); you Tailscale in and open it. Plain HTTP is fine — the tailnet hop
is already WireGuard-encrypted — which sidesteps self-signed-cert warnings on
mobile.

Stdlib only. The one non-obvious requirement is HTTP Range support: mobile
Safari refuses to play an MP4 from a server that answers the whole file with a
200, so the handler speaks 206/byte-ranges for static files.
"""

import html
import http.server
import os
import re
import socketserver

from . import video

# guess_type's default table misses these on some platforms; be explicit for
# the formats we actually serve so <video>/<img> get the right MIME type.
_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".gif": "image/gif",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".html": "text/html; charset=utf-8",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def content_type(path: str) -> str:
    return _CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def parse_range(header: str, size: int) -> tuple[int, int]:
    """Parse a single-range `Range: bytes=start-end` into inclusive byte
    offsets clamped to the file. Raises ValueError on a malformed or
    unsatisfiable range so the caller can answer 416."""
    m = _RANGE_RE.fullmatch(header.strip())
    if not m:
        raise ValueError(f"unsupported Range header: {header!r}")
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        raise ValueError("Range specifies neither start nor end")
    if start_s == "":
        # bytes=-N -> the last N bytes
        length = int(end_s)
        if length == 0:
            raise ValueError("zero-length suffix range")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s != "" else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise ValueError(f"range {start}-{end} not satisfiable for size {size}")
    return start, end


def _media_names(run_dir: str) -> tuple[list[str], list[str], list[str]]:
    """Return (videos, gifs, frame_pngs) basenames present in the run dir."""
    entries = sorted(os.listdir(run_dir))
    videos = [e for e in entries if e.lower().endswith((".mp4", ".webm"))]
    gifs = [e for e in entries if e.lower().endswith(".gif")]
    frames = [f for f in video.frame_paths(run_dir)]
    frames = [os.path.basename(f) for f in frames]
    return videos, gifs, frames


def render_index(run_dir: str) -> bytes:
    """Build the run's HTML page: an autoplaying hero (video preferred, else
    animated GIF) plus the individual frames as a gallery."""
    videos, gifs, frames = _media_names(run_dir)
    title = html.escape(os.path.basename(os.path.normpath(run_dir)) or "run")

    if videos:
        sources = "".join(
            f'<source src="{html.escape(v)}" type="{content_type(v)}">' for v in videos
        )
        # muted+playsinline are what let it autoplay on mobile; loop so a short
        # run keeps repeating; controls so you can scrub.
        hero = (
            f"<h2>Run</h2><video class=hero autoplay loop muted playsinline controls>"
            f"{sources}</video>"
        )
    elif gifs:
        hero = "".join(
            f'<h2>Run</h2><img class=hero src="{html.escape(g)}" alt="animated run">'
            for g in gifs[:1]
        )
    else:
        hero = (
            "<p class=note>No video or GIF in this run yet — showing frames only. "
            "Run <code>tv-ctl encode</code> (needs ffmpeg) to add a playable video.</p>"
        )

    thumbs = "".join(
        f'<figure><img loading=lazy src="{html.escape(f)}">'
        f"<figcaption>{html.escape(f)}</figcaption></figure>"
        for f in frames
    )

    doc = f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>tv-ctl run: {title}</title>
<style>
  :root {{ color-scheme: dark light; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px;
         background: #111; color: #eee; }}
  h1 {{ font-size: 1.1rem; }}
  .hero {{ max-width: 100%; border: 1px solid #333; border-radius: 8px; }}
  .note {{ color: #f2b3b3; }}
  .grid {{ display: grid; gap: 8px;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }}
  figure {{ margin: 0; }}
  .grid img {{ width: 100%; border: 1px solid #333; border-radius: 6px; }}
  figcaption {{ font-size: 12px; color: #999; word-break: break-all; }}
  code {{ background: #222; padding: 1px 4px; border-radius: 4px; }}
</style></head>
<body>
<h1>tv-ctl QA run &mdash; {title}</h1>
{hero}
<h2>Frames ({len(frames)})</h2>
<div class=grid>{thumbs}</div>
</body></html>
"""
    return doc.encode("utf-8")


class _Handler(http.server.SimpleHTTPRequestHandler):
    # set per-server below
    run_dir = "."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self.run_dir, **kwargs)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.path in ("/", "/index.html"):
            body = render_index(self.run_dir)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_static()

    def _send_static(self):
        # translate_path (with directory= set) resolves within run_dir and
        # strips traversal — reuse it rather than hand-rolling path safety.
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not Found")
            return

        size = os.path.getsize(path)
        ctype = content_type(path)
        range_header = self.headers.get("Range")

        if range_header:
            try:
                start, end = parse_range(range_header, size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


def build_handler(run_dir: str):
    """A request-handler class bound to `run_dir`. Factored out so tests can
    drive it without standing up a socket server."""
    return type("BoundHandler", (_Handler,), {"run_dir": run_dir})


def serve(run_dir: str, port: int = 8080, host: str = "0.0.0.0") -> None:
    """Serve `run_dir` until interrupted. Blocks (it's a server)."""
    if not os.path.isdir(run_dir):
        raise NotADirectoryError(f"run directory not found: {run_dir}")

    handler = build_handler(os.path.abspath(run_dir))
    with socketserver.TCPServer((host, port), handler) as httpd:
        shown = host if host not in ("0.0.0.0", "") else "<this-host>"
        print(f"serving {run_dir} at http://{shown}:{port}/", flush=True)
        print("open it from a browser on the same LAN/tailnet; Ctrl-C to stop", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
