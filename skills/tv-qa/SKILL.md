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
  - key: ENTER        # or: wait: <seconds>
    label: <short-slug-for-the-screenshot-filename>
  - wait: 1.5
    label: settle
  - key: DOWN
    label: scroll-to-second-item
```

Every step needs a `label` (used in the screenshot filename) and exactly one
of `key` or `wait`.

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
