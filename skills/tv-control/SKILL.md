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

A `screenshot` proves a *state*. Some things you need to prove are *behaviors* —
a transition plays, focus glides between cards, a row scrolls with momentum, a
splash finishes loading, a trailer actually starts playing. One frame can't show
those. Reach for heavier evidence as the claim demands it:

1. **Snapshot** — `screenshot`. One PNG, one moment. The default.
2. **Snapshot sequence** — `qa-run` (see the `tv-qa` skill). One PNG per step —
   good for discrete before/after states along a scripted path.
3. **Motion clip** — `screencast`. Continuous frames at compositor rate; the
   only way to capture something transient or animated.

**Reviewing it as an agent:** always read the individual PNG/JPEG *frames*, never
the encoded clip — an image model sees only the first frame of an animated
GIF/WebP/MP4. So when the behavior is motion, run
`screencast … --keep-frames <dir>` and look at the frames it drops there. That
dense frame dump *is* the evidence you reason over — a mid-transition frame, a
dropped frame, a stutter shows up across them where one-shot-per-step would miss
it. The encoded `.mp4`/`.webp` is the *human's* copy, not yours.

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
