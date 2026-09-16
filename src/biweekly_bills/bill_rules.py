from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BillRule:
    name: str
    cycle: str
    aliases: tuple[str, ...]


# Keep rules conservative. A transaction must match one of these aliases before
# the workbook updater will consider filling a Paid cell automatically.
BILL_RULES: tuple[BillRule, ...] = (
    BillRule("Verizon", "15th", ("verizon", "vzw")),
    BillRule("Cox", "15th", ("cox communications", "cox com", "cox")),
    BillRule("USAA", "15th", ("usaa",)),
    BillRule("Acellus Academy", "15th", ("acellus",)),
    BillRule("Star Card", "15th", ("military star", "star card")),
)
