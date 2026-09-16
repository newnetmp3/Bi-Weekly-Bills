from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile


OPENFORMULA_NS = "urn:oasis:names:tc:opendocument:xmlns:of:1.2"
FORMULA_ATTR = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}formula"


def _formula_snapshot_from_bytes(content: bytes) -> tuple[str, ...]:
    root = ET.fromstring(content)
    return tuple(
        element.attrib[FORMULA_ATTR]
        for element in root.iter()
        if FORMULA_ATTR in element.attrib
    )


def _latest_pre_gui_backup(workbook_path: Path) -> Path | None:
    folder = workbook_path.parent / "backups"
    if not folder.is_dir():
        return None
    candidates = sorted(
        folder.glob(f"{workbook_path.stem}.*.pre-gui.bak.ods"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def repair_openformula_namespace(workbook_path: Path) -> tuple[Path | None, Path | None]:
    workbook_path = Path(workbook_path).expanduser().resolve()
    if workbook_path.suffix.casefold() != ".ods":
        raise ValueError("OpenFormula repair requires an .ods workbook.")
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    lock_file = workbook_path.parent / f".~lock.{workbook_path.name}#"
    if lock_file.exists():
        raise RuntimeError("Close the workbook in LibreOffice before repairing it.")

    with ZipFile(workbook_path, "r") as archive:
        content = archive.read("content.xml")

    if b"of:=" not in content:
        return None, None
    if b"xmlns:of=" in content:
        return None, _latest_pre_gui_backup(workbook_path)

    pre_gui_backup = _latest_pre_gui_backup(workbook_path)
    if pre_gui_backup is not None:
        with ZipFile(pre_gui_backup, "r") as archive:
            backup_content = archive.read("content.xml")
        if _formula_snapshot_from_bytes(content) != _formula_snapshot_from_bytes(backup_content):
            raise RuntimeError(
                "Current formula strings differ from the pre-GUI backup. "
                f"Do not patch this file in-place; restore {pre_gui_backup} instead."
            )

    match = re.search(br"<(?:[A-Za-z_][\w.-]*:)?document-content\b", content)
    if not match:
        raise RuntimeError("Could not locate the ODS document-content root element.")
    close = content.find(b">", match.end())
    if close < 0:
        raise RuntimeError("Could not locate the end of the ODS document-content start tag.")

    declaration = f' xmlns:of="{OPENFORMULA_NS}"'.encode("ascii")
    repaired_content = content[:close] + declaration + content[close:]

    backup_dir = workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    repair_backup = backup_dir / f"{workbook_path.stem}.{stamp}.pre-openformula-repair.bak.ods"
    shutil.copy2(workbook_path, repair_backup)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{workbook_path.name}.",
        suffix=".repair.tmp",
        dir=workbook_path.parent,
    )
    os.close(fd)
    try:
        with ZipFile(workbook_path, "r") as source, ZipFile(temp_name, "w") as target:
            for info in source.infolist():
                payload = repaired_content if info.filename == "content.xml" else source.read(info.filename)
                target.writestr(info, payload)
        Path(temp_name).replace(workbook_path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise

    with ZipFile(workbook_path, "r") as archive:
        check = archive.read("content.xml")
    if b"xmlns:of=" not in check:
        shutil.copy2(repair_backup, workbook_path)
        raise RuntimeError("OpenFormula repair validation failed; original file was restored.")

    return repair_backup, pre_gui_backup
