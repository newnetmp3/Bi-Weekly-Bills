from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


APP_ID = "biweekly-bills"
APP_NAME = "Bi-Weekly Bills"
ICON_RESOURCE = "assets/icons/scalable/apps/biweekly-bills.svg"
LOGO_RESOURCE = "assets/branding/biweekly-bills-logo.svg"
DESKTOP_TEMPLATE_RESOURCE = "assets/desktop/biweekly-bills.desktop.in"


@dataclass(frozen=True)
class DesktopIntegrationResult:
    desktop_file: Path
    icon_file: Path
    logo_file: Path
    changed: bool


def _resource_bytes(relative_path: str) -> bytes:
    return files("biweekly_bills").joinpath(relative_path).read_bytes()


def _xdg_data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share"


def _desktop_exec_arg(value: str) -> str:
    if not value:
        return '""'
    needs_quotes = any(ch.isspace() or ch in '"\\$' for ch in value)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
    )
    return f'"{escaped}"' if needs_quotes else escaped


def _launcher_command(executable: str | None = None) -> tuple[str, str]:
    if executable:
        resolved = str(Path(executable).expanduser().resolve())
        return _desktop_exec_arg(resolved), resolved

    script = shutil.which("biweekly-bills-app")
    if script:
        resolved = str(Path(script).resolve())
        return _desktop_exec_arg(resolved), resolved

    python = str(Path(sys.executable).resolve())
    exec_line = (
        f"{_desktop_exec_arg(python)} -m biweekly_bills.desktop_app"
    )
    return exec_line, python


def _atomic_write(path: Path, payload: bytes, mode: int = 0o644) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_bytes() == payload:
                os.chmod(path, mode)
                return False
        except OSError:
            pass

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        return True
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _render_desktop_file(executable: str | None = None) -> bytes:
    exec_line, try_exec = _launcher_command(executable)
    template = _resource_bytes(DESKTOP_TEMPLATE_RESOURCE).decode("utf-8")
    rendered = (
        template.replace("@EXEC@", exec_line)
        .replace("@TRYEXEC@", _desktop_exec_arg(try_exec))
    )
    return rendered.encode("utf-8")


def _refresh_desktop_database(applications_dir: Path) -> None:
    helper = shutil.which("update-desktop-database")
    if not helper:
        return
    try:
        subprocess.run(
            [helper, str(applications_dir)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def install_desktop_integration(
    *,
    data_home: Path | None = None,
    executable: str | None = None,
    refresh_cache: bool = True,
) -> DesktopIntegrationResult:
    """Install the launcher and icon into the current user's XDG data tree."""

    data_home = Path(data_home) if data_home is not None else _xdg_data_home()
    applications_dir = data_home / "applications"
    icon_file = (
        data_home
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / f"{APP_ID}.svg"
    )
    logo_file = (
        data_home
        / APP_ID
        / "branding"
        / "biweekly-bills-logo.svg"
    )
    desktop_file = applications_dir / f"{APP_ID}.desktop"

    changed = False
    changed |= _atomic_write(icon_file, _resource_bytes(ICON_RESOURCE))
    changed |= _atomic_write(logo_file, _resource_bytes(LOGO_RESOURCE))
    changed |= _atomic_write(
        desktop_file,
        _render_desktop_file(executable),
    )

    if changed and refresh_cache:
        _refresh_desktop_database(applications_dir)

    return DesktopIntegrationResult(
        desktop_file=desktop_file,
        icon_file=icon_file,
        logo_file=logo_file,
        changed=changed,
    )


def ensure_desktop_integration() -> DesktopIntegrationResult:
    """Idempotently keep the current user's Linux desktop integration current."""
    return install_desktop_integration()
