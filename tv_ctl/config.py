import json
import os
from dataclasses import dataclass

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/tv-ctl/config.json")


@dataclass
class Config:
    host: str
    port: int
    ssh_key: str


def load_config(path: str | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"tv-ctl config not found at {path}. Create it with contents like: "
            '{"host": "<tv-ip>", "port": 22, "ssh_key": "~/.ssh/tv_webos"}'
        )
    with open(path) as f:
        data = json.load(f)
    return Config(
        host=data["host"],
        port=data.get("port", 22),
        ssh_key=os.path.expanduser(data["ssh_key"]),
    )
