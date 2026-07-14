# tv-ctl

A CLI + Claude Code skills for remote-controlling and automating QA against a
**rooted LG webOS TV**. Wakes it, sends remote-control key presses, launches
apps, pulls screenshots, and drives Chrome DevTools Protocol (CDP) —
network requests, console logs, storage inspection — the same protocol
Chrome DevTools itself uses, available once a webOS TV is rooted.

Built out of developing an app for a living-room LG TV and getting tired of
guessing why the D-pad felt wrong. This lets an agent (or you, from a
terminal) actually watch the TV's own console and screen instead of
guessing.

**Not affiliated with LG or the webOS Homebrew community project. Rooting a
TV is done at your own risk** — read the "Rooting your TV" section before
doing anything.

## What's in this repo

- `tv_ctl/` — the Python CLI (`tv-ctl`): SSH-based remote control (wake,
  keys, launch, screenshot) plus a CDP client (Network, Debugger, Storage
  domains) and a YAML-driven QA runner.
- `skills/tv-control/`, `skills/tv-qa/` — Claude Code skills that wrap the
  CLI for agent-driven development and testing.
- `examples/qa/` — a sample QA cartridge.

## Prerequisites: rooting your TV

This tool needs SSH access to the TV, which webOS does not expose out of
the box. Getting there means installing the community-built **[webOS
Homebrew Channel](https://www.webosbrew.org/)** — an unofficial app store
for webOS that also unlocks developer access. The general process on
supported models:

1. Check your TV's webOS version and model against the Homebrew Channel
   project's compatibility list — support and the exact install method vary
   by firmware version, and this changes over time, so treat their docs as
   the source of truth, not this README.
2. Most current installs use their **RootMyTV** one-click tool (a USB-based
   installer) rather than the older manual IPK-sideloading route. Follow
   their current instructions exactly for your model/firmware.
3. Once Homebrew Channel is installed, install their **SSH** homebrew app
   (or enable SSH from Homebrew Channel's own settings, depending on
   version) to get a shell. Set an SSH key rather than relying on a
   password.
4. Rooting this way is a one-time step and, on the models it's designed
   for, avoids LG's normal Developer Mode account requirement (which
   otherwise needs an LG account and a ~50-hour session renewal that phones
   home to LG).

**Risk:** this is a community reverse-engineering project, not an
LG-sanctioned feature. It can affect your warranty. Read the Homebrew
Channel project's own safety notes for your specific model before doing
anything — this README is not a substitute for their docs, which are
model/firmware-specific and actively maintained.

## Blocking telemetry

LG webOS TVs collect viewing data (ACR — automatic content recognition) and
phone home extensively by default, independent of rooting. Rooting alone
doesn't stop this — you need to cut its network access:

**Option A — router-level LAN isolation (what this project uses).** On any
router/firewall that supports per-device rules (OPNsense, pfSense,
UniFi, most consumer routers with a "guest"/isolated VLAN feature): give
the TV a static IP or DHCP reservation, then block that IP/MAC from both
DNS resolution and outbound WAN traffic. The TV still works perfectly for
anything served from your own LAN (a media server, a homebrew app talking
to a local box); it just can't reach the internet at all. This is the most
complete block, but it also means webOS's own cloud features (Content
Store, LG account sign-in) stop working — which is exactly what pushed us
toward rooting via Homebrew Channel instead of using LG's official Developer
Mode, since that path requires periodic contact with LG's servers.

**Option B — DNS sinkhole only.** If you don't want full network isolation
(e.g. you still want casting or a smart-TV app that needs the internet),
run a DNS filter (Pi-hole, AdGuard Home) and block known LG
telemetry/ACR domains. Less complete than Option A — anything using
hardcoded IPs instead of DNS will still get through — but non-disruptive
to other functionality.

Either way, note this in your `tv-ctl` config: if the TV has no DNS/outbound
internet, use its plain LAN IP everywhere (this CLI already does, by
design) — don't rely on hostnames resolving from the TV's side.

## Setup

1. Install dependencies:
   ```
   pip install -e .
   ```
   On Arch (and other PEP 668 "externally-managed-environment" distros),
   this fails against system Python. Use a project-local venv instead:
   ```
   python -m venv .venv && source .venv/bin/activate && pip install -e .
   ```
   This venv is only needed for running the test suite (`pytest`) —
   `bin/tv-ctl` itself runs under `#!/usr/bin/env python3` (whatever
   `python3` resolves to on `PATH`), so it only needs `PyYAML` available to
   *that* interpreter. If your system Python already has PyYAML, `tv-ctl`
   works without activating the venv at all.
2. Create `~/.config/tv-ctl/config.json`:
   ```json
   {"host": "<your TV's LAN IP>", "port": 22, "ssh_key": "~/.ssh/tv_webos"}
   ```
3. Symlink the CLI: `ln -s $(pwd)/bin/tv-ctl ~/bin/tv-ctl`

## Usage

```
tv-ctl wake
tv-ctl power-state
tv-ctl send-key HOME
tv-ctl launch org.webosbrew.hbchannel
tv-ctl screenshot /tmp/tv.png
tv-ctl qa-run examples/qa/hbchannel-smoke.tv-qa.yaml
```

## Known gotchas

Rooted webOS is genuinely undocumented territory. Things we hit building
this:

- A cross-origin tab redirect inside the app under test can destabilize the
  whole app shell — the CDP session doesn't recover cleanly.
- IndexedDB delete calls can hang forever via CDP with no timeout on
  LG's side. Set your own timeout around anything that touches storage.
- `Target.createTarget` isn't supported — you can't open new CDP targets
  the way you would in desktop Chrome; work within the existing target.
- No mouse/cursor protocol is exposed over the wire we could find — this
  CLI only supports D-pad-style `send-key` navigation.

## License

MIT — see `LICENSE`.
