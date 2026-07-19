---
name: tv-control
description: Remote-control a rooted LG webOS TV — wake it, read power state, send remote-control key presses, launch apps, take screenshots. Use when developing or testing any app on a rooted webOS TV, or when asked to control/check it.
---

# TV Control

Wraps `tv-ctl`, a CLI that remote-controls a rooted LG webOS TV over SSH.
Config lives at `~/.config/tv-ctl/config.json` — if `tv-ctl` reports it's
missing, create it with `{"host": "<tv-ip>", "port": 22, "ssh_key": "~/.ssh/tv_webos"}`
(see the repo README for how to get an IP and SSH key in the first place).

## Commands

- `tv-ctl wake` — wake the screen from standby/screensaver. Always run this
  before interacting with the TV if you don't know its current state; it's a
  no-op if the screen is already on.
- `tv-ctl power-state` — read current state (Active, Screen Saver, etc.).
- `tv-ctl send-key <KEY>` — send a remote key press. Valid keys: `HOME`,
  `UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER`, `BACK` (others may work but are
  unverified — if one fails, fall back to these).
- `tv-ctl launch <appId>` — launch or relaunch an app by id (e.g.
  `org.webosbrew.hbchannel`).
- `tv-ctl screenshot [outfile]` — capture the current screen and pull it
  locally. Prints the local file path. If you're an agent driving this,
  always look at the screenshot after capturing it — that's the whole
  point: self-verify what's on screen without a human needing to check
  physically.
- `tv-ctl input-status` — diagnostic: HDMI input state.
- `tv-ctl screencast <outfile> [--duration N] [--keys K,K,…] [--keep-frames DIR]`
  — record the current app's *motion* over CDP into a video (`.mp4`) or animated
  image (`.webp`), rather than a single frame. This is how you capture a
  behavior a still can't show — a transition, a focus glide, a list scrolling
  with momentum, a splash finishing loading, a video actually starting.
  `--keys RIGHT,DOWN,ENTER` drives the D-pad *while* recording so you catch the
  navigation as motion; without it, it records whatever happens in the window.
  `--keep-frames <dir>` also keeps every captured JPEG frame (see "Producing
  evidence" below for why that matters to an agent). Captures the app's web page
  content only — not native TV overlays — and needs an app already on screen.
- `tv-ctl encode <dir> [--out run.webp|run.mp4]` — stitch a directory of frames
  (a `qa-run` directory, or screencast `--keep-frames`) into one shareable
  animated file. `.webp` uses Pillow (no ffmpeg); `.mp4`/`.webm` use ffmpeg.
- `tv-ctl serve <dir> [--port 8080]` — host a run/frames directory as a browser
  page (an autoplaying clip plus the frame gallery) over the LAN/tailnet.

## Producing evidence (beyond a single snapshot)

Snapshots and motion clips prove different things, and they're **complementary —
motion *adds* to snapshots, it never replaces them.**

- **State correctness → snapshot** (`screenshot`, or a `qa-run` sequence of them)
  **plus the cartridge's `assert` health probe.** Is the screen right *now*:
  layout, focus target, text, no console error. A settled PNG is the sharpest,
  cheapest, most reliable tool for this — it waits for the screen to settle, so
  it's crisp and unambiguous. This stays the baseline; don't drop it.
- **Behavior over time → motion clip** (`screencast`). Continuous frames at
  compositor rate, for the questions a settled still literally cannot answer.
  Add it *alongside* snapshots when the claim is about time or motion, not a
  resting state. It's heavier to capture and review, so reach for it when the
  question demands it, not by default.

What a motion clip is for — and it's broader than design polish:

- **Async / structural state** — a spinner appears then clears, a skeleton fills
  in, lazy-loaded rows stream in, a splash hands off to content. Did it finish,
  or hang halfway? These are structural transitions, not animations.
- **Timing / latency (a stopwatch)** — how long from keypress to content
  appearing, how long an animation or scroll actually runs, whether a load
  completes within budget. `--keep-frames <dir>` writes a `frames.json` next to
  the JPEGs with each frame's `elapsed_ms` (and a top-level `span_ms`); find the
  frame where the thing starts and the frame where it lands and subtract their
  `elapsed_ms` — that's your measured duration, in milliseconds.
- **Motion quality** — a transition that's smooth vs janky vs skipped, focus
  that glides vs teleports.

**Reviewing it as an agent:** read the individual PNG/JPEG *frames* (and
`frames.json`), never the encoded clip — an image model sees only the first
frame of an animated GIF/WebP/MP4. The dense frame stream is what shows a
mid-transition glitch, a dropped frame, or exactly *when* content appeared;
`frames.json` turns that "when" into milliseconds. The encoded `.mp4`/`.webp` is
the *human's* copy, not yours.

**Showing it to a human (when they ask to see it):**
- **Desktop** — send the `.webp` inline; it animates on click in the desktop app.
- **Mobile** — `tv-ctl serve <dir>` and open it in a browser; inline animations
  are frozen to one frame there. Serves `.mp4` and `.webp` alike.

Encoded artifacts and run directories are throwaway — gitignore `qa/runs/` and
any frames dir; don't commit them.

## Notes

- Every command fails loud: non-zero exit + a `tv-ctl: error: ...` message on
  stderr on any real failure (SSH unreachable, TV rejected the call, etc.).
  Don't retry blindly — read the error.
- Mouse cursor control is not implemented (the wire protocol is unknown).
  Navigate with `send-key` only.
