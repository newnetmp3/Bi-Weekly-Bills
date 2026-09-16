from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"
USER_CONFIG_DIR = Path.home() / ".config" / "bi-weekly-bills"
USER_ENV_PATH = USER_CONFIG_DIR / "plaid.env"


@dataclass(frozen=True)
class Settings:
    client_id: str
    secret: str
    environment: str
    redirect_uri: str | None
    host: str
    port: int


def _setting_value(
    key: str,
    *,
    default: str = "",
    project_values: dict | None = None,
    user_values: dict | None = None,
) -> str:
    if key in os.environ:
        return str(os.environ.get(key) or "").strip()

    if user_values is None:
        user_values = dotenv_values(USER_ENV_PATH)
    value = user_values.get(key)
    if value is not None:
        return str(value).strip()

    if project_values is None:
        project_values = dotenv_values(PROJECT_ENV_PATH)
    value = project_values.get(key)
    if value is not None:
        return str(value).strip()
    return default


def load_settings(*, require_keys: bool = True) -> Settings:
    project_values = dotenv_values(PROJECT_ENV_PATH)
    user_values = dotenv_values(USER_ENV_PATH)

    client_id = _setting_value(
        "PLAID_CLIENT_ID",
        project_values=project_values,
        user_values=user_values,
    )
    secret = _setting_value(
        "PLAID_SECRET",
        project_values=project_values,
        user_values=user_values,
    )
    environment = _setting_value(
        "PLAID_ENV",
        default="sandbox",
        project_values=project_values,
        user_values=user_values,
    ).lower()
    if environment not in {"sandbox", "production"}:
        raise ValueError("PLAID_ENV must be sandbox or production.")
    if require_keys and (not client_id or not secret):
        raise RuntimeError(
            "Plaid Client ID and Secret are required. "
            "Open Settings → Bank connection to configure them."
        )

    redirect_uri = (
        _setting_value(
            "PLAID_REDIRECT_URI",
            project_values=project_values,
            user_values=user_values,
        )
        or None
    )
    host = _setting_value(
        "PLAID_HOST",
        default="127.0.0.1",
        project_values=project_values,
        user_values=user_values,
    )
    port = int(
        _setting_value(
            "PLAID_PORT",
            default="8000",
            project_values=project_values,
            user_values=user_values,
        )
    )
    return Settings(
        client_id=client_id,
        secret=secret,
        environment=environment,
        redirect_uri=redirect_uri,
        host=host,
        port=port,
    )


def save_user_bank_settings(
    *,
    client_id: str | None = None,
    secret: str | None = None,
    redirect_uri: str | None = None,
    environment: str = "production",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> Path:
    """Persist local Plaid API settings without exposing them in SQLite.

    Blank values preserve already-configured credentials only within the same
    Plaid environment. A secret is never silently carried from one environment
    to another. Environment variables still take precedence over this file.
    """
    existing = load_settings(require_keys=False)
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError("environment must be sandbox or production")

    same_environment = existing.environment == env
    resolved_client_id = (
        str(client_id).strip()
        if client_id is not None and str(client_id).strip()
        else existing.client_id
    )
    resolved_secret = (
        str(secret).strip()
        if secret is not None and str(secret).strip()
        else (existing.secret if same_environment else "")
    )
    resolved_redirect = (
        str(redirect_uri).strip()
        if redirect_uri is not None
        else (existing.redirect_uri or "")
    )

    if not resolved_client_id or not resolved_secret:
        raise ValueError(
            "Plaid Client ID and Secret are required before connecting a bank."
        )

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(USER_CONFIG_DIR, 0o700)

    payload = (
        f"PLAID_CLIENT_ID={resolved_client_id}\n"
        f"PLAID_SECRET={resolved_secret}\n"
        f"PLAID_ENV={env}\n"
        f"PLAID_REDIRECT_URI={resolved_redirect}\n"
        f"PLAID_HOST={host.strip() or '127.0.0.1'}\n"
        f"PLAID_PORT={int(port)}\n"
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".plaid.env.",
        suffix=".tmp",
        dir=USER_CONFIG_DIR,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, USER_ENV_PATH)
        os.chmod(USER_ENV_PATH, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    return USER_ENV_PATH



def _user_ui_preferences_path() -> Path:
    return USER_CONFIG_DIR / "ui.json"


def load_user_ui_preferences() -> dict[str, object]:
    """Load non-sensitive desktop UI preferences.

    Invalid or partially-written preference files are ignored so presentation
    settings can never prevent the bills application from starting.
    """
    path = _user_ui_preferences_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_user_ui_preferences(
    *,
    sidebar_order: list[str],
    sidebar_labels: dict[str, str],
) -> Path:
    """Atomically persist editable sidebar order and labels."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(USER_CONFIG_DIR, 0o700)
    path = _user_ui_preferences_path()

    payload = {
        "version": 1,
        "sidebar_order": [str(value) for value in sidebar_order],
        "sidebar_labels": {
            str(key): str(value)
            for key, value in sidebar_labels.items()
            if str(value).strip()
        },
    }

    fd, temp_name = tempfile.mkstemp(
        prefix=".ui.json.",
        suffix=".tmp",
        dir=USER_CONFIG_DIR,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    return path
