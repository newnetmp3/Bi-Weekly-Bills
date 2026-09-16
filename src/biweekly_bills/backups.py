from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from .database import Database, SCHEMA_VERSION


_SAFE_REASON = re.compile(r"[^a-z0-9_-]+")
_BACKUP_RE = re.compile(
    r"^(?P<stamp>\d{8}T\d{6}\d{6}Z)__(?P<reason>.+)\.sqlite3$"
)


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: datetime
    reason: str
    size_bytes: int
    integrity_ok: bool | None


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_reason(reason: str) -> str:
    cleaned = _SAFE_REASON.sub("-", reason.strip().casefold()).strip("-")
    return cleaned or "backup"


def _parse_backup(
    path: Path,
    *,
    verify: bool = True,
    integrity_ok: bool | None = None,
) -> BackupInfo:
    match = _BACKUP_RE.match(path.name)
    if match:
        stamp = datetime.strptime(
            match.group("stamp"), "%Y%m%dT%H%M%S%fZ"
        ).replace(tzinfo=timezone.utc)
        reason = match.group("reason").replace("-", " ")
    else:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        reason = "backup"

    if integrity_ok is None and verify:
        integrity_ok = validate_sqlite(path)

    return BackupInfo(
        path=path,
        created_at=stamp,
        reason=reason,
        size_bytes=path.stat().st_size,
        integrity_ok=integrity_ok,
    )


def validate_sqlite(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row and str(row[0]).casefold() == "ok")
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def database_schema_version(path: Path) -> int | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


class BackupManager:
    """Verified SQLite snapshot and restore manager.

    Only the application SQLite database is copied. Plaid credentials remain in
    secure_store and are intentionally outside this backup system.
    """

    def __init__(
        self,
        database: Database,
        *,
        backup_dir: Path | str | None = None,
        retention: int = 30,
    ):
        self.database = database
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir is not None
            else database.path.parent / "backups"
        )
        self.retention = max(int(retention), 1)
        self._integrity_cache: dict[
            tuple[str, int, int], bool
        ] = {}

    @staticmethod
    def _cache_key(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (
            str(path.resolve()),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )

    def _remember_integrity(self, path: Path, result: bool) -> None:
        self._integrity_cache[self._cache_key(path)] = bool(result)

    def _cached_integrity(self, path: Path) -> bool | None:
        try:
            return self._integrity_cache.get(self._cache_key(path))
        except OSError:
            return None

    def create_backup(self, reason: str, *, prune: bool = True) -> BackupInfo:
        if not self.database.path.exists():
            raise ValueError("The SQLite database does not exist yet.")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = _safe_reason(reason)
        final_path = self.backup_dir / f"{_utc_stamp()}__{safe_reason}.sqlite3"
        temp_path = final_path.with_suffix(".sqlite3.tmp")

        source = sqlite3.connect(self.database.path)
        destination = sqlite3.connect(temp_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        if not validate_sqlite(temp_path):
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("Backup integrity verification failed.")

        os.replace(temp_path, final_path)
        self._remember_integrity(final_path, True)
        info = _parse_backup(
            final_path,
            verify=False,
            integrity_ok=True,
        )

        if prune:
            self.prune()

        return info

    def list_backups(
        self,
        *,
        verify: bool = True,
    ) -> list[BackupInfo]:
        if not self.backup_dir.exists():
            return []

        backups: list[BackupInfo] = []
        for path in self.backup_dir.glob("*.sqlite3"):
            try:
                cached = self._cached_integrity(path)
                if verify and cached is None:
                    cached = validate_sqlite(path)
                    self._remember_integrity(path, cached)
                backups.append(
                    _parse_backup(
                        path,
                        verify=False,
                        integrity_ok=cached,
                    )
                )
            except (OSError, ValueError):
                continue
        backups.sort(key=lambda item: item.created_at, reverse=True)
        return backups

    def prune(self) -> None:
        if not self.backup_dir.exists():
            return

        # Filenames begin with a fixed-width UTC timestamp, so lexical order is
        # chronological. Retention therefore does not need to open/integrity-
        # check every historical snapshot on each automatic pre-edit backup.
        paths = sorted(
            self.backup_dir.glob("*.sqlite3"),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in paths[self.retention :]:
            path.unlink(missing_ok=True)

    def restore(self, backup_path: Path | str) -> tuple[BackupInfo, BackupInfo]:
        backup_path = Path(backup_path)
        try:
            backup_path.resolve().relative_to(self.backup_dir.resolve())
        except ValueError as exc:
            raise ValueError("Selected backup is outside the managed backup directory.") from exc

        if not validate_sqlite(backup_path):
            self._remember_integrity(backup_path, False)
            raise ValueError("Selected backup failed SQLite integrity verification.")
        self._remember_integrity(backup_path, True)

        safety = self.create_backup("pre-restore-safety", prune=False)

        source = sqlite3.connect(backup_path)
        destination = sqlite3.connect(self.database.path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        if not validate_sqlite(self.database.path):
            # Best-effort rollback to the safety snapshot before surfacing the
            # restore error.
            rollback_source = sqlite3.connect(safety.path)
            rollback_destination = sqlite3.connect(self.database.path)
            try:
                rollback_source.backup(rollback_destination)
            finally:
                rollback_destination.close()
                rollback_source.close()
            raise RuntimeError("Restored database failed integrity verification; safety backup restored.")

        self.database.initialize()
        restored = _parse_backup(
            backup_path,
            verify=False,
            integrity_ok=True,
        )
        self.prune()
        return restored, safety

    def backup_before_schema_migration(self) -> BackupInfo | None:
        if not self.database.path.exists() or self.database.path.stat().st_size <= 0:
            return None

        version = database_schema_version(self.database.path)
        if version is None or version < SCHEMA_VERSION:
            return self.create_backup("pre-schema-migration")
        return None
