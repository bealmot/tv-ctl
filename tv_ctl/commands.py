import subprocess

from . import cdp, luna
from .config import Config

REMOTE_SCREENSHOT_PATH = "/tmp/tv-ctl-screenshot.png"


def wake(cfg: Config) -> dict:
    return luna.call(cfg, "luna://com.webos.service.tvpower/power/turnOnScreen")


def power_state(cfg: Config) -> dict:
    return luna.call(cfg, "luna://com.webos.service.tvpower/power/getPowerState")


def send_key(cfg: Config, key: str) -> dict:
    return luna.call(cfg, "luna://com.webos.service.networkinput/sendSpecialKey", {"key": key})


def launch(cfg: Config, app_id: str) -> dict:
    return luna.call(cfg, "luna://com.webos.applicationManager/launch", {"id": app_id})


def close(cfg: Config, app_id: str) -> dict:
    return luna.call(cfg, "luna://com.webos.applicationManager/closeByAppId", {"id": app_id})


def input_status(cfg: Config) -> dict:
    return luna.call(cfg, "luna://com.webos.service.eim/getAllInputStatus")


def get_volume(cfg: Config) -> dict:
    """Current volume level + mute status, straight from the audio service —
    not inferred from remote-key presses."""
    return luna.call(cfg, "luna://com.webos.audio/getVolume")


def set_volume(cfg: Config, level: int) -> dict:
    """Set volume directly (0-100). Confirmed live against the real TV: this
    also clears mute as a side effect (muteStatus flips to false), and there
    is no separate setMute method on this service (verified — every
    plausible name returns "Unknown method"). set-volume 0 is this
    platform's real mute equivalent, not a workaround."""
    return luna.call(cfg, "luna://com.webos.audio/setVolume", {"volume": level})


def _coerce_setting_value(raw: str):
    """CLI setting values arrive as strings; the settings service expects
    the native JSON type it stored the key as (backlight is an int, some
    flags are bool). Best-effort coercion: int, then bool, then leave as the
    original string — never raises, since an unrecognized shape should still
    be sent through as-is rather than blocking the call."""
    try:
        return int(raw)
    except ValueError:
        pass
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


def get_setting(cfg: Config, category: str, keys: list[str] | None = None) -> dict:
    """Read TV settings for a category (e.g. "picture", "sound", "general") —
    the same store the on-screen settings menu reads/writes. `keys` narrows
    to specific keys; omitted, the service returns everything in that
    category."""
    payload: dict = {"category": category}
    if keys:
        payload["keys"] = keys
    return luna.call(cfg, "luna://com.webos.settingsservice/getSystemSettings", payload)


def set_setting(cfg: Config, category: str, key: str, value: str) -> dict:
    """Set a single TV setting directly — confirmed live (picture/backlight)
    to actually change the TV's on-screen state, not just report success."""
    return luna.call(
        cfg,
        "luna://com.webos.settingsservice/setSystemSettings",
        {"category": category, "settings": {key: _coerce_setting_value(value)}},
    )


def net_log(cfg: Config, duration: float = 10.0, block_pattern: str | None = None) -> list[dict]:
    """Capture request/response activity from the TV's current page for
    `duration` seconds over Chrome DevTools Protocol, tunneling over SSH.
    With `block_pattern` set, matching requests are actively failed via the
    Fetch domain rather than just observed."""
    return cdp.capture_network(cfg, duration=duration, block_pattern=block_pattern)


def break_at(
    cfg: Config,
    timeout: float = 15.0,
    url_pattern: str | None = None,
    line: int | None = None,
    column: int = 0,
) -> dict:
    """Set a CDP breakpoint, reload the page to trigger it, and report the
    paused call stack + top-frame locals. Without url_pattern/line, pauses on
    the page's first script execution instead of a specific line."""
    return cdp.capture_breakpoint(cfg, timeout=timeout, url_pattern=url_pattern, line=line, column=column)


def storage(cfg: Config, max_records: int = 20, call_timeout: float = 5.0) -> dict:
    """Read localStorage, sessionStorage, and IndexedDB (schema + a sample of
    records per object store) for the current page's origin."""
    return cdp.inspect_storage(cfg, max_records=max_records, call_timeout=call_timeout)


def screenshot(cfg: Config, local_path: str) -> str:
    luna.call(
        cfg,
        "luna://com.webos.service.capture/executeOneShot",
        {"path": REMOTE_SCREENSHOT_PATH, "format": "PNG"},
    )

    scp_cmd = [
        "scp",
        *luna.ssh_base_opts(cfg),
        "-P", str(cfg.port),
        f"root@{cfg.host}:{REMOTE_SCREENSHOT_PATH}",
        local_path,
    ]
    try:
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise luna.LunaError(f"scp pulling screenshot from {cfg.host} timed out after 15s")

    if result.returncode != 0:
        raise luna.LunaError(f"scp failed pulling screenshot: {result.stderr.strip()}")

    return local_path
