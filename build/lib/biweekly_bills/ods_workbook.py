from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

TABLE = f"{{{NS['table']}}}table"
ROW = f"{{{NS['table']}}}table-row"
CELL = f"{{{NS['table']}}}table-cell"
COVERED_CELL = f"{{{NS['table']}}}covered-table-cell"
ROW_REPEAT = f"{{{NS['table']}}}number-rows-repeated"
COL_REPEAT = f"{{{NS['table']}}}number-columns-repeated"
TABLE_NAME = f"{{{NS['table']}}}name"
FORMULA = f"{{{NS['table']}}}formula"
VALUE_TYPE = f"{{{NS['office']}}}value-type"
VALUE = f"{{{NS['office']}}}value"
STRING_VALUE = f"{{{NS['office']}}}string-value"
DATE_VALUE = f"{{{NS['office']}}}date-value"
TIME_VALUE = f"{{{NS['office']}}}time-value"
BOOLEAN_VALUE = f"{{{NS['office']}}}boolean-value"
CURRENCY = f"{{{NS['office']}}}currency"
CALCEXT_VALUE_TYPE = f"{{{NS['calcext']}}}value-type"
TEXT_P = f"{{{NS['text']}}}p"

CELL_RE = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")


def column_index(column: str) -> int:
    value = 0
    for char in column.upper():
        if not "A" <= char <= "Z":
            raise ValueError(f"Invalid column: {column}")
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def parse_cell_ref(cell_ref: str) -> tuple[int, int]:
    match = CELL_RE.fullmatch(cell_ref.strip())
    if not match:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    return int(match.group(2)), column_index(match.group(1))


def _repeat(element: ET.Element, attr: str) -> int:
    raw = element.get(attr)
    return int(raw) if raw else 1


def _split_repeated(
    parent: ET.Element,
    element: ET.Element,
    *,
    repeat_attr: str,
    offset: int,
    total: int,
) -> ET.Element:
    if total <= 1:
        return element

    children = list(parent)
    position = children.index(element)
    replacements: list[ET.Element] = []

    if offset:
        before = deepcopy(element)
        before.set(repeat_attr, str(offset))
        replacements.append(before)

    target = deepcopy(element)
    target.attrib.pop(repeat_attr, None)
    replacements.append(target)

    after_count = total - offset - 1
    if after_count:
        after = deepcopy(element)
        after.set(repeat_attr, str(after_count))
        replacements.append(after)

    parent.remove(element)
    for index, replacement in enumerate(replacements):
        parent.insert(position + index, replacement)
    return target


class ODSWorkbook:
    """Minimal in-place ODS reader/editor that preserves the rest of the package."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.suffix.lower() != ".ods":
            raise ValueError("Bi-Weekly Bills requires an .ods workbook.")
        with ZipFile(self.path, "r") as archive:
            try:
                content = archive.read("content.xml")
            except KeyError as exc:
                raise ValueError(f"{self.path} is not a valid ODS package: content.xml is missing.") from exc
        self.root = ET.fromstring(content)
        spreadsheet = self.root.find(".//office:spreadsheet", NS)
        if spreadsheet is None:
            raise ValueError(f"{self.path} does not contain an ODS spreadsheet.")
        self.spreadsheet = spreadsheet

    @property
    def sheet_names(self) -> list[str]:
        return [table.get(TABLE_NAME, "") for table in self.spreadsheet.findall("table:table", NS)]

    def _table(self, sheet_name: str) -> ET.Element:
        for table in self.spreadsheet.findall("table:table", NS):
            if table.get(TABLE_NAME) == sheet_name:
                return table
        raise KeyError(f"Workbook has no {sheet_name!r} sheet.")

    def _iter_rows(self, parent: ET.Element):
        for child in list(parent):
            if child.tag == ROW:
                yield parent, child
            elif child.tag in {
                f"{{{NS['table']}}}table-header-rows",
                f"{{{NS['table']}}}table-row-group",
                f"{{{NS['table']}}}table-rows",
            }:
                yield from self._iter_rows(child)

    def _row(self, sheet_name: str, row_number: int, *, for_write: bool) -> ET.Element | None:
        table = self._table(sheet_name)
        logical_row = 1
        for parent, row in self._iter_rows(table):
            repeated = _repeat(row, ROW_REPEAT)
            if logical_row <= row_number < logical_row + repeated:
                if for_write and repeated > 1:
                    return _split_repeated(
                        parent,
                        row,
                        repeat_attr=ROW_REPEAT,
                        offset=row_number - logical_row,
                        total=repeated,
                    )
                return row
            logical_row += repeated
        return None

    def _cell(self, row: ET.Element, column_number: int, *, for_write: bool) -> ET.Element | None:
        logical_col = 1
        for cell in [child for child in list(row) if child.tag in {CELL, COVERED_CELL}]:
            repeated = _repeat(cell, COL_REPEAT)
            if logical_col <= column_number < logical_col + repeated:
                if for_write and repeated > 1:
                    return _split_repeated(
                        row,
                        cell,
                        repeat_attr=COL_REPEAT,
                        offset=column_number - logical_col,
                        total=repeated,
                    )
                return cell
            logical_col += repeated

        if not for_write:
            return None

        gap = column_number - logical_col
        if gap > 0:
            filler = ET.Element(CELL)
            filler.set(COL_REPEAT, str(gap))
            row.append(filler)
        target = ET.Element(CELL)
        row.append(target)
        return target

    def get_cell_value(self, sheet_name: str, cell_ref: str):
        row_number, column_number = parse_cell_ref(cell_ref)
        row = self._row(sheet_name, row_number, for_write=False)
        if row is None:
            return None
        cell = self._cell(row, column_number, for_write=False)
        if cell is None or cell.tag == COVERED_CELL:
            return None

        value_type = cell.get(VALUE_TYPE)
        if value_type in {"float", "currency", "percentage"}:
            raw = cell.get(VALUE)
            if raw is None:
                return None
            try:
                return float(raw)
            except ValueError:
                return raw
        if value_type == "boolean":
            return cell.get(BOOLEAN_VALUE) == "true"
        if value_type == "date":
            return cell.get(DATE_VALUE)
        if value_type == "time":
            return cell.get(TIME_VALUE)
        if value_type == "string" and cell.get(STRING_VALUE) is not None:
            return cell.get(STRING_VALUE)

        text = "".join(cell.itertext()).strip()
        return text if text else None

    def set_number(self, sheet_name: str, cell_ref: str, value: float) -> None:
        row_number, column_number = parse_cell_ref(cell_ref)
        row = self._row(sheet_name, row_number, for_write=True)
        if row is None:
            raise IndexError(f"{sheet_name}!{cell_ref} is beyond the existing ODS row range.")
        cell = self._cell(row, column_number, for_write=True)
        if cell is None or cell.tag == COVERED_CELL:
            raise ValueError(f"{sheet_name}!{cell_ref} is a covered/merged cell and cannot be written.")

        for attr in (
            FORMULA,
            VALUE,
            STRING_VALUE,
            DATE_VALUE,
            TIME_VALUE,
            BOOLEAN_VALUE,
            CURRENCY,
            VALUE_TYPE,
            CALCEXT_VALUE_TYPE,
        ):
            cell.attrib.pop(attr, None)

        numeric = float(value)
        cell.set(VALUE_TYPE, "float")
        cell.set(CALCEXT_VALUE_TYPE, "float")
        cell.set(VALUE, format(numeric, ".15g"))

        for child in list(cell):
            if child.tag == TEXT_P:
                cell.remove(child)
        paragraph = ET.Element(TEXT_P)
        paragraph.text = format(numeric, ".15g")
        cell.append(paragraph)

    def save(self) -> None:
        # OpenFormula prefixes such as "of:=" live inside attribute VALUES.
        # ElementTree does not consider those values namespace usage and would
        # otherwise drop xmlns:of while serializing, causing LibreOffice Err:510.
        self.root.set("xmlns:of", NS["of"])
        xml = ET.tostring(self.root, encoding="utf-8", xml_declaration=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        os.close(fd)
        try:
            with ZipFile(self.path, "r") as source, ZipFile(temp_name, "w") as target:
                for info in source.infolist():
                    payload = xml if info.filename == "content.xml" else source.read(info.filename)
                    target.writestr(info, payload)
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
