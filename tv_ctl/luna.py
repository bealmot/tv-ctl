import json
import shlex
import subprocess

from .config import Config


class LunaError(Exception):
    pass


def ssh_base_opts(cfg: Config) -> list[str]:
    """Common SSH/SCP connection options shared by luna.call() and commands.screenshot()."""
    return [
        "-i", cfg.ssh_key,
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]


def call(cfg: Config, uri: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    """Call a Luna Service Bus method on the TV over SSH and return the parsed JSON response.

    Uses `ssh -tt` because plain non-interactive SSH silently swallows all
    luna-send output — luna-send requires a real TTY to print anything.
    """
    payload_json = json.dumps(payload or {})
    remote_cmd = f"luna-send -n 1 -f {uri} {shlex.quote(payload_json)}"
    ssh_cmd = [
        "ssh", "-tt",
        *ssh_base_opts(cfg),
        "-p", str(cfg.port),
        f"root@{cfg.host}",
        remote_cmd,
    ]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise LunaError(f"ssh call to {cfg.host} timed out after {timeout}s (uri={uri})")

    if result.returncode != 0:
        raise LunaError(
            f"ssh command failed (exit {result.returncode}) for uri={uri}: {result.stderr.strip()}"
        )

    output = result.stdout.strip()
    start = output.find("{")
    if start == -1:
        raise LunaError(f"no JSON found in luna-send output for uri={uri}: {output!r}")

    json_text = output[start:]
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise LunaError(f"failed to parse luna-send JSON for uri={uri}: {e}\nraw: {json_text!r}")

    if data.get("returnValue") is False:
        raise LunaError(f"luna-send call failed for uri={uri}: {data.get('errorText', data)}")

    return data
