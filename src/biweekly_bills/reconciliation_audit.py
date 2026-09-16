from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .auto_reconcile import (
    account_routed_paid_evidence,
    assess_candidate,
    bill_identity_score,
    expected_payment_cents,
)
from .bank_data import normalize
from .database import Database
from .reconciliation import transaction_date


@dataclass(frozen=True)
class AuditEntry:
    bill_instance_id: int
    year: int
    month: int
    cycle: str
    bill_name: str
    due_cents: int | None
    paid_cents: int | None
    state: str
    reason: str
    candidate_transaction_id: str | None = None
    candidate_description: str | None = None
    candidate_account: str | None = None
    candidate_date: str | None = None
    candidate_amount_cents: int | None = None


@dataclass(frozen=True)
class DiagnosticIssue:
    severity: str
    code: str
    subject: str
    detail: str
    period: str | None = None


def _is_paid(instance: Any) -> bool:
    return (
        bool(int(instance["manually_paid"] or 0))
        or int(instance["paid_cents"] or 0) > 0
        or normalize(str(instance["status"] or "")) == "paid"
    )


def _description(row: Any) -> str:
    return str(row["merchant_name"] or row["name"] or "(no description)")


def _account_label(row: Any) -> str:
    label = str(row["account_name"] or "(unknown account)")
    mask = str(row["account_mask"] or "")
    if mask:
        label += f" ••••{mask}"
    return label


def _candidate_rank(assessment) -> float:
    if assessment.expected_cents is None:
        amount_score = 0.0
    else:
        gap = abs(assessment.actual_cents - assessment.expected_cents)
        amount_score = max(
            0.0,
            1.0 - (gap / max(assessment.expected_cents, 100)),
        )
    descriptive_rank = (
        assessment.name_score * 0.65
        + amount_score * 0.35
    )
    return max(descriptive_rank, assessment.confidence)


def _best_direct_candidates(
    database: Database,
    instance: Any,
    rows: list[Any],
) -> list[tuple[float, Any, Any]]:
    candidates: list[tuple[float, Any, Any]] = []
    for row in rows:
        if int(row["pending"] or 0) or int(row["amount_cents"]) <= 0:
            continue
        assessment = assess_candidate(database, row, instance)
        if assessment.expected_cents is None:
            continue
        if (
            assessment.name_score < 0.55
            and not account_routed_paid_evidence(row, instance)
        ):
            continue
        candidates.append((_candidate_rank(assessment), row, assessment))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _credit_payment_evidence(
    database: Database,
    instance: Any,
    rows: list[Any],
) -> tuple[str | None, Any | None]:
    expected = expected_payment_cents(instance)
    payment_account_id = str(instance["payment_account_id"] or "")
    if expected is None or not payment_account_id:
        return None, None

    destinations: list[Any] = []
    sources = [
        row
        for row in rows
        if not int(row["pending"] or 0)
        and int(row["amount_cents"]) > 0
        and str(row["plaid_account_id"] or "") == payment_account_id
    ]

    for row in rows:
        if int(row["pending"] or 0) or int(row["amount_cents"]) >= 0:
            continue
        if normalize(str(row["account_type"] or "")) != "credit":
            continue
        if not any(
            normalize(str(value or "")) == "nfo payment received"
            for value in (row["merchant_name"], row["name"])
        ):
            continue
        if bill_identity_score(
            database,
            instance,
            str(row["account_name"] or ""),
        ) < 0.72:
            continue
        if abs(abs(int(row["amount_cents"])) - expected) > max(
            100,
            min(500, int(round(expected * 0.02))),
        ):
            continue
        destinations.append(row)

    if not destinations:
        return None, None

    pairs: list[tuple[Any, Any]] = []
    for destination in destinations:
        destination_date = transaction_date(destination)
        if destination_date is None:
            continue
        amount = abs(int(destination["amount_cents"]))
        for source in sources:
            if int(source["amount_cents"]) != amount:
                continue
            source_date = transaction_date(source)
            if source_date is None:
                continue
            if abs((source_date - destination_date).days) <= 3:
                pairs.append((source, destination))

    if len(pairs) == 1:
        source, destination = pairs[0]
        return (
            "A unique credit-account payment pair is present "
            "(configured source, exact amount, NFO payment receipt, and date window).",
            source,
        )
    if len(pairs) > 1:
        return (
            "Multiple source transfers could pair with the NFO payment receipt; "
            "manual review is required.",
            pairs[0][0],
        )
    return (
        "NFO PAYMENT RECEIVED is present on the matching credit account, "
        "but no unique configured-source transfer was found within three days.",
        destinations[0],
    )


def build_reconciliation_audit(
    database: Database,
    environment: str,
    *,
    today: date | None = None,
) -> list[AuditEntry]:
    current = today or date.today()
    transactions = database.list_bank_transactions(environment, limit=100000)

    by_period: dict[tuple[int, int], list[Any]] = {}
    for row in transactions:
        tx_date = transaction_date(row)
        if tx_date is None:
            continue
        by_period.setdefault((tx_date.year, tx_date.month), []).append(row)

    entries: list[AuditEntry] = []
    for year in database.available_years():
        for month in range(1, 13):
            if (year, month) >= (current.year, current.month):
                continue
            instances = database.list_month_instances(
                year,
                month,
                active_only=False,
            )
            if not instances:
                continue

            period_rows = by_period.get((year, month), [])
            for instance in instances:
                bill_name = str(instance["bill_name_snapshot"])
                base = dict(
                    bill_instance_id=int(instance["id"]),
                    year=year,
                    month=month,
                    cycle=str(instance["cycle"]),
                    bill_name=bill_name,
                    due_cents=(
                        None
                        if instance["due_cents"] is None
                        else int(instance["due_cents"])
                    ),
                    paid_cents=(
                        None
                        if instance["paid_cents"] is None
                        else int(instance["paid_cents"])
                    ),
                )

                if int(instance["bank_verified"] or 0):
                    description = str(
                        instance["bank_verified_description"]
                        or "posted bank transaction"
                    )
                    verified_date = str(
                        instance["bank_verified_date"] or "unknown date"
                    )
                    account = str(
                        instance["bank_verified_account_name"]
                        or "linked account"
                    )
                    mask = str(instance["bank_verified_account_mask"] or "")
                    if mask:
                        account += f" ••••{mask}"

                    evidence = instance["bank_evidence_account_name"]
                    if evidence:
                        evidence_label = str(evidence)
                        evidence_mask = str(
                            instance["bank_evidence_account_mask"] or ""
                        )
                        if evidence_mask:
                            evidence_label += f" ••••{evidence_mask}"
                        reason = (
                            f"Verified by paired internal payment: {description} "
                            f"on {verified_date} from {account}, with matching "
                            f"receipt evidence on {evidence_label}."
                        )
                    else:
                        reason = (
                            f"Verified by {description} on {verified_date} "
                            f"from {account}."
                        )
                    entries.append(
                        AuditEntry(
                            **base,
                            state="Verified",
                            reason=reason,
                            candidate_transaction_id=str(
                                instance["bank_transaction_id"] or ""
                            ) or None,
                            candidate_description=description,
                            candidate_account=account,
                            candidate_date=verified_date,
                            candidate_amount_cents=(
                                None
                                if instance["paid_cents"] is None
                                else int(instance["paid_cents"])
                            ),
                        )
                    )
                    continue

                paid = _is_paid(instance)
                direct = _best_direct_candidates(
                    database,
                    instance,
                    period_rows,
                )
                credit_reason, credit_row = _credit_payment_evidence(
                    database,
                    instance,
                    period_rows,
                )

                if credit_reason:
                    row = credit_row
                    entries.append(
                        AuditEntry(
                            **base,
                            state="Needs review",
                            reason=credit_reason,
                            candidate_transaction_id=(
                                None
                                if row is None
                                else str(row["plaid_transaction_id"])
                            ),
                            candidate_description=(
                                None if row is None else _description(row)
                            ),
                            candidate_account=(
                                None if row is None else _account_label(row)
                            ),
                            candidate_date=(
                                None
                                if row is None
                                else str(
                                    row["posted_date"]
                                    or row["authorized_date"]
                                    or ""
                                )
                            ),
                            candidate_amount_cents=(
                                None
                                if row is None
                                else abs(int(row["amount_cents"]))
                            ),
                        )
                    )
                    continue

                if direct:
                    best_rank, row, assessment = direct[0]
                    ambiguous = (
                        len(direct) > 1
                        and best_rank - direct[1][0] < 0.08
                    )
                    disposition = row["reconciliation_disposition"]
                    if disposition == "matched":
                        reason = (
                            f"Closest bank transaction is already matched to "
                            f"{row['reconciled_bill_name']}; {assessment.reason}."
                        )
                    elif disposition == "ignored":
                        reason = (
                            f"Closest bank transaction is marked ignored; "
                            f"{assessment.reason}."
                        )
                    elif ambiguous:
                        reason = (
                            "Multiple bank transactions are similarly plausible; "
                            "manual review is required."
                        )
                    elif (
                        assessment.accepted
                        and account_routed_paid_evidence(row, instance)
                    ):
                        reason = (
                            "A posted outgoing transaction from the configured "
                            "Payment Account matches the recorded Paid amount; "
                            "run Reconcile history to commit the verification."
                        )
                    elif assessment.accepted:
                        reason = (
                            "A high-confidence posted transaction is available; "
                            "run Reconcile history to commit the match."
                        )
                    else:
                        reason = assessment.reason

                    entries.append(
                        AuditEntry(
                            **base,
                            state="Needs review",
                            reason=reason,
                            candidate_transaction_id=str(
                                row["plaid_transaction_id"]
                            ),
                            candidate_description=_description(row),
                            candidate_account=_account_label(row),
                            candidate_date=str(
                                row["posted_date"]
                                or row["authorized_date"]
                                or ""
                            ),
                            candidate_amount_cents=abs(
                                int(row["amount_cents"])
                            ),
                        )
                    )
                    continue

                if paid:
                    entries.append(
                        AuditEntry(
                            **base,
                            state="Paid · unverified",
                            reason=(
                                "Paid is recorded, but no sufficiently similar "
                                "posted bank transaction and no qualifying "
                                "outgoing transaction from the configured "
                                "Payment Account is stored for this month."
                            ),
                        )
                    )
                else:
                    entries.append(
                        AuditEntry(
                            **base,
                            state="Unpaid",
                            reason=(
                                "No manual Paid checkpoint, recorded payment, "
                                "or bank reconciliation exists."
                            ),
                        )
                    )

    return sorted(
        entries,
        key=lambda item: (
            -item.year,
            -item.month,
            0 if item.cycle == "1st" else 1,
            item.bill_name.casefold(),
        ),
    )


def run_integrity_diagnostics(
    database: Database,
    environment: str,
    *,
    today: date | None = None,
    audit_entries: list[AuditEntry] | None = None,
) -> list[DiagnosticIssue]:
    issues: list[DiagnosticIssue] = []

    with database.connection() as conn:
        missing_transactions = conn.execute(
            """
            SELECT tr.plaid_transaction_id, bi.bill_name_snapshot,
                   pp.year, pp.month
            FROM transaction_reconciliations tr
            LEFT JOIN bank_transactions bt
              ON bt.environment=tr.environment
             AND bt.plaid_transaction_id=tr.plaid_transaction_id
            LEFT JOIN bill_instances bi ON bi.id=tr.bill_instance_id
            LEFT JOIN pay_periods pp ON pp.id=bi.pay_period_id
            WHERE tr.environment=?
              AND tr.disposition='matched'
              AND bt.id IS NULL
            """,
            (environment,),
        ).fetchall()
        for row in missing_transactions:
            issues.append(
                DiagnosticIssue(
                    "error",
                    "missing-bank-transaction",
                    str(row["bill_name_snapshot"] or "Reconciliation"),
                    (
                        "A matched reconciliation points to a bank transaction "
                        "that is no longer stored."
                    ),
                    (
                        None
                        if row["year"] is None
                        else f"{int(row['year']):04d}-{int(row['month']):02d}"
                    ),
                )
            )

        missing_accounts = conn.execute(
            """
            SELECT b.name, b.payment_account_id
            FROM bills b
            LEFT JOIN bank_accounts ba
              ON ba.environment='production'
             AND ba.plaid_account_id=b.payment_account_id
            WHERE b.active=1
              AND b.payment_account_id IS NOT NULL
              AND ba.id IS NULL
            """
        ).fetchall()
        for row in missing_accounts:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "missing-payment-account",
                    str(row["name"]),
                    (
                        "The configured Payment Account is not present in the "
                        "current linked-account cache."
                    ),
                )
            )

        duplicate_aliases = conn.execute(
            """
            SELECT ba.alias, COUNT(DISTINCT ba.bill_id) AS bill_count,
                   GROUP_CONCAT(b.name, ', ') AS bill_names
            FROM bill_aliases ba
            JOIN bills b ON b.id=ba.bill_id
            WHERE b.active=1
            GROUP BY LOWER(ba.alias)
            HAVING COUNT(DISTINCT ba.bill_id) > 1
            """
        ).fetchall()
        for row in duplicate_aliases:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "duplicate-merchant-alias",
                    str(row["alias"]),
                    (
                        "The same merchant alias is assigned to multiple active "
                        f"bills: {row['bill_names']}."
                    ),
                )
            )

        orphaned_instances = conn.execute(
            """
            SELECT bi.bill_name_snapshot, pp.year, pp.month
            FROM bill_instances bi
            JOIN pay_periods pp ON pp.id=bi.pay_period_id
            LEFT JOIN bills b ON b.id=bi.bill_id
            WHERE bi.bill_id IS NULL OR b.id IS NULL
            """
        ).fetchall()
        for row in orphaned_instances:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "orphaned-bill-instance",
                    str(row["bill_name_snapshot"]),
                    "Historical bill instance has no master bill record.",
                    f"{int(row['year']):04d}-{int(row['month']):02d}",
                )
            )

        transfer_gaps = conn.execute(
            """
            SELECT name, payment_account_id, transfer_source_account_id
            FROM bills
            WHERE active=1
              AND transfer_required=1
              AND (
                payment_account_id IS NULL
                OR transfer_source_account_id IS NULL
              )
            """
        ).fetchall()
        for row in transfer_gaps:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "incomplete-transfer-routing",
                    str(row["name"]),
                    (
                        "Transfer Required is enabled but Payment Account or "
                        "Transfer Source is missing."
                    ),
                )
            )

        status_drift = conn.execute(
            """
            SELECT bi.bill_name_snapshot, bi.status, bi.paid_cents,
                   pp.year, pp.month
            FROM bill_instances bi
            JOIN pay_periods pp ON pp.id=bi.pay_period_id
            WHERE COALESCE(bi.paid_cents, 0) > 0
              AND LOWER(COALESCE(bi.status, ''))
                  NOT IN ('paid', 'partial')
            """
        ).fetchall()
        for row in status_drift:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "paid-status-drift",
                    str(row["bill_name_snapshot"]),
                    (
                        f"Paid amount is recorded but Status is "
                        f"{row['status'] or 'blank'}."
                    ),
                    f"{int(row['year']):04d}-{int(row['month']):02d}",
                )
            )

        nfo_unpaired = conn.execute(
            """
            SELECT bt.plaid_transaction_id, ba.name AS account_name,
                   COALESCE(bt.posted_date, bt.authorized_date) AS tx_date
            FROM bank_transactions bt
            JOIN bank_accounts ba
              ON ba.environment=bt.environment
             AND ba.plaid_account_id=bt.plaid_account_id
            LEFT JOIN internal_transfer_matches itm
              ON itm.environment=bt.environment
             AND itm.destination_plaid_transaction_id=bt.plaid_transaction_id
            WHERE bt.environment=?
              AND bt.pending=0
              AND LOWER(COALESCE(ba.account_type, ''))='credit'
              AND (
                LOWER(TRIM(COALESCE(bt.name, '')))='nfo payment received'
                OR LOWER(TRIM(COALESCE(bt.merchant_name, '')))
                    ='nfo payment received'
              )
              AND itm.id IS NULL
            """,
            (environment,),
        ).fetchall()
        for row in nfo_unpaired:
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "unpaired-credit-receipt",
                    str(row["account_name"] or "Credit account"),
                    (
                        "NFO PAYMENT RECEIVED exists but is not paired to a "
                        "bill payment source transaction."
                    ),
                    (
                        str(row["tx_date"])[:7]
                        if row["tx_date"]
                        else None
                    ),
                )
            )

    audit = (
        audit_entries
        if audit_entries is not None
        else build_reconciliation_audit(
            database,
            environment,
            today=today,
        )
    )
    for entry in audit:
        if entry.state == "Needs review":
            issues.append(
                DiagnosticIssue(
                    "warning",
                    "historical-reconciliation-review",
                    entry.bill_name,
                    entry.reason,
                    f"{entry.year:04d}-{entry.month:02d}",
                )
            )
        elif entry.state == "Paid · unverified":
            issues.append(
                DiagnosticIssue(
                    "info",
                    "historical-paid-unverified",
                    entry.bill_name,
                    entry.reason,
                    f"{entry.year:04d}-{entry.month:02d}",
                )
            )

    severity_order = {"error": 0, "warning": 1, "info": 2}
    return sorted(
        issues,
        key=lambda issue: (
            severity_order.get(issue.severity, 9),
            issue.period or "",
            issue.subject.casefold(),
        ),
    )
