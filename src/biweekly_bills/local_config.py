from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LEGACY_CONFIG_PATH = DATA_DIR / "local_config.json"


def config_path(environment: str) -> Path:
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")
    return DATA_DIR / f"local_config_{env}.json"


def _migrate_legacy_to_sandbox() -> None:
    target = config_path("sandbox")
    if target.exists() or not LEGACY_CONFIG_PATH.exists():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(LEGACY_CONFIG_PATH, target)


def load_local_config(environment: str) -> dict[str, Any]:
    if environment == "sandbox":
        _migrate_legacy_to_sandbox()
    path = config_path(environment)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_local_config(environment: str, data: dict[str, Any]) -> None:
    path = config_path(environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def update_local_config(environment: str, **values: Any) -> dict[str, Any]:
    data = load_local_config(environment)
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    save_local_config(environment, data)
    return data
