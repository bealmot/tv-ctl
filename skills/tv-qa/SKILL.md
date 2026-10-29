---
name: tv-qa
description: Run automated QA against an app on a rooted webOS TV by walking a .tv-qa.yaml cartridge (key presses + screenshots). Use when asked to test/QA/verify a TV app after making changes.
---

# TV QA

Runs YAML-defined test sequences ("cartridges") against apps on a rooted
webOS TV, via `tv-ctl qa-run` (part of the `tv-control` skill's CLI — see
that skill for setup). This skill is a generic *runner*; it has no built-in
knowledge of any specific app. Each app owns its own cartridges.

## Cartridge convention

Look for `*.tv-qa.yaml` files under a top-level `qa/` directory in the app's
own repo — visible, not hidden, since cartridges are hand-authored test
definitions, not generated output. If none exist yet for an app you're
testing, write one following this shape:

```yaml
name: <short description>
appId: <webOS app id>
steps:
  - key: ENTER        # send a D-pad key
    label: <short-slug-for-the-screenshot-filename>
  - wait: 1.5         # pause N seconds
    label: settle
  - assert:           # health-probe the page; a mismatch fails the run
      focused_count: 1
      focused_text_contains: "Blade Runner"
    label: focus-landed
  - screencast:       # capture a motion clip inline (see below)
      duration: 4
      keys: [RIGHT, RIGHT, DOWN]
    label: browse-carousel
  - key: DOWN
    label: scroll-to-second-item
```

Every step needs a `label` and exactly one of `key`, `wait`, `assert`, or
`screencast`. `key`/`wait`/`assert` steps screenshot the settled screen after
running (`NN-label.png`); a `screencast` step instead produces a motion clip
(see below) as its artifact.

## Running a cartridge

```
tv-ctl qa-run path/to/some.tv-qa.yaml
```

This closes `appId` if it's currently running (a true cold start, not just
resuming a possibly-stuck instance), launches it fresh, walks the steps in
order, and screenshots after every step into a timestamped run directory
(`qa/runs/<timestamp>/` next to the cartridge, unless `--run-dir` is given —
gitignore this, results shouldn't be committed). It prints a JSON list of
the screenshot paths written. Every run starts from the same known state —
you don't need a HOME-key reset step in your own cartridge, the runner
handles it.

## After running

**Always look at every screenshot in the run** and report what you
observe — this skill deliberately does not do pixel-diffing or pass/fail
assertions; visual review of the sequence *is* the QA check. Compare
against a prior run's screenshots in the same `qa/runs/` directory if one
exists, to spot regressions.

Review the individual PNG frames, not a stitched video/animation — an image
model reads only the first frame of an animated GIF/WebP/MP4, so the per-step
PNGs are the source of truth for automated review.

To hand a *human* the whole run as one artifact, `tv-ctl encode <run-dir>
--out run.webp` (or `.mp4`) stitches the frames together, and `tv-ctl serve
<run-dir>` hosts them in a browser — see "Producing evidence" in the
`tv-control` skill for which to use where (inline `.webp` on desktop, `serve`
on mobile).

## When a step sequence isn't enough — motion evidence

A cartridge captures *discrete, settled states* — one frame per step, after the
screen stops moving — plus its `assert` checks. Keep it: that's your baseline
for state correctness. But settled stills are blind to anything that lives
*between* or *over* those states, and for that you **add** a `screencast`
alongside the run — not in place of it. It answers questions the cartridge
structurally can't:

- **Did an async transition complete?** A spinner clears, a skeleton fills, lazy
  rows stream in, a splash hands off to content — or it hangs halfway. (These are
  structural transitions, not just design animations.)
- **How long did it take?** Time from keypress to content, how long an animation
  or scroll runs, whether a load finishes within budget — motion as a stopwatch.
- **Did the motion look right?** Smooth vs janky vs skipped; focus that glides
  vs teleports.

The simplest way is a `screencast` **step in the cartridge itself**, captured
inline at the point in the sequence where the motion happens:

```yaml
  - screencast:
      duration: 4
      keys: [RIGHT, RIGHT, DOWN, ENTER]   # D-pad fired during capture; omit to record a passive load/animation
      # format: webp (default; needs Pillow) | mp4 | webm (need ffmpeg)
      # fps / quality / max_width / key_delay / key_interval also accepted
    label: browse-carousel
```

The step records over the run's own CDP connection (no second tool invocation)
and writes two things into the run dir: the encoded clip `NN-label.<fmt>` and a
`NN-label.frames/` directory holding every JPEG frame plus a `frames.json`
timing manifest. Review the **frames** the way you review cartridge PNGs — the
encoded clip is for a human, since vision reads only one frame of it — and use
`frames.json` as the stopwatch: `elapsed_ms` at the frame where content lands,
minus at the frame where you triggered it, is the measured latency.

For an ad-hoc capture outside a cartridge, the standalone `tv-ctl screencast`
command does the same thing (see the `tv-control` skill's "Producing evidence"
section). Gitignore the run dir as usual.
