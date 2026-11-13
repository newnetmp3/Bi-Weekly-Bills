from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from .bank_data import normalize
from .bank_sync import suggested_bill_for_transaction
from .database import Database
from .merchant_profiles import meaningful_merchant_name
from .reconciliation import transaction_date


_MIN_NAME_SCORE = 0.82
_MIN_COMBINED_SCORE = 0.89
_MIN_WINNER_GAP = 0.08
_INTERNAL_DATE_WINDOW_DAYS = 3
_NFO_PAYMENT_RECEIVED = "nfo payment received"


@dataclass(frozen=True)
class AutoMatch:
    plaid_transaction_id: str
    bill_instance_id: int
    bill_name: str
    bank_amount_cents: int
    expected_cents: int
    difference_cents: int
    confidence: float
    match_kind: str = "merchant"
    evidence_transaction_id: str | None = None


@dataclass(frozen=True)
class AutoReconcileReport:
    scanned_count: int
    matched_count: int
    review_count: int
    internal_transfer_count: int
    matches: tuple[AutoMatch, ...]


@dataclass(frozen=True)
class MatchAssessment:
    bill_instance_id: int
    bill_name: str
    expected_cents: int | None
    actual_cents: int
    difference_cents: int | None
    tolerance_cents: int | None
    name_score: float
    confidence: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class _Proposal:
    row: Any
    match: AutoMatch


@dataclass(frozen=True)
class _InternalProposal:
    source_row: Any
    destination_row: Any
    match: AutoMatch


def _row_text(row: Any) -> str:
    parts = (
        row["merchant_name"],
        row["name"],
    )
    return normalize(" ".join(str(part) for part in parts if part))


def _acronym(text: str) -> str:
    words = [
        word
        for word in normalize(text).split()
        if word not in {"a", "an", "and", "of", "the"}
    ]
    return "".join(word[0] for word in words if word)


def _plain_name_score(text: str, bill_name: str) -> float:
    candidate = normalize(text)
    bill_text = normalize(bill_name)
    if not candidate or not bill_text:
        return 0.0
    if candidate == bill_text:
        return 1.0
    if bill_text in candidate or candidate in bill_text:
        return 0.98

    bill_tokens = set(bill_text.split())
    candidate_tokens = set(candidate.split())
    if bill_tokens:
        coverage = len(bill_tokens & candidate_tokens) / len(bill_tokens)
        if coverage == 1.0:
            return 0.95
        if coverage >= 0.75:
            return 0.88

    bill_acronym = _acronym(bill_text)
    candidate_acronym = _acronym(candidate)
    compact_bill = bill_text.replace(" ", "")
    if len(bill_acronym) >= 3 and (
        bill_acronym in candidate_tokens
        or bill_acronym == candidate_acronym
    ):
        return 0.95
    if (
        len(bill_tokens) == 1
        and 3 <= len(compact_bill) <= 8
        and compact_bill == candidate_acronym
    ):
        return 0.95

    return SequenceMatcher(None, bill_text, candidate).ratio()


def _load_alias_map(database: Database) -> dict[int, tuple[str, ...]]:
    """Load all bill aliases once for bulk matching/review operations."""
    grouped: dict[int, list[str]] = defaultdict(list)
    with database.connection() as conn:
        rows = conn.execute(
            "SELECT bill_id, alias FROM bill_aliases ORDER BY bill_id, alias"
        ).fetchall()
    for row in rows:
        grouped[int(row["bill_id"])].append(str(row["alias"]))
    return {
        bill_id: tuple(values)
        for bill_id, values in grouped.items()
    }


def _connected_credit_context(
    database: Database,
    environment: str,
) -> tuple[set[int], set[str]]:
    """Return known bill IDs and account names for connected credit/loan accounts."""
    account_names: set[str] = set()
    for account in database.list_bank_accounts(environment):
        account_type = normalize(str(account["account_type"] or ""))
        subtype = normalize(str(account["account_subtype"] or ""))
        if (
            account_type in {"credit", "loan"}
            or "credit card" in subtype
            or "line of credit" in subtype
            or subtype == "loan"
        ):
            name = normalize(str(account["name"] or ""))
            if name:
                account_names.add(name)

    with database.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT bi.bill_id
            FROM internal_transfer_matches itm
            JOIN bill_instances bi ON bi.id=itm.bill_instance_id
            WHERE itm.environment=?
              AND bi.bill_id IS NOT NULL
            """,
            (environment,),
        ).fetchall()
    bill_ids = {int(row["bill_id"]) for row in rows}
    return bill_ids, account_names


def _is_connected_credit_bill(
    database: Database,
    instance: Any,
    *,
    alias_map: dict[int, tuple[str, ...]] | None,
    connected_bill_ids: set[int] | None,
    connected_account_names: set[str] | None,
) -> bool:
    bill_id = instance["bill_id"]
    if (
        bill_id is not None
        and connected_bill_ids is not None
        and int(bill_id) in connected_bill_ids
    ):
        return True

    if not connected_account_names:
        return False

    return any(
        normalize(name) in connected_account_names
        for name in _bill_names(database, instance, alias_map)
    )


def _bill_names(
    database: Database,
    instance: Any,
    alias_map: dict[int, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    names = [str(instance["bill_name_snapshot"])]
    bill_id = instance["bill_id"]
    if bill_id is not None:
        if alias_map is None:
            names.extend(database.list_bill_aliases(int(bill_id)))
        else:
            names.extend(alias_map.get(int(bill_id), ()))

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        value = " ".join(str(raw).strip().split())
        key = normalize(value)
        if value and key not in seen:
            seen.add(key)
            cleaned.append(value)
    return tuple(cleaned)


def bill_identity_score(
    database: Database,
    instance: Any,
    text: str,
    *,
    alias_map: dict[int, tuple[str, ...]] | None = None,
) -> float:
    return max(
        (
            _plain_name_score(text, candidate)
            for candidate in _bill_names(
                database,
                instance,
                alias_map,
            )
        ),
        default=0.0,
    )


def transaction_name_score(
    database: Database,
    row: Any,
    instance: Any,
    *,
    alias_map: dict[int, tuple[str, ...]] | None = None,
) -> float:
    names = _bill_names(database, instance, alias_map)
    suggestion = suggested_bill_for_transaction(row)
    if suggestion and any(
        normalize(suggestion) == normalize(name)
        for name in names
    ):
        return 1.0

    scores: list[float] = []
    for raw in (row["merchant_name"], row["name"]):
        if not raw:
            continue
        scores.extend(
            _plain_name_score(str(raw), candidate)
            for candidate in names
        )
    combined = _row_text(row)
    if combined:
        scores.extend(
            _plain_name_score(combined, candidate)
            for candidate in names
        )
    return max(scores, default=0.0)


def _amount_tolerance(expected_cents: int) -> int:
    # Small bill drift is common, but automatic reconciliation should never
    # stretch far enough to turn a different charge into a plausible match.
    # Allow at least $1, at most $5, or 2% of the expected payment in between.
    return max(100, min(500, int(round(expected_cents * 0.02))))


def expected_payment_cents(instance: Any) -> int | None:
    """Return the best known amount for matching a posted payment.

    Historical/imported rows often already contain the actual Paid amount.
    Prefer that recorded amount unless the bill is explicitly Partial; Due is
    the fallback for bills whose actual payment is not known yet.
    """
    paid = instance["paid_cents"]
    status = normalize(str(instance["status"] or ""))
    if paid is not None and int(paid) > 0 and status != "partial":
        return int(paid)

    due = instance["due_cents"]
    if due is None or int(due) <= 0:
        return None
    return int(due)


def recorded_paid_checkpoint(instance: Any) -> bool:
    """Return whether the bill is already recorded as paid by the user/app."""
    return (
        bool(int(instance["manually_paid"] or 0))
        or int(instance["paid_cents"] or 0) > 0
        or normalize(str(instance["status"] or "")) == "paid"
    )


def configured_payment_account_matches(row: Any, instance: Any) -> bool:
    """Require stable Plaid account identity for account-routed verification."""
    payment_account_id = str(instance["payment_account_id"] or "")
    transaction_account_id = str(row["plaid_account_id"] or "")
    return bool(
        payment_account_id
        and transaction_account_id
        and transaction_account_id == payment_account_id
    )


def account_routed_paid_evidence(
    row: Any,
    instance: Any,
    *,
    name_score: float | None = None,
) -> bool:
    """Strong account evidence, but never overrule a conflicting real merchant.

    Generic ACH/payment/transfer descriptions can legitimately identify a bill
    by recorded Paid amount plus the configured source account. When Plaid has
    identified a real merchant, however, that merchant must also look like the
    bill (or one of its aliases). This prevents an unrelated store/restaurant
    charge from being suggested as a connected credit/loan payment merely
    because the amount happens to match.
    """
    if meaningful_merchant_name(row):
        if name_score is None or name_score < _MIN_NAME_SCORE:
            return False
    return (
        recorded_paid_checkpoint(instance)
        and configured_payment_account_matches(row, instance)
    )


def assess_candidate(
    database: Database,
    row: Any,
    instance: Any,
    *,
    alias_map: dict[int, tuple[str, ...]] | None = None,
    connected_bill_ids: set[int] | None = None,
    connected_account_names: set[str] | None = None,
) -> MatchAssessment:
    actual = int(row["amount_cents"])
    expected = expected_payment_cents(instance)
    name_score = transaction_name_score(
        database,
        row,
        instance,
        alias_map=alias_map,
    )

    if (
        meaningful_merchant_name(row)
        and _is_connected_credit_bill(
            database,
            instance,
            alias_map=alias_map,
            connected_bill_ids=connected_bill_ids,
            connected_account_names=connected_account_names,
        )
    ):
        expected = expected_payment_cents(instance)
        actual = int(row["amount_cents"])
        return MatchAssessment(
            bill_instance_id=int(instance["id"]),
            bill_name=str(instance["bill_name_snapshot"]),
            expected_cents=expected,
            actual_cents=actual,
            difference_cents=(
                None if expected is None else actual - expected
            ),
            tolerance_cents=(
                None if expected is None
                else _amount_tolerance(expected)
            ),
            name_score=name_score,
            confidence=0.0,
            accepted=False,
            reason=(
                "named merchant transaction is not treated as a payment "
                "to a connected credit/loan account"
            ),
        )

    if int(row["pending"] or 0):
        return MatchAssessment(
            int(instance["id"]),
            str(instance["bill_name_snapshot"]),
            expected,
            actual,
            None if expected is None else actual - expected,
            None if expected is None else _amount_tolerance(expected),
            name_score,
            0.0,
            False,
            "transaction is still pending",
        )
    if actual <= 0:
        return MatchAssessment(
            int(instance["id"]),
            str(instance["bill_name_snapshot"]),
            expected,
            actual,
            None if expected is None else actual - expected,
            None if expected is None else _amount_tolerance(expected),
            name_score,
            0.0,
            False,
            "transaction is not a posted outflow",
        )
    if expected is None:
        return MatchAssessment(
            int(instance["id"]),
            str(instance["bill_name_snapshot"]),
            None,
            actual,
            None,
            None,
            name_score,
            0.0,
            False,
            "bill has no usable Due or Paid amount",
        )

    payment_account_id = str(instance["payment_account_id"] or "")
    transaction_account_id = str(row["plaid_account_id"] or "")
    if payment_account_id and transaction_account_id != payment_account_id:
        return MatchAssessment(
            bill_instance_id=int(instance["id"]),
            bill_name=str(instance["bill_name_snapshot"]),
            expected_cents=expected,
            actual_cents=actual,
            difference_cents=actual - expected,
            tolerance_cents=_amount_tolerance(expected),
            name_score=name_score,
            confidence=0.0,
            accepted=False,
            reason=(
                "transaction is not from the bill's configured Payment Account"
            ),
        )

    difference = actual - expected
    tolerance = _amount_tolerance(expected)
    amount_ratio = min(abs(difference) / max(tolerance, 1), 10.0)
    amount_score = max(0.0, 1.0 - (0.10 * amount_ratio))
    merchant_confidence = (name_score * 0.70) + (amount_score * 0.30)
    account_evidence = account_routed_paid_evidence(
        row,
        instance,
        name_score=name_score,
    )
    account_confidence = min(0.99, 0.96 + (0.03 * amount_score))
    confidence = (
        max(merchant_confidence, account_confidence)
        if account_evidence
        else merchant_confidence
    )

    if abs(difference) > tolerance:
        reason = (
            f"amount differs by {abs(difference)} cents; "
            f"automatic tolerance is {tolerance} cents"
        )
        accepted = False
    elif account_evidence:
        reason = (
            "recorded Paid amount matches a posted outgoing transaction "
            "from the configured Payment Account"
        )
        accepted = True
    elif name_score < _MIN_NAME_SCORE:
        reason = (
            f"merchant/name confidence {name_score:.2f} is below "
            f"{_MIN_NAME_SCORE:.2f}"
        )
        accepted = False
    elif confidence < _MIN_COMBINED_SCORE:
        reason = (
            f"combined confidence {confidence:.2f} is below "
            f"{_MIN_COMBINED_SCORE:.2f}"
        )
        accepted = False
    else:
        reason = "unique high-confidence merchant/amount match"
        accepted = True

    return MatchAssessment(
        bill_instance_id=int(instance["id"]),
        bill_name=str(instance["bill_name_snapshot"]),
        expected_cents=expected,
        actual_cents=actual,
        difference_cents=difference,
        tolerance_cents=tolerance,
        name_score=name_score,
        confidence=confidence,
        accepted=accepted,
        reason=reason,
    )


def _candidate_match(
    database: Database,
    row: Any,
    instance: Any,
    *,
    alias_map: dict[int, tuple[str, ...]] | None = None,
    connected_bill_ids: set[int] | None = None,
    connected_account_names: set[str] | None = None,
) -> AutoMatch | None:
    assessment = assess_candidate(
        database,
        row,
        instance,
        alias_map=alias_map,
        connected_bill_ids=connected_bill_ids,
        connected_account_names=connected_account_names,
    )
    if not assessment.accepted:
        return None
    assert assessment.expected_cents is not None
    assert assessment.difference_cents is not None
    return AutoMatch(
        plaid_transaction_id=str(row["plaid_transaction_id"]),
        bill_instance_id=assessment.bill_instance_id,
        bill_name=assessment.bill_name,
        bank_amount_cents=assessment.actual_cents,
        expected_cents=assessment.expected_cents,
        difference_cents=assessment.difference_cents,
        confidence=assessment.confidence,
        match_kind=(
            "payment-account"
            if account_routed_paid_evidence(
                row,
                instance,
                name_score=assessment.name_score,
            )
            else "merchant"
        ),
    )


def _review_rank(assessment: MatchAssessment) -> float:
    if assessment.expected_cents is None:
        amount_closeness = 0.0
    else:
        gap = abs(assessment.actual_cents - assessment.expected_cents)
        amount_closeness = max(
            0.0,
            1.0 - (gap / max(assessment.expected_cents, 100)),
        )
    return (assessment.name_score * 0.65) + (amount_closeness * 0.35)


def _best_review_candidate_from_instances(
    database: Database,
    row: Any,
    instances: list[Any],
    *,
    alias_map: dict[int, tuple[str, ...]],
    connected_bill_ids: set[int],
    connected_account_names: set[str],
) -> tuple[MatchAssessment | None, bool]:
    assessments: list[MatchAssessment] = []
    for instance in instances:
        if int(instance["bank_verified"] or 0):
            continue
        if (
            meaningful_merchant_name(row)
            and _is_connected_credit_bill(
                database,
                instance,
                alias_map=alias_map,
                connected_bill_ids=connected_bill_ids,
                connected_account_names=connected_account_names,
            )
        ):
            continue
        assessment = assess_candidate(
            database,
            row,
            instance,
            alias_map=alias_map,
            connected_bill_ids=connected_bill_ids,
            connected_account_names=connected_account_names,
        )
        if assessment.expected_cents is None:
            continue
        if (
            assessment.name_score < 0.55
            and not account_routed_paid_evidence(
                row,
                instance,
                name_score=assessment.name_score,
            )
        ):
            continue
        assessments.append(assessment)

    if not assessments:
        return None, False

    assessments.sort(key=_review_rank, reverse=True)
    best = assessments[0]
    ambiguous = (
        len(assessments) > 1
        and _review_rank(best) - _review_rank(assessments[1])
        < _MIN_WINNER_GAP
    )
    return best, ambiguous


def best_review_candidates(
    database: Database,
    rows: list[Any],
    *,
    active_only: bool = False,
) -> dict[str, tuple[MatchAssessment | None, bool]]:
    """Bulk review matching with one bill/alias load per period, not per row."""
    alias_map = _load_alias_map(database)
    period_cache: dict[tuple[int, int], list[Any]] = {}
    credit_context: dict[str, tuple[set[int], set[str]]] = {}
    results: dict[str, tuple[MatchAssessment | None, bool]] = {}

    for row in rows:
        tx_id = str(row["plaid_transaction_id"])
        tx_date = transaction_date(row)
        if (
            tx_date is None
            or int(row["pending"] or 0)
            or int(row["amount_cents"]) <= 0
        ):
            results[tx_id] = (None, False)
            continue

        period = (tx_date.year, tx_date.month)
        if period not in period_cache:
            period_cache[period] = database.list_month_instances(
                tx_date.year,
                tx_date.month,
                active_only=active_only,
            )
        environment = str(row["environment"] or "production")
        if environment not in credit_context:
            credit_context[environment] = _connected_credit_context(
                database,
                environment,
            )
        connected_bill_ids, connected_account_names = (
            credit_context[environment]
        )
        results[tx_id] = _best_review_candidate_from_instances(
            database,
            row,
            period_cache[period],
            alias_map=alias_map,
            connected_bill_ids=connected_bill_ids,
            connected_account_names=connected_account_names,
        )

    return results


def best_review_candidate(
    database: Database,
    row: Any,
    *,
    active_only: bool = False,
) -> tuple[MatchAssessment | None, bool]:
    """Return the most plausible bill for review, even if auto-match is unsafe."""
    tx_id = str(row["plaid_transaction_id"])
    return best_review_candidates(
        database,
        [row],
        active_only=active_only,
    ).get(tx_id, (None, False))


def _best_unique_match(
    database: Database,
    row: Any,
    *,
    active_only: bool = True,
    alias_map: dict[int, tuple[str, ...]] | None = None,
    period_cache: dict[tuple[int, int], list[Any]] | None = None,
    connected_bill_ids: set[int] | None = None,
    connected_account_names: set[str] | None = None,
) -> tuple[AutoMatch | None, bool]:
    tx_date = transaction_date(row)
    if tx_date is None:
        return None, False

    period = (tx_date.year, tx_date.month)
    if period_cache is not None and period in period_cache:
        candidates = period_cache[period]
    else:
        candidates = database.list_month_instances(
            tx_date.year,
            tx_date.month,
            active_only=active_only,
        )
        if period_cache is not None:
            period_cache[period] = candidates

    scored: list[AutoMatch] = []
    for instance in candidates:
        # A bill already verified by another bank transaction is not available
        # for a second automatic match.
        if int(instance["bank_verified"] or 0):
            continue
        match = _candidate_match(
            database,
            row,
            instance,
            alias_map=alias_map,
            connected_bill_ids=connected_bill_ids,
            connected_account_names=connected_account_names,
        )
        if match is not None:
            scored.append(match)

    if not scored:
        return None, False

    scored.sort(key=lambda item: item.confidence, reverse=True)
    best = scored[0]
    if len(scored) > 1:
        runner_up = scored[1]
        if best.confidence - runner_up.confidence < _MIN_WINNER_GAP:
            return None, True
    return best, False


def _is_credit_or_loan_account(row: Any) -> bool:
    account_type = normalize(str(row["account_type"] or ""))
    subtype = normalize(str(row["account_subtype"] or ""))
    if account_type in {"credit", "loan"}:
        return True
    return (
        "credit card" in subtype
        or "line of credit" in subtype
        or subtype == "loan"
    )


def _is_nfo_payment_received(row: Any) -> bool:
    return any(
        normalize(str(value or "")) == _NFO_PAYMENT_RECEIVED
        for value in (row["merchant_name"], row["name"])
    )


def _internal_transfer_proposals(
    database: Database,
    rows: list[Any],
    *,
    active_only: bool = True,
    alias_map: dict[int, tuple[str, ...]] | None = None,
    period_cache: dict[tuple[int, int], list[Any]] | None = None,
) -> tuple[list[_InternalProposal], int]:
    """Build unique two-sided credit/loan payment proposals.

    This is deliberately separate from merchant matching. For Navy Federal
    credit payments, generic NFO PAYMENT RECEIVED descriptions are treated as
    strong evidence when account identity, exact amount, configured source
    account, and posting dates agree.
    """

    unresolved = [
        row
        for row in rows
        if not int(row["pending"] or 0)
        and row["reconciliation_disposition"] is None
        and row["internal_transfer_role"] is None
    ]
    outgoing = [row for row in unresolved if int(row["amount_cents"]) > 0]
    destinations = [
        row
        for row in unresolved
        if int(row["amount_cents"]) < 0 and _is_credit_or_loan_account(row)
    ]

    proposals: list[_InternalProposal] = []
    ambiguous_destinations: set[str] = set()

    for destination in destinations:
        destination_date = transaction_date(destination)
        if destination_date is None:
            continue

        amount = abs(int(destination["amount_cents"]))
        if amount <= 0:
            continue

        is_nfo_credit_evidence = (
            normalize(str(destination["account_type"] or "")) == "credit"
            and _is_nfo_payment_received(destination)
        )

        candidates: list[_InternalProposal] = []
        period = (destination_date.year, destination_date.month)
        if period_cache is not None and period in period_cache:
            instances = period_cache[period]
        else:
            instances = database.list_month_instances(
                destination_date.year,
                destination_date.month,
                active_only=active_only,
            )
            if period_cache is not None:
                period_cache[period] = instances

        for instance in instances:
            if int(instance["bank_verified"] or 0):
                continue

            payment_account_id = str(instance["payment_account_id"] or "")
            if not payment_account_id:
                continue

            expected = expected_payment_cents(instance)
            if expected is None:
                continue

            difference = amount - expected
            tolerance = _amount_tolerance(expected)
            if abs(difference) > tolerance:
                continue

            account_score = bill_identity_score(
                database,
                instance,
                str(destination["account_name"] or ""),
                alias_map=alias_map,
            )
            identity_floor = 0.72 if is_nfo_credit_evidence else _MIN_NAME_SCORE
            if account_score < identity_floor:
                continue

            source_rows: list[Any] = []
            for source in outgoing:
                if str(source["plaid_account_id"] or "") != payment_account_id:
                    continue
                if int(source["amount_cents"]) != amount:
                    continue
                source_date = transaction_date(source)
                if source_date is None:
                    continue
                if abs((source_date - destination_date).days) > _INTERNAL_DATE_WINDOW_DAYS:
                    continue
                source_rows.append(source)

            for source in source_rows:
                source_date = transaction_date(source)
                assert source_date is not None
                date_gap = abs((source_date - destination_date).days)
                amount_score = 1.0 - (0.05 * (abs(difference) / tolerance))
                date_score = 1.0 - (
                    0.05 * (date_gap / _INTERNAL_DATE_WINDOW_DAYS)
                )
                if is_nfo_credit_evidence:
                    confidence = (
                        account_score * 0.55
                        + amount_score * 0.25
                        + date_score * 0.10
                        + 0.10
                    )
                else:
                    confidence = (
                        account_score * 0.65
                        + amount_score * 0.25
                        + date_score * 0.10
                    )
                candidates.append(
                    _InternalProposal(
                        source_row=source,
                        destination_row=destination,
                        match=AutoMatch(
                            plaid_transaction_id=str(
                                source["plaid_transaction_id"]
                            ),
                            bill_instance_id=int(instance["id"]),
                            bill_name=str(instance["bill_name_snapshot"]),
                            bank_amount_cents=amount,
                            expected_cents=expected,
                            difference_cents=difference,
                            confidence=confidence,
                            match_kind="internal-transfer",
                            evidence_transaction_id=str(
                                destination["plaid_transaction_id"]
                            ),
                        ),
                    )
                )

        if len(candidates) == 1:
            proposals.append(candidates[0])
        elif len(candidates) > 1:
            candidates.sort(
                key=lambda proposal: proposal.match.confidence,
                reverse=True,
            )
            if (
                candidates[0].match.confidence
                - candidates[1].match.confidence
                >= _MIN_WINNER_GAP
            ):
                proposals.append(candidates[0])
            else:
                ambiguous_destinations.add(
                    str(destination["plaid_transaction_id"])
                )

    if not proposals:
        return [], len(ambiguous_destinations)

    source_counts = Counter(
        str(proposal.source_row["plaid_transaction_id"])
        for proposal in proposals
    )
    destination_counts = Counter(
        str(proposal.destination_row["plaid_transaction_id"])
        for proposal in proposals
    )
    bill_counts = Counter(
        proposal.match.bill_instance_id
        for proposal in proposals
    )

    accepted: list[_InternalProposal] = []
    for proposal in proposals:
        source_id = str(proposal.source_row["plaid_transaction_id"])
        destination_id = str(
            proposal.destination_row["plaid_transaction_id"]
        )
        if (
            source_counts[source_id] == 1
            and destination_counts[destination_id] == 1
            and bill_counts[proposal.match.bill_instance_id] == 1
        ):
            accepted.append(proposal)
        else:
            ambiguous_destinations.add(destination_id)

    return accepted, len(ambiguous_destinations)


def _apply_internal_transfer_matches(
    database: Database,
    environment: str,
    rows: list[Any],
    *,
    active_only: bool = True,
    alias_map: dict[int, tuple[str, ...]] | None = None,
    period_cache: dict[tuple[int, int], list[Any]] | None = None,
) -> tuple[list[AutoMatch], int]:
    proposals, review = _internal_transfer_proposals(
        database,
        rows,
        active_only=active_only,
        alias_map=alias_map,
        period_cache=period_cache,
    )
    matches: list[AutoMatch] = []

    for proposal in proposals:
        source_id = proposal.match.plaid_transaction_id
        destination_id = str(
            proposal.destination_row["plaid_transaction_id"]
        )
        try:
            database.reconcile_transaction(
                environment=environment,
                plaid_transaction_id=source_id,
                bill_instance_id=proposal.match.bill_instance_id,
            )
            database.record_internal_transfer_match(
                environment=environment,
                source_plaid_transaction_id=source_id,
                destination_plaid_transaction_id=destination_id,
                bill_instance_id=proposal.match.bill_instance_id,
                amount_cents=proposal.match.bank_amount_cents,
            )
        except ValueError:
            # If pairing failed after the source reconciliation succeeded,
            # return the source transaction and bill to their prior state.
            try:
                if database.get_reconciliation(environment, source_id):
                    database.undo_reconciliation(environment, source_id)
            except ValueError:
                pass
            review += 1
            continue
        matches.append(proposal.match)

    return matches, review


def _before_period(row: Any, period: tuple[int, int] | None) -> bool:
    if period is None:
        return True
    tx_date = transaction_date(row)
    if tx_date is None:
        return False
    return (tx_date.year, tx_date.month) < period


def _auto_reconcile(
    database: Database,
    environment: str,
    *,
    active_only: bool,
    before_period: tuple[int, int] | None,
) -> AutoReconcileReport:
    rows = [
        row
        for row in database.list_bank_transactions(environment, limit=100000)
        if _before_period(row, before_period)
    ]
    alias_map = _load_alias_map(database)
    period_cache: dict[tuple[int, int], list[Any]] = {}
    connected_bill_ids, connected_account_names = (
        _connected_credit_context(database, environment)
    )

    internal_matches, internal_review = _apply_internal_transfer_matches(
        database,
        environment,
        rows,
        active_only=active_only,
        alias_map=alias_map,
        period_cache=period_cache,
    )

    # Re-read so successfully paired source and destination transactions are
    # excluded from normal merchant matching in this same pass. Bill-instance
    # rows must also be reloaded because internal pairing may have just changed
    # bank_verified state.
    rows = [
        row
        for row in database.list_bank_transactions(environment, limit=100000)
        if _before_period(row, before_period)
    ]
    period_cache.clear()

    proposals: list[_Proposal] = []
    scanned = 0
    review = internal_review

    for row in rows:
        if int(row["pending"] or 0) or int(row["amount_cents"]) <= 0:
            continue
        if row["reconciliation_disposition"] is not None:
            continue
        if row["internal_transfer_role"] is not None:
            continue

        scanned += 1
        match, ambiguous = _best_unique_match(
            database,
            row,
            active_only=active_only,
            alias_map=alias_map,
            period_cache=period_cache,
            connected_bill_ids=connected_bill_ids,
            connected_account_names=connected_account_names,
        )
        if ambiguous:
            review += 1
            continue
        if match is None:
            continue
        proposals.append(_Proposal(row=row, match=match))

    by_bill: dict[int, list[_Proposal]] = defaultdict(list)
    for proposal in proposals:
        by_bill[proposal.match.bill_instance_id].append(proposal)

    accepted: list[_Proposal] = []
    for competing in by_bill.values():
        if len(competing) == 1:
            accepted.append(competing[0])
        else:
            review += len(competing)

    matches: list[AutoMatch] = list(internal_matches)
    for proposal in accepted:
        try:
            database.reconcile_transaction(
                environment=environment,
                plaid_transaction_id=proposal.match.plaid_transaction_id,
                bill_instance_id=proposal.match.bill_instance_id,
            )
        except ValueError:
            review += 1
            continue
        matches.append(proposal.match)

    return AutoReconcileReport(
        scanned_count=scanned,
        matched_count=len(matches),
        review_count=review,
        internal_transfer_count=len(internal_matches),
        matches=tuple(matches),
    )


def auto_reconcile_transactions(
    database: Database,
    environment: str,
) -> AutoReconcileReport:
    """Reconcile current bank history against active bill definitions only."""
    return _auto_reconcile(
        database,
        environment,
        active_only=True,
        before_period=None,
    )


def reconcile_history(
    database: Database,
    environment: str,
    *,
    today: date | None = None,
) -> AutoReconcileReport:
    """Re-scan stored transactions for completed months.

    Historical instances are eligible even if their master bill is inactive
    today. Current and future months are intentionally excluded.
    """
    current = today or date.today()
    return _auto_reconcile(
        database,
        environment,
        active_only=False,
        before_period=(current.year, current.month),
    )
