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

## Notes

- Every command fails loud: non-zero exit + a `tv-ctl: error: ...` message on
  stderr on any real failure (SSH unreachable, TV rejected the call, etc.).
  Don't retry blindly — read the error.
- Mouse cursor control is not implemented (the wire protocol is unknown).
  Navigate with `send-key` only.
