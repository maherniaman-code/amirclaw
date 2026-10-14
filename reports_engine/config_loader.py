"""Config and environment loader for Meydan Reports Automation."""

import os
from pathlib import Path
from typing import Any, Dict
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "meydan_rules.yaml"
ENV_PATH = BASE_DIR / ".env"


def load_env_file() -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    env_vars[key] = val
                    if key not in os.environ:
                        os.environ[key] = val
    return env_vars


def load_rules_config() -> Dict[str, Any]:
    load_env_file()
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Configuration file not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config


# Cache config
_cached_config = None


def get_config() -> Dict[str, Any]:
    global _cached_config
    if _cached_config is None:
        _cached_config = load_rules_config()
    return _cached_config
