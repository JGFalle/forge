from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
_config: dict | None = None


def load_config() -> dict:
    global _config
    if _config is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def get(key_path: str, default=None):
    """Dot-separated key access. e.g. get('person.email')"""
    parts = key_path.split(".")
    node = load_config()
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
