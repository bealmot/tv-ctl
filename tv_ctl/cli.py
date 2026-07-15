import argparse
import json
import os
import sys
from datetime import datetime

from . import commands, config, fuzz, qa, screencast as screencast_mod, serve as serve_mod, video
from .cdp import CdpError
from .luna import LunaError
from .qa import CartridgeError
from .video import VideoError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tv-ctl", description="Remote control for the rooted webOS TV")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("wake", help="Wake the screen from standby/screensaver")
    sub.add_parser("power-state", help="Read the current TV power state")

    p_key = sub.add_parser("send-key", help="Send a remote-control key press")
    p_key.add_argument("key", help="Key name, e.g. HOME, UP, DOWN, LEFT, RIGHT, ENTER, BACK")

    p_launch = sub.add_parser("launch", help="Launch an app by id")
    p_launch.add_argument("app_id")

    p_shot = sub.add_parser("screenshot", help="Capture and pull a screenshot")
    p_shot.add_argument("outfile", nargs="?", default=None)

    sub.add_parser("input-status", help="Read HDMI/input status")

    sub.add_parser("get-volume", help="Read current volume level + mute status")

    p_setvol = sub.add_parser("set-volume", help="Set volume directly (0-100); 0 is this platform's real mute")
    p_setvol.add_argument("level", type=int)

    p_getset = sub.add_parser("get-setting", help="Read TV settings for a category (e.g. picture, sound, general)")
    p_getset.add_argument("category")
    p_getset.add_argument("keys", nargs="*", help="Specific keys to read (default: everything in the category)")

    p_setset = sub.add_parser("set-setting", help="Set a single TV setting")
    p_setset.add_argument("category")
    p_setset.add_argument("key")
    p_setset.add_argument("value")

    p_net = sub.add_parser("net-log", help="Capture request/response activity via CDP Network domain")
    p_net.add_argument("--duration", type=float, default=10.0, help="Seconds to capture (default: 10)")
    p_net.add_argument("--block", default=None, help="Regex; matching requests are failed via the Fetch domain instead of allowed through")
    p_net.add_argument("outfile", nargs="?", default=None, help="Write JSON here instead of stdout")

    p_break = sub.add_parser("break", help="Set a breakpoint, reload to trigger it, report the paused stack + locals")
    p_break.add_argument("--timeout", type=float, default=15.0, help="Seconds to wait for the breakpoint to hit (default: 15)")
    p_break.add_argument("--url-pattern", default=None, help="Regex matching the script URL; requires --line")
    p_break.add_argument("--line", type=int, default=None, help="0-indexed line number; requires --url-pattern")
    p_break.add_argument("--column", type=int, default=0, help="0-indexed column number (default: 0)")
    p_break.add_argument("outfile", nargs="?", default=None, help="Write JSON here instead of stdout")

    p_storage = sub.add_parser("storage", help="Inspect localStorage/sessionStorage/IndexedDB for the current page")
    p_storage.add_argument("--max-records", type=int, default=20, help="Max records to sample per IndexedDB object store (default: 20)")
    p_storage.add_argument("--call-timeout", type=float, default=5.0, help="Seconds to wait per CDP call before giving up on that database (default: 5)")
    p_storage.add_argument("outfile", nargs="?", default=None, help="Write JSON here instead of stdout")

    p_qa = sub.add_parser("qa-run", help="Run a .tv-qa.yaml cartridge")
    p_qa.add_argument("cartridge_path")
    p_qa.add_argument("--run-dir", default=None)

    p_fuzz = sub.add_parser(
        "fuzz",
        help="Randomized D-pad navigation fuzz test — weighted-random keys, focus-health checked after every step",
    )
    p_fuzz.add_argument("app_id")
    p_fuzz.add_argument("--steps", type=int, default=100, help="Number of random keypresses (default: 100)")
    p_fuzz.add_argument("--seed", type=int, default=None, help="RNG seed for a reproducible run (default: current time)")
    p_fuzz.add_argument("--settle", type=float, default=0.35, help="Seconds to wait after each key before checking focus health (default: 0.35)")
    p_fuzz.add_argument("--start-path", default=None, help="Navigate here (a real page load) before fuzzing, instead of using the app's default landing screen")
    p_fuzz.add_argument("--warmup", type=float, default=3.0, help="Seconds to wait after --start-path navigation before the first keypress (default: 3.0)")
    p_fuzz.add_argument("--allow-no-focus", action="store_true", help="Don't flag zero focused elements as an anomaly — for screens with no D-pad content nav at all")
    p_fuzz.add_argument("outfile", nargs="?", default=None, help="Write the JSON report here instead of stdout")

    p_cast = sub.add_parser("screencast", help="Record smooth motion off the TV's current page via CDP screencast into a video (needs ffmpeg)")
    p_cast.add_argument("outfile", help="Output video path, e.g. nav.mp4 (.webm not supported here — screencast encodes H.264)")
    p_cast.add_argument("--duration", type=float, default=5.0, help="Seconds to record (default: 5)")
    p_cast.add_argument("--keys", default=None, help="Comma-separated D-pad keys to fire during capture, e.g. RIGHT,RIGHT,DOWN,ENTER")
    p_cast.add_argument("--key-delay", type=float, default=0.5, help="Seconds to wait before the first scripted key (default: 0.5)")
    p_cast.add_argument("--key-interval", type=float, default=0.6, help="Seconds between scripted keys (default: 0.6)")
    p_cast.add_argument("--fps", type=int, default=30, help="Constant output frame rate; capture timing is preserved regardless (default: 30)")
    p_cast.add_argument("--quality", type=int, default=80, help="JPEG quality of captured frames, 0-100 (default: 80)")
    p_cast.add_argument("--max-width", type=int, default=None, help="Cap captured frame width in px (preserves aspect); default is the page's own size")
    p_cast.add_argument("--keep-frames", default=None, help="Keep the raw JPEG frames in this directory instead of a temp dir")

    p_encode = sub.add_parser("encode", help="Encode a QA run's PNG frames into a browser-playable video (needs ffmpeg)")
    p_encode.add_argument("run_dir", help="A QA run directory of NN-label.png frames")
    p_encode.add_argument("--fps", type=float, default=1.5, help="Input frame rate; each still is held 1/fps seconds (default: 1.5)")
    p_encode.add_argument("--out", default="run.mp4", help="Output filename inside run_dir; .webm selects VP9, else H.264 MP4 (default: run.mp4)")

    p_serve = sub.add_parser("serve", help="Serve a QA run directory as a browser page over the LAN/tailnet")
    p_serve.add_argument("run_dir", help="A QA run directory to serve")
    p_serve.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    p_serve.add_argument("--host", default="0.0.0.0", help="Address to bind (default: 0.0.0.0 — all interfaces)")
    p_serve.add_argument("--fps", type=float, default=1.5, help="Frame rate for the auto-encoded video (default: 1.5)")
    p_serve.add_argument("--out", default="run.mp4", help="Video filename to encode/serve (default: run.mp4)")
    p_serve.add_argument("--no-encode", action="store_true", help="Don't auto-encode a video; serve whatever media already exists")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # These operate on a local run directory, not the TV — don't require a
        # config.json just to encode or view a run.
        if args.command == "encode":
            path = video.encode_run(args.run_dir, out_name=args.out, fps=args.fps)
            print(path)
            return 0
        elif args.command == "serve":
            if not args.no_encode and video.ffmpeg_available():
                out_path = os.path.join(args.run_dir, args.out)
                if not os.path.exists(out_path):
                    try:
                        video.encode_run(args.run_dir, out_name=args.out, fps=args.fps)
                    except VideoError as e:
                        print(f"tv-ctl: warning: could not encode video ({e}); serving frames only", file=sys.stderr)
            serve_mod.serve(args.run_dir, port=args.port, host=args.host)
            return 0

        cfg = config.load_config()

        if args.command == "wake":
            result = commands.wake(cfg)
        elif args.command == "power-state":
            result = commands.power_state(cfg)
        elif args.command == "send-key":
            result = commands.send_key(cfg, args.key)
        elif args.command == "launch":
            result = commands.launch(cfg, args.app_id)
        elif args.command == "input-status":
            result = commands.input_status(cfg)
        elif args.command == "get-volume":
            result = commands.get_volume(cfg)
        elif args.command == "set-volume":
            result = commands.set_volume(cfg, args.level)
        elif args.command == "get-setting":
            result = commands.get_setting(cfg, args.category, args.keys or None)
        elif args.command == "set-setting":
            result = commands.set_setting(cfg, args.category, args.key, args.value)
        elif args.command == "net-log":
            entries = commands.net_log(cfg, duration=args.duration, block_pattern=args.block)
            output = json.dumps(entries, indent=2)
            if args.outfile:
                with open(args.outfile, "w") as f:
                    f.write(output)
                print(args.outfile)
            else:
                print(output)
            return 0
        elif args.command == "break":
            if bool(args.url_pattern) != (args.line is not None):
                raise ValueError("--url-pattern and --line must be given together")
            result = commands.break_at(
                cfg,
                timeout=args.timeout,
                url_pattern=args.url_pattern,
                line=args.line,
                column=args.column,
            )
            output = json.dumps(result, indent=2)
            if args.outfile:
                with open(args.outfile, "w") as f:
                    f.write(output)
                print(args.outfile)
            else:
                print(output)
            return 0
        elif args.command == "storage":
            result = commands.storage(cfg, max_records=args.max_records, call_timeout=args.call_timeout)
            output = json.dumps(result, indent=2)
            if args.outfile:
                with open(args.outfile, "w") as f:
                    f.write(output)
                print(args.outfile)
            else:
                print(output)
            return 0
        elif args.command == "screenshot":
            outfile = args.outfile or f"/tmp/tv-screenshot-{datetime.now().strftime('%Y%m%dT%H%M%S')}.png"
            path = commands.screenshot(cfg, outfile)
            print(path)
            return 0
        elif args.command == "screencast":
            keys = [k.strip() for k in args.keys.split(",") if k.strip()] if args.keys else None
            path = screencast_mod.record_to_video(
                cfg, args.outfile,
                duration=args.duration, keys=keys, frames_dir=args.keep_frames, fps=args.fps,
                key_delay=args.key_delay, key_interval=args.key_interval,
                quality=args.quality, max_width=args.max_width,
            )
            print(path)
            return 0
        elif args.command == "qa-run":
            paths = qa.run_cartridge(cfg, args.cartridge_path, run_dir=args.run_dir)
            print(json.dumps(paths))
            return 0
        elif args.command == "fuzz":
            report = fuzz.fuzz(
                cfg, args.app_id, steps=args.steps, seed=args.seed, settle=args.settle,
                start_path=args.start_path, warmup=args.warmup, allow_no_focus=args.allow_no_focus,
            )
            output = json.dumps(report, indent=2)
            if args.outfile:
                with open(args.outfile, "w") as f:
                    f.write(output)
                print(args.outfile)
            else:
                print(output)
            return 0
        else:
            raise ValueError(f"unhandled command: {args.command}")

        print(json.dumps(result))
        return 0
    except (LunaError, CartridgeError, CdpError, VideoError, OSError, KeyError, ValueError) as e:
        message = str(e) or f"{type(e).__name__} (no further detail)"
        print(f"tv-ctl: error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
