from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from .merchant_profiles import (
    merchant_key,
    merchant_metadata_from_raw_json,
    meaningful_merchant_text,
    safe_remote_logo_url,
)


SCHEMA_VERSION = 8
VALID_CYCLES = {"1st", "15th", "Both"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_database_path() -> Path:
    root = Path(
        os.environ.get(
            "XDG_DATA_HOME",
            Path.home() / ".local" / "share",
        )
    )
    return root / "bi-weekly-bills" / "biweekly-bills.sqlite3"


def cents(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value) * 100))


@dataclass(frozen=True)
class MonthSummary:
    year: int
    month: int
    due_cents: int
    paid_cents: int
    remaining_cents: int
    bill_count: int
    paid_count: int


@dataclass(frozen=True)
class BillWorkflowProgress:
    bill_count: int
    handled_count: int
    manually_paid_count: int
    bank_verified_count: int


@dataclass(frozen=True)
class MonthCloseout:
    year: int
    month: int
    scheduled_cents: int
    paid_cents: int
    remaining_cents: int
    bill_count: int
    handled_count: int
    verified_count: int
    paid_unverified_count: int


class Database:
    """SQLite-backed source of truth for the desktop application.

    Plaid access tokens and other credentials intentionally do not belong here;
    the existing secure_store module remains authoritative for secrets.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_database_path()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bills (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    cycle TEXT NOT NULL CHECK (cycle IN ('1st', '15th', 'Both')),
                    latest_due TEXT,
                    default_method TEXT,
                    payment_account TEXT,
                    funding_account TEXT,
                    payment_account_id TEXT,
                    transfer_source_account_id TEXT,
                    transfer_required INTEGER
                        CHECK (transfer_required IS NULL OR transfer_required IN (0, 1)),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bill_aliases (
                    id INTEGER PRIMARY KEY,
                    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL COLLATE NOCASE,
                    created_at TEXT NOT NULL,
                    UNIQUE (bill_id, alias)
                );

                CREATE TABLE IF NOT EXISTS pay_periods (
                    id INTEGER PRIMARY KEY,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    cycle TEXT NOT NULL CHECK (cycle IN ('1st', '15th')),
                    created_at TEXT NOT NULL,
                    UNIQUE (year, month, cycle)
                );

                CREATE TABLE IF NOT EXISTS bill_instances (
                    id INTEGER PRIMARY KEY,
                    pay_period_id INTEGER NOT NULL REFERENCES pay_periods(id) ON DELETE CASCADE,
                    bill_id INTEGER REFERENCES bills(id) ON DELETE SET NULL,
                    bill_key TEXT NOT NULL,
                    bill_name_snapshot TEXT NOT NULL,
                    when_label TEXT,
                    due_cents INTEGER,
                    paid_cents INTEGER,
                    manually_paid INTEGER NOT NULL DEFAULT 0 CHECK (manually_paid IN (0, 1)),
                    manually_paid_at TEXT,
                    method TEXT,
                    payment_account_snapshot TEXT,
                    status TEXT,
                    extra_short TEXT,
                    source TEXT NOT NULL DEFAULT 'app',
                    source_sheet TEXT,
                    source_row INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (pay_period_id, bill_key)
                );

                CREATE TABLE IF NOT EXISTS bank_accounts (
                    id INTEGER PRIMARY KEY,
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    plaid_account_id TEXT NOT NULL,
                    name TEXT,
                    mask TEXT,
                    account_type TEXT,
                    account_subtype TEXT,
                    is_bills_checking INTEGER NOT NULL DEFAULT 0 CHECK (is_bills_checking IN (0, 1)),
                    current_balance_cents INTEGER,
                    available_balance_cents INTEGER,
                    last_synced_at TEXT,
                    UNIQUE (environment, plaid_account_id)
                );

                CREATE TABLE IF NOT EXISTS bank_transactions (
                    id INTEGER PRIMARY KEY,
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    plaid_transaction_id TEXT NOT NULL,
                    plaid_account_id TEXT,
                    posted_date TEXT,
                    authorized_date TEXT,
                    merchant_name TEXT,
                    name TEXT,
                    amount_cents INTEGER NOT NULL,
                    pending INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0, 1)),
                    raw_json TEXT,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE (environment, plaid_transaction_id)
                );

                CREATE TABLE IF NOT EXISTS merchant_profiles (
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    merchant_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    merchant_entity_id TEXT,
                    logo_url TEXT,
                    website TEXT,
                    logo_data BLOB,
                    logo_mime TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (environment, merchant_key)
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    environment TEXT PRIMARY KEY CHECK (environment IN ('sandbox', 'production')),
                    transaction_cursor TEXT,
                    transactions_update_status TEXT,
                    last_sync_at TEXT
                );

                CREATE TABLE IF NOT EXISTS transaction_reconciliations (
                    id INTEGER PRIMARY KEY,
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    plaid_transaction_id TEXT NOT NULL,
                    disposition TEXT NOT NULL CHECK (disposition IN ('matched', 'ignored')),
                    bill_instance_id INTEGER REFERENCES bill_instances(id) ON DELETE SET NULL,
                    prior_paid_cents INTEGER,
                    prior_status TEXT,
                    prior_source TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (environment, plaid_transaction_id)
                );

                CREATE TABLE IF NOT EXISTS funding_transfer_validations (
                    id INTEGER PRIMARY KEY,
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    plaid_transaction_id TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    scope TEXT NOT NULL CHECK (scope IN ('1st', '15th', 'month')),
                    expected_cents INTEGER NOT NULL,
                    actual_cents INTEGER NOT NULL,
                    difference_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (environment, plaid_transaction_id)
                );

                CREATE TABLE IF NOT EXISTS internal_transfer_matches (
                    id INTEGER PRIMARY KEY,
                    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                    source_plaid_transaction_id TEXT NOT NULL,
                    destination_plaid_transaction_id TEXT NOT NULL,
                    bill_instance_id INTEGER NOT NULL REFERENCES bill_instances(id) ON DELETE CASCADE,
                    amount_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (environment, source_plaid_transaction_id),
                    UNIQUE (environment, destination_plaid_transaction_id)
                );

                CREATE TABLE IF NOT EXISTS workbook_imports (
                    id INTEGER PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    legacy_year INTEGER,
                    imported_at TEXT NOT NULL,
                    setup_bills INTEGER NOT NULL DEFAULT 0,
                    bill_instances INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (source_sha256, legacy_year)
                );

                CREATE INDEX IF NOT EXISTS idx_bill_instances_period
                    ON bill_instances(pay_period_id);
                CREATE INDEX IF NOT EXISTS idx_bill_aliases_bill
                    ON bill_aliases(bill_id, alias COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_bank_transactions_date
                    ON bank_transactions(environment, posted_date);
                CREATE INDEX IF NOT EXISTS idx_bank_transactions_authorized_date
                    ON bank_transactions(environment, authorized_date);
                CREATE INDEX IF NOT EXISTS idx_merchant_profiles_name
                    ON merchant_profiles(environment, display_name COLLATE NOCASE);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_bill_instance
                    ON transaction_reconciliations(bill_instance_id)
                    WHERE disposition='matched' AND bill_instance_id IS NOT NULL;
                """
            )
            bill_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(bills)").fetchall()
            }
            if "payment_account_id" not in bill_columns:
                conn.execute("ALTER TABLE bills ADD COLUMN payment_account_id TEXT")
            if "transfer_source_account_id" not in bill_columns:
                conn.execute(
                    "ALTER TABLE bills ADD COLUMN transfer_source_account_id TEXT"
                )

            instance_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(bill_instances)").fetchall()
            }
            if "manually_paid" not in instance_columns:
                conn.execute(
                    "ALTER TABLE bill_instances "
                    "ADD COLUMN manually_paid INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (manually_paid IN (0, 1))"
                )
            if "manually_paid_at" not in instance_columns:
                conn.execute(
                    "ALTER TABLE bill_instances ADD COLUMN manually_paid_at TEXT"
                )

            merchant_backfill_needed = conn.execute(
                """
                SELECT value
                FROM metadata
                WHERE key='merchant_profiles_backfill_v1'
                """
            ).fetchone() is None

            conn.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

        if merchant_backfill_needed:
            for environment in ("sandbox", "production"):
                self.rebuild_merchant_profiles(environment)
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO metadata(key, value)
                    VALUES ('merchant_profiles_backfill_v1', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (utc_now(),),
                )

    @staticmethod
    def _bill_key(name: str) -> str:
        return " ".join(name.strip().casefold().split())

    def upsert_bill(
        self,
        *,
        name: str,
        cycle: str,
        latest_due: str | None = None,
        default_method: str | None = None,
        payment_account: str | None = None,
        funding_account: str | None = None,
        payment_account_id: str | None = None,
        transfer_source_account_id: str | None = None,
        transfer_required: bool | None = None,
        active: bool = True,
        notes: str | None = None,
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Bill name is required.")
        if cycle not in VALID_CYCLES:
            raise ValueError(f"Invalid bill cycle: {cycle!r}")

        now = utc_now()
        transfer_value = None if transfer_required is None else int(transfer_required)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO bills(
                    name, cycle, latest_due, default_method, payment_account,
                    funding_account, payment_account_id, transfer_source_account_id,
                    transfer_required, active, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    cycle=excluded.cycle,
                    latest_due=COALESCE(excluded.latest_due, bills.latest_due),
                    default_method=COALESCE(excluded.default_method, bills.default_method),
                    payment_account=COALESCE(excluded.payment_account, bills.payment_account),
                    funding_account=COALESCE(excluded.funding_account, bills.funding_account),
                    payment_account_id=COALESCE(
                        excluded.payment_account_id,
                        bills.payment_account_id
                    ),
                    transfer_source_account_id=COALESCE(
                        excluded.transfer_source_account_id,
                        bills.transfer_source_account_id
                    ),
                    transfer_required=COALESCE(excluded.transfer_required, bills.transfer_required),
                    active=excluded.active,
                    notes=COALESCE(excluded.notes, bills.notes),
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    cycle,
                    latest_due,
                    default_method,
                    payment_account,
                    funding_account,
                    payment_account_id,
                    transfer_source_account_id,
                    transfer_value,
                    int(active),
                    notes,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT id FROM bills WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            assert row is not None
            return int(row["id"])

    def default_transfer_source_account(
        self,
        environment: str = "production",
    ) -> sqlite3.Row | None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")

        key = f"default_transfer_source_account_id:{environment}"
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key=?",
                (key,),
            ).fetchone()

        if row is not None:
            account = self.get_bank_account(
                environment,
                str(row["value"]),
            )
            if (
                account is not None
                and str(account["account_type"] or "") == "depository"
                and str(account["account_subtype"] or "") == "checking"
                and not int(account["is_bills_checking"] or 0)
            ):
                return account

        return None

    def set_default_transfer_source_account(
        self,
        environment: str,
        plaid_account_id: str,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        account = self.get_bank_account(environment, plaid_account_id)
        if account is None:
            raise ValueError(
                "Selected transfer source account is not present in the local database."
            )
        if (
            str(account["account_type"] or "") != "depository"
            or str(account["account_subtype"] or "") != "checking"
        ):
            raise ValueError(
                "Default Transfer Source must be a checking account."
            )
        if int(account["is_bills_checking"] or 0):
            raise ValueError(
                "Bills Checking cannot also be the Default Transfer Source."
            )

        key = f"default_transfer_source_account_id:{environment}"
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, plaid_account_id),
            )
            bills_account = conn.execute(
                """
                SELECT plaid_account_id
                FROM bank_accounts
                WHERE environment=? AND is_bills_checking=1
                LIMIT 1
                """,
                (environment,),
            ).fetchone()
            if bills_account is not None:
                conn.execute(
                    """
                    UPDATE bills
                    SET transfer_source_account_id=?,
                        transfer_required=1,
                        updated_at=?
                    WHERE active=1
                      AND payment_account_id=?
                    """,
                    (
                        plaid_account_id,
                        utc_now(),
                        str(bills_account["plaid_account_id"]),
                    ),
                )

    def set_bill_payment_account(
        self,
        bill_id: int,
        payment_account_id: str | None,
    ) -> None:
        bills_account = next(
            (
                row
                for row in self.list_bank_accounts("production")
                if int(row["is_bills_checking"] or 0)
            ),
            None,
        )

        transfer_required = False
        transfer_source_account_id: str | None = None

        if payment_account_id:
            payment = self.get_bank_account(
                "production",
                payment_account_id,
            )
            if payment is None:
                raise ValueError(
                    "Selected Payment Account is not present in the Production account cache."
                )
            if str(payment["account_type"] or "") != "depository":
                raise ValueError(
                    "Bill Payment Account must be a depository account."
                )

            if (
                bills_account is not None
                and str(bills_account["plaid_account_id"]) == payment_account_id
            ):
                source = self.default_transfer_source_account("production")
                if source is None:
                    raise ValueError(
                        "Bills Checking payments require a Default Transfer Source "
                        "checking account. Select one in Settings."
                    )
                transfer_required = True
                transfer_source_account_id = str(source["plaid_account_id"])

        self.set_bill_account_assignments(
            bill_id,
            payment_account_id=payment_account_id,
            transfer_source_account_id=transfer_source_account_id,
            transfer_required=transfer_required,
        )

    def set_bill_account_assignments(
        self,
        bill_id: int,
        *,
        payment_account_id: str | None,
        transfer_source_account_id: str | None,
        transfer_required: bool | None,
    ) -> None:
        def require_depository(account_id: str, *, source: bool) -> sqlite3.Row:
            row = self.get_bank_account("production", account_id)
            if row is None:
                raise ValueError(
                    "Selected account is not present in the Production account cache."
                )
            if str(row["account_type"] or "") != "depository":
                raise ValueError(
                    "Bill payment and transfer-source accounts must be depository accounts."
                )
            if source and int(row["is_bills_checking"] or 0):
                raise ValueError(
                    "Bills Checking is the transfer destination and cannot be its own transfer source."
                )
            return row

        if payment_account_id:
            require_depository(payment_account_id, source=False)
        if transfer_source_account_id:
            require_depository(transfer_source_account_id, source=True)

        if transfer_required is not True:
            transfer_source_account_id = None

        transfer_value = (
            None if transfer_required is None else int(transfer_required)
        )
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE bills
                SET payment_account_id=?,
                    transfer_source_account_id=?,
                    transfer_required=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    payment_account_id,
                    transfer_source_account_id,
                    transfer_value,
                    utc_now(),
                    int(bill_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Bill {bill_id} was not found.")

    def set_bill_active(self, bill_id: int, active: bool) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE bills SET active=?, updated_at=? WHERE id=?",
                (int(active), utc_now(), bill_id),
            )

    def list_bills(self, *, active_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM bills"
        params: Sequence[object] = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY name COLLATE NOCASE"
        with self.connection() as conn:
            return list(conn.execute(query, params).fetchall())

    def list_bill_aliases(self, bill_id: int) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT alias FROM bill_aliases
                WHERE bill_id=?
                ORDER BY alias COLLATE NOCASE
                """,
                (int(bill_id),),
            ).fetchall()
        return [str(row["alias"]) for row in rows]

    def set_bill_aliases(
        self,
        bill_id: int,
        aliases: Sequence[str],
    ) -> None:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in aliases:
            alias = " ".join(str(raw).strip().split())
            key = alias.casefold()
            if not alias or key in seen:
                continue
            seen.add(key)
            cleaned.append(alias)

        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM bills WHERE id=?",
                (int(bill_id),),
            ).fetchone()
            if exists is None:
                raise ValueError(f"Bill {bill_id} was not found.")
            conn.execute(
                "DELETE FROM bill_aliases WHERE bill_id=?",
                (int(bill_id),),
            )
            now = utc_now()
            conn.executemany(
                """
                INSERT INTO bill_aliases(bill_id, alias, created_at)
                VALUES (?, ?, ?)
                """,
                [(int(bill_id), alias, now) for alias in cleaned],
            )

    def ensure_pay_period(self, year: int, month: int, cycle: str) -> int:
        if cycle not in {"1st", "15th"}:
            raise ValueError("Pay-period cycle must be '1st' or '15th'.")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO pay_periods(year, month, cycle, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year, month, cycle) DO NOTHING
                """,
                (year, month, cycle, utc_now()),
            )
            row = conn.execute(
                "SELECT id FROM pay_periods WHERE year=? AND month=? AND cycle=?",
                (year, month, cycle),
            ).fetchone()
            assert row is not None
            return int(row["id"])

    def upsert_bill_instance(
        self,
        *,
        year: int,
        month: int,
        cycle: str,
        bill_name: str,
        bill_id: int | None = None,
        when_label: str | None = None,
        due_cents: int | None = None,
        paid_cents: int | None = None,
        method: str | None = None,
        payment_account_snapshot: str | None = None,
        status: str | None = None,
        extra_short: str | None = None,
        source: str = "app",
        source_sheet: str | None = None,
        source_row: int | None = None,
    ) -> int:
        pay_period_id = self.ensure_pay_period(year, month, cycle)
        bill_name = bill_name.strip()
        bill_key = self._bill_key(bill_name)
        now = utc_now()

        with self.transaction() as conn:
            if bill_id is None:
                row = conn.execute(
                    "SELECT id FROM bills WHERE name=? COLLATE NOCASE",
                    (bill_name,),
                ).fetchone()
                bill_id = int(row["id"]) if row else None

            conn.execute(
                """
                INSERT INTO bill_instances(
                    pay_period_id, bill_id, bill_key, bill_name_snapshot, when_label,
                    due_cents, paid_cents, method, payment_account_snapshot, status,
                    extra_short, source, source_sheet, source_row, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pay_period_id, bill_key) DO UPDATE SET
                    bill_id=COALESCE(excluded.bill_id, bill_instances.bill_id),
                    bill_name_snapshot=excluded.bill_name_snapshot,
                    when_label=COALESCE(excluded.when_label, bill_instances.when_label),
                    due_cents=COALESCE(excluded.due_cents, bill_instances.due_cents),
                    paid_cents=COALESCE(excluded.paid_cents, bill_instances.paid_cents),
                    method=COALESCE(excluded.method, bill_instances.method),
                    payment_account_snapshot=COALESCE(
                        excluded.payment_account_snapshot,
                        bill_instances.payment_account_snapshot
                    ),
                    status=COALESCE(excluded.status, bill_instances.status),
                    extra_short=COALESCE(excluded.extra_short, bill_instances.extra_short),
                    source=excluded.source,
                    source_sheet=COALESCE(excluded.source_sheet, bill_instances.source_sheet),
                    source_row=COALESCE(excluded.source_row, bill_instances.source_row),
                    updated_at=excluded.updated_at
                """,
                (
                    pay_period_id,
                    bill_id,
                    bill_key,
                    bill_name,
                    when_label,
                    due_cents,
                    paid_cents,
                    method,
                    payment_account_snapshot,
                    status,
                    extra_short,
                    source,
                    source_sheet,
                    source_row,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM bill_instances
                WHERE pay_period_id=? AND bill_key=?
                """,
                (pay_period_id, bill_key),
            ).fetchone()
            assert row is not None
            return int(row["id"])

    @staticmethod
    def _typical_due_cents(value: str | None) -> int | None:
        if value is None:
            return None
        raw = str(value).strip().replace("$", "").replace(",", "")
        if not raw:
            return None
        try:
            amount = Decimal(raw)
        except InvalidOperation:
            return None
        return int((amount * 100).quantize(Decimal("1")))

    def materialize_active_bills(
        self,
        year: int,
        month: int,
        *,
        today: date | None = None,
    ) -> int:
        """Add missing active recurring bills to current/future periods only.

        Existing bill instances are never overwritten, and historical periods
        are never synthesized. The whole materialization runs in one SQLite
        transaction instead of opening a connection for every bill/cycle.
        """
        current = today or date.today()
        if (int(year), int(month)) < (current.year, current.month):
            return 0

        bills = self.list_bills(active_only=True)
        if not bills:
            return 0

        now = utc_now()
        created = 0
        with self.transaction() as conn:
            period_ids: dict[str, int] = {}
            for cycle in ("1st", "15th"):
                conn.execute(
                    """
                    INSERT INTO pay_periods(year, month, cycle, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(year, month, cycle) DO NOTHING
                    """,
                    (int(year), int(month), cycle, now),
                )
                row = conn.execute(
                    """
                    SELECT id FROM pay_periods
                    WHERE year=? AND month=? AND cycle=?
                    """,
                    (int(year), int(month), cycle),
                ).fetchone()
                assert row is not None
                period_ids[cycle] = int(row["id"])

            existing_rows = conn.execute(
                """
                SELECT pay_period_id, bill_id, bill_key
                FROM bill_instances
                WHERE pay_period_id IN (?, ?)
                """,
                (
                    period_ids["1st"],
                    period_ids["15th"],
                ),
            ).fetchall()
            existing_by_period: dict[int, set[tuple[int | None, str]]] = {
                period_ids["1st"]: set(),
                period_ids["15th"]: set(),
            }
            for row in existing_rows:
                existing_by_period[int(row["pay_period_id"])].add(
                    (
                        None
                        if row["bill_id"] is None
                        else int(row["bill_id"]),
                        str(row["bill_key"]),
                    )
                )

            for bill in bills:
                cycles = (
                    ("1st", "15th")
                    if str(bill["cycle"]) == "Both"
                    else (str(bill["cycle"]),)
                )
                bill_id = int(bill["id"])
                bill_name = str(bill["name"]).strip()
                bill_key = self._bill_key(bill_name)
                due_cents = self._typical_due_cents(
                    bill["latest_due"]
                )

                for cycle in cycles:
                    period_id = period_ids[cycle]
                    existing = existing_by_period[period_id]
                    if any(
                        existing_bill_id == bill_id
                        or existing_key == bill_key
                        for existing_bill_id, existing_key in existing
                    ):
                        continue

                    conn.execute(
                        """
                        INSERT INTO bill_instances(
                            pay_period_id, bill_id, bill_key,
                            bill_name_snapshot, when_label,
                            due_cents, paid_cents, method,
                            payment_account_snapshot, status,
                            extra_short, source, source_sheet, source_row,
                            created_at, updated_at
                        )
                        VALUES (
                            ?, ?, ?, ?, NULL,
                            ?, NULL, ?, NULL, 'Due',
                            NULL, 'app', NULL, NULL, ?, ?
                        )
                        """,
                        (
                            period_id,
                            bill_id,
                            bill_key,
                            bill_name,
                            due_cents,
                            bill["default_method"],
                            now,
                            now,
                        ),
                    )
                    existing.add((bill_id, bill_key))
                    created += 1

        return created

    def list_month_instances(
        self,
        year: int,
        month: int,
        *,
        active_only: bool = False,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        bi.*,
                        CASE WHEN EXISTS (
                            SELECT 1
                            FROM transaction_reconciliations tr
                            WHERE tr.bill_instance_id=bi.id
                              AND tr.disposition='matched'
                        ) THEN 1 ELSE 0 END AS bank_verified,
                        (
                            SELECT tr.plaid_transaction_id
                            FROM transaction_reconciliations tr
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_transaction_id,
                        (
                            SELECT COALESCE(bt.posted_date, bt.authorized_date)
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_date,
                        (
                            SELECT COALESCE(bt.merchant_name, bt.name)
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_description,
                        (
                            SELECT ba2.name
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            LEFT JOIN bank_accounts ba2
                                ON ba2.environment=bt.environment
                                AND ba2.plaid_account_id=bt.plaid_account_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_account_name,
                        (
                            SELECT ba2.mask
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            LEFT JOIN bank_accounts ba2
                                ON ba2.environment=bt.environment
                                AND ba2.plaid_account_id=bt.plaid_account_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_account_mask,
                        (
                            SELECT ba3.name
                            FROM internal_transfer_matches itm
                            JOIN bank_transactions bt3
                                ON bt3.environment=itm.environment
                                AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                            LEFT JOIN bank_accounts ba3
                                ON ba3.environment=bt3.environment
                                AND ba3.plaid_account_id=bt3.plaid_account_id
                            WHERE itm.bill_instance_id=bi.id
                            LIMIT 1
                        ) AS bank_evidence_account_name,
                        (
                            SELECT ba3.mask
                            FROM internal_transfer_matches itm
                            JOIN bank_transactions bt3
                                ON bt3.environment=itm.environment
                                AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                            LEFT JOIN bank_accounts ba3
                                ON ba3.environment=bt3.environment
                                AND ba3.plaid_account_id=bt3.plaid_account_id
                            WHERE itm.bill_instance_id=bi.id
                            LIMIT 1
                        ) AS bank_evidence_account_mask,
                        pp.year,
                        pp.month,
                        pp.cycle,
                        b.transfer_required,
                        b.payment_account,
                        b.funding_account,
                        b.payment_account_id,
                        b.transfer_source_account_id,
                        pa.name AS payment_account_name,
                        pa.mask AS payment_account_mask,
                        tsa.name AS transfer_source_account_name,
                        tsa.mask AS transfer_source_account_mask
                    FROM bill_instances bi
                    JOIN pay_periods pp ON pp.id = bi.pay_period_id
                    LEFT JOIN bills b ON b.id = bi.bill_id
                    LEFT JOIN bank_accounts pa
                      ON pa.environment='production'
                     AND pa.plaid_account_id=b.payment_account_id
                    LEFT JOIN bank_accounts tsa
                      ON tsa.environment='production'
                     AND tsa.plaid_account_id=b.transfer_source_account_id
                    WHERE pp.year=? AND pp.month=?
                      AND (?=0 OR b.id IS NULL OR b.active=1)
                    ORDER BY CASE pp.cycle WHEN '1st' THEN 0 ELSE 1 END,
                             bi.bill_name_snapshot COLLATE NOCASE
                    """,
                    (year, month, int(active_only)),
                ).fetchall()
            )

    def month_summary(self, year: int, month: int) -> MonthSummary:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(COALESCE(bi.due_cents, 0)), 0) AS due_cents,
                    COALESCE(SUM(COALESCE(bi.paid_cents, 0)), 0) AS paid_cents,
                    COUNT(*) AS bill_count,
                    COALESCE(SUM(CASE WHEN bi.paid_cents IS NOT NULL AND bi.paid_cents > 0
                                      THEN 1 ELSE 0 END), 0) AS paid_count
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id = bi.pay_period_id
                WHERE pp.year=? AND pp.month=?
                """,
                (year, month),
            ).fetchone()
            assert row is not None

        due = int(row["due_cents"])
        paid = int(row["paid_cents"])
        return MonthSummary(
            year=year,
            month=month,
            due_cents=due,
            paid_cents=paid,
            remaining_cents=max(due - paid, 0),
            bill_count=int(row["bill_count"]),
            paid_count=int(row["paid_count"]),
        )

    def month_closeout(self, year: int, month: int) -> MonthCloseout:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(COALESCE(bi.due_cents, 0)), 0)
                        AS scheduled_cents,
                    COALESCE(SUM(COALESCE(bi.paid_cents, 0)), 0)
                        AS paid_cents,
                    COUNT(*) AS bill_count,
                    COALESCE(SUM(CASE
                        WHEN bi.manually_paid=1
                          OR COALESCE(bi.paid_cents, 0) > 0
                          OR LOWER(COALESCE(bi.status, ''))='paid'
                          OR EXISTS (
                              SELECT 1
                              FROM transaction_reconciliations tr
                              WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                          )
                        THEN 1 ELSE 0 END), 0) AS handled_count,
                    COALESCE(SUM(CASE WHEN EXISTS (
                        SELECT 1
                        FROM transaction_reconciliations tr
                        WHERE tr.bill_instance_id=bi.id
                          AND tr.disposition='matched'
                    ) THEN 1 ELSE 0 END), 0) AS verified_count,
                    COALESCE(SUM(CASE
                        WHEN NOT EXISTS (
                            SELECT 1
                            FROM transaction_reconciliations tr
                            WHERE tr.bill_instance_id=bi.id
                              AND tr.disposition='matched'
                        )
                         AND (
                            bi.manually_paid=1
                            OR COALESCE(bi.paid_cents, 0) > 0
                            OR LOWER(COALESCE(bi.status, ''))='paid'
                         )
                        THEN 1 ELSE 0 END), 0) AS paid_unverified_count
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id=bi.pay_period_id
                WHERE pp.year=? AND pp.month=?
                """,
                (int(year), int(month)),
            ).fetchone()
            assert row is not None

        scheduled = int(row["scheduled_cents"])
        paid = int(row["paid_cents"])
        return MonthCloseout(
            year=int(year),
            month=int(month),
            scheduled_cents=scheduled,
            paid_cents=paid,
            remaining_cents=max(scheduled - paid, 0),
            bill_count=int(row["bill_count"]),
            handled_count=int(row["handled_count"]),
            verified_count=int(row["verified_count"]),
            paid_unverified_count=int(row["paid_unverified_count"]),
        )

    def cycle_summary(self, year: int, month: int, cycle: str) -> MonthSummary:
        if cycle not in {"1st", "15th"}:
            raise ValueError("cycle must be 1st or 15th")
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(COALESCE(bi.due_cents, 0)), 0) AS due_cents,
                    COALESCE(SUM(COALESCE(bi.paid_cents, 0)), 0) AS paid_cents,
                    COUNT(*) AS bill_count,
                    COALESCE(SUM(CASE WHEN bi.paid_cents IS NOT NULL AND bi.paid_cents > 0
                                      THEN 1 ELSE 0 END), 0) AS paid_count
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id = bi.pay_period_id
                WHERE pp.year=? AND pp.month=? AND pp.cycle=?
                """,
                (year, month, cycle),
            ).fetchone()
            assert row is not None

        due = int(row["due_cents"])
        paid = int(row["paid_cents"])
        return MonthSummary(
            year=year,
            month=month,
            due_cents=due,
            paid_cents=paid,
            remaining_cents=max(due - paid, 0),
            bill_count=int(row["bill_count"]),
            paid_count=int(row["paid_count"]),
        )

    def workflow_progress(
        self,
        year: int,
        month: int,
        cycle: str | None = None,
        *,
        active_only: bool = False,
    ) -> BillWorkflowProgress:
        if cycle not in {None, "1st", "15th"}:
            raise ValueError("cycle must be 1st, 15th, or None")

        where = "WHERE pp.year=? AND pp.month=?"
        params: tuple[object, ...] = (year, month)
        if cycle is not None:
            where += " AND pp.cycle=?"
            params += (cycle,)
        if active_only:
            where += " AND (b.id IS NULL OR b.active=1)"

        with self.connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS bill_count,
                    COALESCE(SUM(CASE
                        WHEN bi.manually_paid=1
                          OR EXISTS (
                              SELECT 1
                              FROM transaction_reconciliations tr
                              WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                          )
                        THEN 1 ELSE 0 END), 0) AS handled_count,
                    COALESCE(SUM(CASE WHEN bi.manually_paid=1 THEN 1 ELSE 0 END), 0)
                        AS manually_paid_count,
                    COALESCE(SUM(CASE WHEN EXISTS (
                        SELECT 1
                        FROM transaction_reconciliations tr
                        WHERE tr.bill_instance_id=bi.id
                          AND tr.disposition='matched'
                    ) THEN 1 ELSE 0 END), 0) AS bank_verified_count
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id=bi.pay_period_id
                LEFT JOIN bills b ON b.id=bi.bill_id
                {where}
                """,
                params,
            ).fetchone()
            assert row is not None

        return BillWorkflowProgress(
            bill_count=int(row["bill_count"]),
            handled_count=int(row["handled_count"]),
            manually_paid_count=int(row["manually_paid_count"]),
            bank_verified_count=int(row["bank_verified_count"]),
        )

    def list_cycle_instances(
        self,
        year: int,
        month: int,
        cycle: str,
        *,
        active_only: bool = False,
    ) -> list[sqlite3.Row]:
        if cycle not in {"1st", "15th"}:
            raise ValueError("cycle must be 1st or 15th")
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        bi.*,
                        CASE WHEN EXISTS (
                            SELECT 1
                            FROM transaction_reconciliations tr
                            WHERE tr.bill_instance_id=bi.id
                              AND tr.disposition='matched'
                        ) THEN 1 ELSE 0 END AS bank_verified,
                        (
                            SELECT tr.plaid_transaction_id
                            FROM transaction_reconciliations tr
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_transaction_id,
                        (
                            SELECT COALESCE(bt.posted_date, bt.authorized_date)
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_date,
                        (
                            SELECT COALESCE(bt.merchant_name, bt.name)
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_description,
                        (
                            SELECT ba2.name
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            LEFT JOIN bank_accounts ba2
                                ON ba2.environment=bt.environment
                                AND ba2.plaid_account_id=bt.plaid_account_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_account_name,
                        (
                            SELECT ba2.mask
                            FROM transaction_reconciliations tr
                            JOIN bank_transactions bt
                                ON bt.environment=tr.environment
                                AND bt.plaid_transaction_id=tr.plaid_transaction_id
                            LEFT JOIN bank_accounts ba2
                                ON ba2.environment=bt.environment
                                AND ba2.plaid_account_id=bt.plaid_account_id
                            WHERE tr.bill_instance_id=bi.id
                                AND tr.disposition='matched'
                            LIMIT 1
                        ) AS bank_verified_account_mask,
                        (
                            SELECT ba3.name
                            FROM internal_transfer_matches itm
                            JOIN bank_transactions bt3
                                ON bt3.environment=itm.environment
                                AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                            LEFT JOIN bank_accounts ba3
                                ON ba3.environment=bt3.environment
                                AND ba3.plaid_account_id=bt3.plaid_account_id
                            WHERE itm.bill_instance_id=bi.id
                            LIMIT 1
                        ) AS bank_evidence_account_name,
                        (
                            SELECT ba3.mask
                            FROM internal_transfer_matches itm
                            JOIN bank_transactions bt3
                                ON bt3.environment=itm.environment
                                AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                            LEFT JOIN bank_accounts ba3
                                ON ba3.environment=bt3.environment
                                AND ba3.plaid_account_id=bt3.plaid_account_id
                            WHERE itm.bill_instance_id=bi.id
                            LIMIT 1
                        ) AS bank_evidence_account_mask,
                        pp.year,
                        pp.month,
                        pp.cycle,
                        b.name AS master_name,
                        b.payment_account,
                        b.funding_account,
                        b.payment_account_id,
                        b.transfer_source_account_id,
                        b.transfer_required,
                        pa.name AS payment_account_name,
                        pa.mask AS payment_account_mask,
                        tsa.name AS transfer_source_account_name,
                        tsa.mask AS transfer_source_account_mask
                    FROM bill_instances bi
                    JOIN pay_periods pp ON pp.id = bi.pay_period_id
                    LEFT JOIN bills b ON b.id = bi.bill_id
                    LEFT JOIN bank_accounts pa
                      ON pa.environment='production'
                     AND pa.plaid_account_id=b.payment_account_id
                    LEFT JOIN bank_accounts tsa
                      ON tsa.environment='production'
                     AND tsa.plaid_account_id=b.transfer_source_account_id
                    WHERE pp.year=? AND pp.month=? AND pp.cycle=?
                      AND (?=0 OR b.id IS NULL OR b.active=1)
                    ORDER BY bi.source_row IS NULL, bi.source_row,
                             bi.bill_name_snapshot COLLATE NOCASE
                    """,
                    (year, month, cycle, int(active_only)),
                ).fetchall()
            )

    def get_bill_instance(self, instance_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT
                    bi.*,
                    CASE WHEN EXISTS (
                        SELECT 1
                        FROM transaction_reconciliations tr
                        WHERE tr.bill_instance_id=bi.id
                          AND tr.disposition='matched'
                    ) THEN 1 ELSE 0 END AS bank_verified,
                    (
                        SELECT tr.plaid_transaction_id
                        FROM transaction_reconciliations tr
                        WHERE tr.bill_instance_id=bi.id
                            AND tr.disposition='matched'
                        LIMIT 1
                    ) AS bank_transaction_id,
                    (
                        SELECT COALESCE(bt.posted_date, bt.authorized_date)
                        FROM transaction_reconciliations tr
                        JOIN bank_transactions bt
                            ON bt.environment=tr.environment
                            AND bt.plaid_transaction_id=tr.plaid_transaction_id
                        WHERE tr.bill_instance_id=bi.id
                            AND tr.disposition='matched'
                        LIMIT 1
                    ) AS bank_verified_date,
                    (
                        SELECT COALESCE(bt.merchant_name, bt.name)
                        FROM transaction_reconciliations tr
                        JOIN bank_transactions bt
                            ON bt.environment=tr.environment
                            AND bt.plaid_transaction_id=tr.plaid_transaction_id
                        WHERE tr.bill_instance_id=bi.id
                            AND tr.disposition='matched'
                        LIMIT 1
                    ) AS bank_verified_description,
                    (
                        SELECT ba2.name
                        FROM transaction_reconciliations tr
                        JOIN bank_transactions bt
                            ON bt.environment=tr.environment
                            AND bt.plaid_transaction_id=tr.plaid_transaction_id
                        LEFT JOIN bank_accounts ba2
                            ON ba2.environment=bt.environment
                            AND ba2.plaid_account_id=bt.plaid_account_id
                        WHERE tr.bill_instance_id=bi.id
                            AND tr.disposition='matched'
                        LIMIT 1
                    ) AS bank_verified_account_name,
                    (
                        SELECT ba2.mask
                        FROM transaction_reconciliations tr
                        JOIN bank_transactions bt
                            ON bt.environment=tr.environment
                            AND bt.plaid_transaction_id=tr.plaid_transaction_id
                        LEFT JOIN bank_accounts ba2
                            ON ba2.environment=bt.environment
                            AND ba2.plaid_account_id=bt.plaid_account_id
                        WHERE tr.bill_instance_id=bi.id
                            AND tr.disposition='matched'
                        LIMIT 1
                    ) AS bank_verified_account_mask,
                    (
                        SELECT ba3.name
                        FROM internal_transfer_matches itm
                        JOIN bank_transactions bt3
                            ON bt3.environment=itm.environment
                            AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                        LEFT JOIN bank_accounts ba3
                            ON ba3.environment=bt3.environment
                            AND ba3.plaid_account_id=bt3.plaid_account_id
                        WHERE itm.bill_instance_id=bi.id
                        LIMIT 1
                    ) AS bank_evidence_account_name,
                    (
                        SELECT ba3.mask
                        FROM internal_transfer_matches itm
                        JOIN bank_transactions bt3
                            ON bt3.environment=itm.environment
                            AND bt3.plaid_transaction_id=itm.destination_plaid_transaction_id
                        LEFT JOIN bank_accounts ba3
                            ON ba3.environment=bt3.environment
                            AND ba3.plaid_account_id=bt3.plaid_account_id
                        WHERE itm.bill_instance_id=bi.id
                        LIMIT 1
                    ) AS bank_evidence_account_mask,
                    pp.year,
                    pp.month,
                    pp.cycle,
                    b.payment_account,
                    b.funding_account,
                    b.payment_account_id,
                    b.transfer_source_account_id,
                    b.transfer_required,
                    pa.name AS payment_account_name,
                    pa.mask AS payment_account_mask,
                    tsa.name AS transfer_source_account_name,
                    tsa.mask AS transfer_source_account_mask
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id = bi.pay_period_id
                LEFT JOIN bills b ON b.id = bi.bill_id
                LEFT JOIN bank_accounts pa
                  ON pa.environment='production'
                 AND pa.plaid_account_id=b.payment_account_id
                LEFT JOIN bank_accounts tsa
                  ON tsa.environment='production'
                 AND tsa.plaid_account_id=b.transfer_source_account_id
                WHERE bi.id=?
                """,
                (instance_id,),
            ).fetchone()

    def update_bill_instance(
        self,
        instance_id: int,
        *,
        when_label: str | None,
        due_cents: int | None,
        paid_cents: int | None,
        method: str | None,
        status: str | None,
        extra_short: str | None,
        payment_account_snapshot: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE bill_instances
                SET when_label=?,
                    due_cents=?,
                    paid_cents=?,
                    method=?,
                    status=?,
                    extra_short=?,
                    payment_account_snapshot=?,
                    source='app',
                    updated_at=?
                WHERE id=?
                """,
                (
                    when_label,
                    due_cents,
                    paid_cents,
                    method,
                    status,
                    extra_short,
                    payment_account_snapshot,
                    utc_now(),
                    instance_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Bill instance {instance_id} was not found.")

    def set_bill_manually_paid(
        self,
        instance_id: int,
        manually_paid: bool,
    ) -> None:
        now = utc_now()
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE bill_instances
                SET manually_paid=?,
                    manually_paid_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    int(manually_paid),
                    now if manually_paid else None,
                    now,
                    instance_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Bill instance {instance_id} was not found.")

    def available_years(self) -> list[int]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT year FROM pay_periods ORDER BY year"
            ).fetchall()
        return [int(row["year"]) for row in rows]

    def upsert_bank_account(
        self,
        *,
        environment: str,
        plaid_account_id: str,
        name: str | None,
        mask: str | None,
        account_type: str | None,
        account_subtype: str | None,
        current_balance_cents: int | None,
        available_balance_cents: int | None,
        last_synced_at: str | None,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO bank_accounts(
                    environment, plaid_account_id, name, mask, account_type,
                    account_subtype, current_balance_cents, available_balance_cents,
                    last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment, plaid_account_id) DO UPDATE SET
                    name=excluded.name,
                    mask=excluded.mask,
                    account_type=excluded.account_type,
                    account_subtype=excluded.account_subtype,
                    current_balance_cents=excluded.current_balance_cents,
                    available_balance_cents=excluded.available_balance_cents,
                    last_synced_at=excluded.last_synced_at
                """,
                (
                    environment,
                    plaid_account_id,
                    name,
                    mask,
                    account_type,
                    account_subtype,
                    current_balance_cents,
                    available_balance_cents,
                    last_synced_at,
                ),
            )

    def get_bank_account(
        self,
        environment: str,
        plaid_account_id: str | None,
    ) -> sqlite3.Row | None:
        if not plaid_account_id:
            return None
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT
                    ba.*,
                    CASE WHEN
                        LOWER(TRIM(COALESCE(ba.account_type, ''))) = 'credit'
                        AND EXISTS (
                            SELECT 1
                            FROM bank_transactions bt
                            WHERE bt.environment=ba.environment
                              AND bt.plaid_account_id=ba.plaid_account_id
                              AND bt.pending=0
                              AND (
                                  UPPER(TRIM(COALESCE(bt.name, '')))
                                      = 'NFO PAYMENT RECEIVED'
                                  OR UPPER(TRIM(COALESCE(bt.merchant_name, '')))
                                      = 'NFO PAYMENT RECEIVED'
                              )
                        )
                    THEN 1 ELSE 0 END AS verified
                FROM bank_accounts ba
                WHERE ba.environment=? AND ba.plaid_account_id=?
                """,
                (environment, plaid_account_id),
            ).fetchone()

    def bank_account_label(
        self,
        environment: str,
        plaid_account_id: str | None,
    ) -> str | None:
        row = self.get_bank_account(environment, plaid_account_id)
        if row is None:
            return None
        label = str(row["name"] or "(unnamed account)")
        mask = str(row["mask"] or "")
        if mask:
            label += f" ••••{mask}"
        return label

    def set_bills_checking(self, environment: str, plaid_account_id: str) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")

        # Resolve the configured source before changing account roles. Older
        # installations may only have the original compatibility fallback;
        # seed that into the explicit metadata role when it is still valid.
        prior_source = self.default_transfer_source_account(environment)
        prior_source_id = (
            None
            if prior_source is None
            else str(prior_source["plaid_account_id"])
        )
        if prior_source_id == plaid_account_id:
            prior_source_id = None

        source_key = f"default_transfer_source_account_id:{environment}"
        with self.transaction() as conn:
            row = conn.execute(
                """
                SELECT account_type, account_subtype
                FROM bank_accounts
                WHERE environment=? AND plaid_account_id=?
                """,
                (environment, plaid_account_id),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Selected account is not present in the local database."
                )
            if (
                str(row["account_type"] or "") != "depository"
                or str(row["account_subtype"] or "") != "checking"
            ):
                raise ValueError(
                    "Bills Checking must be a checking account."
                )

            conn.execute(
                "UPDATE bank_accounts SET is_bills_checking=0 WHERE environment=?",
                (environment,),
            )
            conn.execute(
                """
                UPDATE bank_accounts
                SET is_bills_checking=1
                WHERE environment=? AND plaid_account_id=?
                """,
                (environment, plaid_account_id),
            )

            conn.execute(
                """
                DELETE FROM metadata
                WHERE key=?
                  AND value=?
                """,
                (source_key, plaid_account_id),
            )
            source_row = conn.execute(
                "SELECT value FROM metadata WHERE key=?",
                (source_key,),
            ).fetchone()
            source_id = (
                None
                if source_row is None
                else str(source_row["value"])
            )
            if source_id is None and prior_source_id is not None:
                conn.execute(
                    """
                    INSERT INTO metadata(key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (source_key, prior_source_id),
                )
                source_id = prior_source_id

            # Bills no longer paid from the designated Bills Checking account
            # must leave the aggregate-transfer workflow.
            conn.execute(
                """
                UPDATE bills
                SET transfer_required=0,
                    transfer_source_account_id=NULL,
                    updated_at=?
                WHERE active=1
                  AND transfer_required=1
                  AND COALESCE(payment_account_id, '')<>?
                """,
                (utc_now(), plaid_account_id),
            )
            conn.execute(
                """
                UPDATE bills
                SET transfer_required=1,
                    transfer_source_account_id=?,
                    updated_at=?
                WHERE active=1
                  AND payment_account_id=?
                """,
                (source_id, utc_now(), plaid_account_id),
            )

    def list_bank_accounts(self, environment: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        ba.*,
                        CASE WHEN
                            LOWER(TRIM(COALESCE(ba.account_type, ''))) = 'credit'
                            AND EXISTS (
                                SELECT 1
                                FROM bank_transactions bt
                                WHERE bt.environment=ba.environment
                                  AND bt.plaid_account_id=ba.plaid_account_id
                                  AND bt.pending=0
                                  AND (
                                      UPPER(TRIM(COALESCE(bt.name, '')))
                                          = 'NFO PAYMENT RECEIVED'
                                      OR UPPER(TRIM(COALESCE(bt.merchant_name, '')))
                                          = 'NFO PAYMENT RECEIVED'
                                  )
                            )
                        THEN 1 ELSE 0 END AS verified
                    FROM bank_accounts ba
                    WHERE ba.environment=?
                    ORDER BY ba.is_bills_checking DESC,
                             ba.name COLLATE NOCASE,
                             ba.mask
                    """,
                    (environment,),
                ).fetchall()
            )

    @staticmethod
    def _upsert_merchant_profile_conn(
        conn: sqlite3.Connection,
        *,
        environment: str,
        merchant_name: str | None,
        raw_json: str | None,
        seen_at: str | None,
    ) -> None:
        merchant_name = meaningful_merchant_text(merchant_name)
        key = merchant_key(merchant_name)
        if not key:
            return

        logo_url, website, entity_id = merchant_metadata_from_raw_json(
            raw_json
        )
        logo_url = safe_remote_logo_url(logo_url)
        display_name = " ".join(str(merchant_name).strip().split())
        now = utc_now()

        conn.execute(
            """
            INSERT INTO merchant_profiles(
                environment, merchant_key, display_name,
                merchant_entity_id, logo_url, website,
                logo_data, logo_mime,
                first_seen_at, last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(environment, merchant_key) DO UPDATE SET
                display_name=excluded.display_name,
                merchant_entity_id=COALESCE(
                    excluded.merchant_entity_id,
                    merchant_profiles.merchant_entity_id
                ),
                logo_data=CASE
                    WHEN excluded.logo_url IS NOT NULL
                     AND excluded.logo_url != merchant_profiles.logo_url
                    THEN NULL
                    ELSE merchant_profiles.logo_data
                END,
                logo_mime=CASE
                    WHEN excluded.logo_url IS NOT NULL
                     AND excluded.logo_url != merchant_profiles.logo_url
                    THEN NULL
                    ELSE merchant_profiles.logo_mime
                END,
                logo_url=COALESCE(
                    excluded.logo_url,
                    merchant_profiles.logo_url
                ),
                website=COALESCE(
                    excluded.website,
                    merchant_profiles.website
                ),
                first_seen_at=CASE
                    WHEN merchant_profiles.first_seen_at IS NULL
                    THEN excluded.first_seen_at
                    WHEN excluded.first_seen_at IS NULL
                    THEN merchant_profiles.first_seen_at
                    WHEN excluded.first_seen_at < merchant_profiles.first_seen_at
                    THEN excluded.first_seen_at
                    ELSE merchant_profiles.first_seen_at
                END,
                last_seen_at=CASE
                    WHEN merchant_profiles.last_seen_at IS NULL
                    THEN excluded.last_seen_at
                    WHEN excluded.last_seen_at IS NULL
                    THEN merchant_profiles.last_seen_at
                    WHEN excluded.last_seen_at > merchant_profiles.last_seen_at
                    THEN excluded.last_seen_at
                    ELSE merchant_profiles.last_seen_at
                END,
                updated_at=excluded.updated_at
            """,
            (
                environment,
                key,
                display_name,
                entity_id,
                logo_url,
                website,
                seen_at,
                seen_at,
                now,
            ),
        )

    def rebuild_merchant_profiles(self, environment: str) -> int:
        """Backfill merchant identities from locally stored transaction history."""
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")

        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT merchant_name, raw_json,
                       COALESCE(posted_date, authorized_date, last_seen_at)
                           AS seen_at
                FROM bank_transactions
                WHERE environment=?
                  AND TRIM(COALESCE(merchant_name, '')) != ''
                ORDER BY COALESCE(posted_date, authorized_date, last_seen_at)
                """,
                (environment,),
            ).fetchall()
            for row in rows:
                self._upsert_merchant_profile_conn(
                    conn,
                    environment=environment,
                    merchant_name=row["merchant_name"],
                    raw_json=row["raw_json"],
                    seen_at=row["seen_at"],
                )
        return len(rows)

    def list_merchant_profiles(
        self,
        environment: str,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT *
                    FROM merchant_profiles
                    WHERE environment=?
                    ORDER BY display_name COLLATE NOCASE
                    """,
                    (environment,),
                ).fetchall()
            )

    def get_merchant_profile(
        self,
        environment: str,
        key: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT *
                FROM merchant_profiles
                WHERE environment=? AND merchant_key=?
                """,
                (environment, key),
            ).fetchone()

    def set_merchant_logo(
        self,
        environment: str,
        key: str,
        logo_data: bytes,
        *,
        mime_type: str | None = None,
    ) -> None:
        if not logo_data:
            raise ValueError("logo_data must not be empty")
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE merchant_profiles
                SET logo_data=?, logo_mime=?, updated_at=?
                WHERE environment=? AND merchant_key=?
                """,
                (
                    sqlite3.Binary(logo_data),
                    mime_type,
                    utc_now(),
                    environment,
                    key,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"Merchant profile {key!r} was not found."
                )

    def upsert_bank_transaction(
        self,
        *,
        environment: str,
        plaid_transaction_id: str,
        plaid_account_id: str | None,
        posted_date: str | None,
        authorized_date: str | None,
        merchant_name: str | None,
        name: str | None,
        amount_cents: int,
        pending: bool,
        raw_json: str | None,
        last_seen_at: str,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO bank_transactions(
                    environment, plaid_transaction_id, plaid_account_id,
                    posted_date, authorized_date, merchant_name, name,
                    amount_cents, pending, raw_json, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment, plaid_transaction_id) DO UPDATE SET
                    plaid_account_id=excluded.plaid_account_id,
                    posted_date=excluded.posted_date,
                    authorized_date=excluded.authorized_date,
                    merchant_name=excluded.merchant_name,
                    name=excluded.name,
                    amount_cents=excluded.amount_cents,
                    pending=excluded.pending,
                    raw_json=excluded.raw_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    environment,
                    plaid_transaction_id,
                    plaid_account_id,
                    posted_date,
                    authorized_date,
                    merchant_name,
                    name,
                    amount_cents,
                    int(pending),
                    raw_json,
                    last_seen_at,
                ),
            )

            self._upsert_merchant_profile_conn(
                conn,
                environment=environment,
                merchant_name=merchant_name,
                raw_json=raw_json,
                seen_at=posted_date or authorized_date or last_seen_at,
            )

    def delete_bank_transaction(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> None:
        with self.transaction() as conn:
            internal = conn.execute(
                """
                SELECT * FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id=?
                    OR destination_plaid_transaction_id=?
                  )
                """,
                (
                    environment,
                    plaid_transaction_id,
                    plaid_transaction_id,
                ),
            ).fetchone()

            # If Plaid removes the destination/evidence side, the source
            # payment is no longer independently verified. Restore the bill
            # and return the source transaction to unresolved state.
            if (
                internal is not None
                and str(internal["destination_plaid_transaction_id"])
                == plaid_transaction_id
            ):
                source_id = str(internal["source_plaid_transaction_id"])
                source_reconciliation = conn.execute(
                    """
                    SELECT * FROM transaction_reconciliations
                    WHERE environment=? AND plaid_transaction_id=?
                    """,
                    (environment, source_id),
                ).fetchone()
                if (
                    source_reconciliation is not None
                    and source_reconciliation["disposition"] == "matched"
                    and source_reconciliation["bill_instance_id"] is not None
                ):
                    conn.execute(
                        """
                        UPDATE bill_instances
                        SET paid_cents=?, status=?, source=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            source_reconciliation["prior_paid_cents"],
                            source_reconciliation["prior_status"],
                            source_reconciliation["prior_source"] or "app",
                            utc_now(),
                            int(source_reconciliation["bill_instance_id"]),
                        ),
                    )
                    conn.execute(
                        """
                        DELETE FROM transaction_reconciliations
                        WHERE environment=? AND plaid_transaction_id=?
                        """,
                        (environment, source_id),
                    )

            reconciliation = conn.execute(
                """
                SELECT * FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            if (
                reconciliation is not None
                and reconciliation["disposition"] == "matched"
                and reconciliation["bill_instance_id"] is not None
            ):
                conn.execute(
                    """
                    UPDATE bill_instances
                    SET paid_cents=?, status=?, source=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        reconciliation["prior_paid_cents"],
                        reconciliation["prior_status"],
                        reconciliation["prior_source"] or "app",
                        utc_now(),
                        int(reconciliation["bill_instance_id"]),
                    ),
                )

            conn.execute(
                """
                DELETE FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id=?
                    OR destination_plaid_transaction_id=?
                  )
                """,
                (
                    environment,
                    plaid_transaction_id,
                    plaid_transaction_id,
                ),
            )
            conn.execute(
                """
                DELETE FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            )
            conn.execute(
                """
                DELETE FROM funding_transfer_validations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            )
            conn.execute(
                """
                DELETE FROM bank_transactions
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            )

    def clear_bank_transactions(
        self,
        environment: str,
        *,
        clear_sync_state: bool = True,
    ) -> int:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")

        with self.connection() as conn:
            transaction_ids = [
                str(row["plaid_transaction_id"])
                for row in conn.execute(
                    """
                    SELECT plaid_transaction_id
                    FROM bank_transactions
                    WHERE environment=?
                    ORDER BY plaid_transaction_id
                    """,
                    (environment,),
                ).fetchall()
            ]

        # Use the normal delete path so any reconciliation-created Paid/Status
        # values are unwound instead of leaving test-bank effects on bill rows.
        for transaction_id in transaction_ids:
            self.delete_bank_transaction(environment, transaction_id)

        with self.transaction() as conn:
            # Defensive cleanup in case an older database contains an orphaned
            # reconciliation row.
            conn.execute(
                "DELETE FROM transaction_reconciliations WHERE environment=?",
                (environment,),
            )
            conn.execute(
                "DELETE FROM funding_transfer_validations WHERE environment=?",
                (environment,),
            )
            if clear_sync_state:
                conn.execute(
                    "DELETE FROM sync_state WHERE environment=?",
                    (environment,),
                )

        return len(transaction_ids)

    def bank_transaction_periods(
        self,
        environment: str,
    ) -> list[tuple[int, int]]:
        """Return available transaction months without loading transaction rows."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT
                    CAST(SUBSTR(COALESCE(posted_date, authorized_date), 1, 4) AS INTEGER)
                        AS year,
                    CAST(SUBSTR(COALESCE(posted_date, authorized_date), 6, 2) AS INTEGER)
                        AS month
                FROM bank_transactions
                WHERE environment=?
                  AND COALESCE(posted_date, authorized_date) IS NOT NULL
                  AND LENGTH(COALESCE(posted_date, authorized_date)) >= 7
                ORDER BY year DESC, month DESC
                """,
                (environment,),
            ).fetchall()
        return [
            (int(row["year"]), int(row["month"]))
            for row in rows
            if int(row["year"] or 0) > 0
            and 1 <= int(row["month"] or 0) <= 12
        ]

    def list_bank_transactions(
        self,
        environment: str,
        *,
        limit: int = 1000,
        year: int | None = None,
        month: int | None = None,
    ) -> list[sqlite3.Row]:
        period_clause = ""
        params: list[object] = [environment]
        if year is not None or month is not None:
            if year is None or month is None:
                raise ValueError("year and month must be supplied together")
            selected_year = int(year)
            selected_month = int(month)
            if not 1 <= selected_month <= 12:
                raise ValueError("month must be between 1 and 12")
            start = date(selected_year, selected_month, 1)
            if selected_month == 12:
                end = date(selected_year + 1, 1, 1)
            else:
                end = date(selected_year, selected_month + 1, 1)
            start_text = start.isoformat()
            end_text = end.isoformat()
            period_clause = """
              AND (
                    (bt.posted_date>=? AND bt.posted_date<?)
                    OR (
                        bt.posted_date IS NULL
                        AND bt.authorized_date>=?
                        AND bt.authorized_date<?
                    )
                  )
            """
            params.extend(
                [start_text, end_text, start_text, end_text]
            )
        params.append(int(limit))

        with self.connection() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT
                        bt.*,
                        ba.name AS account_name,
                        ba.mask AS account_mask,
                        ba.account_type AS account_type,
                        ba.account_subtype AS account_subtype,
                        ba.is_bills_checking,
                        tr.disposition AS reconciliation_disposition,
                        tr.bill_instance_id AS reconciled_bill_instance_id,
                        bi.bill_name_snapshot AS reconciled_bill_name,
                        pp.year AS reconciled_year,
                        pp.month AS reconciled_month,
                        pp.cycle AS reconciled_cycle,
                        ftv.scope AS funding_validation_scope,
                        ftv.year AS funding_validation_year,
                        ftv.month AS funding_validation_month,
                        ftv.expected_cents AS funding_expected_cents,
                        ftv.actual_cents AS funding_actual_cents,
                        ftv.difference_cents AS funding_difference_cents,
                        CASE
                            WHEN itms.id IS NOT NULL THEN 'source'
                            WHEN itmd.id IS NOT NULL THEN 'destination'
                            ELSE NULL
                        END AS internal_transfer_role,
                        COALESCE(
                            itms.source_plaid_transaction_id,
                            itmd.source_plaid_transaction_id
                        ) AS internal_transfer_source_transaction_id,
                        COALESCE(
                            itms.destination_plaid_transaction_id,
                            itmd.destination_plaid_transaction_id
                        ) AS internal_transfer_destination_transaction_id,
                        COALESCE(
                            itms.bill_instance_id,
                            itmd.bill_instance_id
                        ) AS internal_transfer_bill_instance_id,
                        COALESCE(
                            itms.amount_cents,
                            itmd.amount_cents
                        ) AS internal_transfer_amount_cents,
                        itbi.bill_name_snapshot AS internal_transfer_bill_name
                    FROM bank_transactions bt
                    LEFT JOIN bank_accounts ba
                      ON ba.environment=bt.environment
                     AND ba.plaid_account_id=bt.plaid_account_id
                    LEFT JOIN transaction_reconciliations tr
                      ON tr.environment=bt.environment
                     AND tr.plaid_transaction_id=bt.plaid_transaction_id
                    LEFT JOIN bill_instances bi ON bi.id=tr.bill_instance_id
                    LEFT JOIN pay_periods pp ON pp.id=bi.pay_period_id
                    LEFT JOIN funding_transfer_validations ftv
                      ON ftv.environment=bt.environment
                     AND ftv.plaid_transaction_id=bt.plaid_transaction_id
                    LEFT JOIN internal_transfer_matches itms
                      ON itms.environment=bt.environment
                     AND itms.source_plaid_transaction_id=bt.plaid_transaction_id
                    LEFT JOIN internal_transfer_matches itmd
                      ON itmd.environment=bt.environment
                     AND itmd.destination_plaid_transaction_id=bt.plaid_transaction_id
                    LEFT JOIN bill_instances itbi
                      ON itbi.id=COALESCE(itms.bill_instance_id, itmd.bill_instance_id)
                    WHERE bt.environment=?{period_clause}
                    ORDER BY COALESCE(bt.posted_date, bt.authorized_date) DESC,
                             bt.plaid_transaction_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
            )

    def get_bank_transaction(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT
                    bt.*,
                    ba.name AS account_name,
                    ba.mask AS account_mask,
                    ba.account_type AS account_type,
                    ba.account_subtype AS account_subtype,
                    ba.is_bills_checking,
                    tr.disposition AS reconciliation_disposition,
                    tr.bill_instance_id AS reconciled_bill_instance_id,
                    bi.bill_name_snapshot AS reconciled_bill_name,
                    pp.year AS reconciled_year,
                    pp.month AS reconciled_month,
                    pp.cycle AS reconciled_cycle,
                    ftv.scope AS funding_validation_scope,
                    ftv.year AS funding_validation_year,
                    ftv.month AS funding_validation_month,
                    ftv.expected_cents AS funding_expected_cents,
                    ftv.actual_cents AS funding_actual_cents,
                    ftv.difference_cents AS funding_difference_cents,
                    CASE
                        WHEN itms.id IS NOT NULL THEN 'source'
                        WHEN itmd.id IS NOT NULL THEN 'destination'
                        ELSE NULL
                    END AS internal_transfer_role,
                    COALESCE(
                        itms.source_plaid_transaction_id,
                        itmd.source_plaid_transaction_id
                    ) AS internal_transfer_source_transaction_id,
                    COALESCE(
                        itms.destination_plaid_transaction_id,
                        itmd.destination_plaid_transaction_id
                    ) AS internal_transfer_destination_transaction_id,
                    COALESCE(
                        itms.bill_instance_id,
                        itmd.bill_instance_id
                    ) AS internal_transfer_bill_instance_id,
                    COALESCE(
                        itms.amount_cents,
                        itmd.amount_cents
                    ) AS internal_transfer_amount_cents,
                    itbi.bill_name_snapshot AS internal_transfer_bill_name
                FROM bank_transactions bt
                LEFT JOIN bank_accounts ba
                  ON ba.environment=bt.environment
                 AND ba.plaid_account_id=bt.plaid_account_id
                LEFT JOIN transaction_reconciliations tr
                  ON tr.environment=bt.environment
                 AND tr.plaid_transaction_id=bt.plaid_transaction_id
                LEFT JOIN bill_instances bi ON bi.id=tr.bill_instance_id
                LEFT JOIN pay_periods pp ON pp.id=bi.pay_period_id
                LEFT JOIN funding_transfer_validations ftv
                  ON ftv.environment=bt.environment
                 AND ftv.plaid_transaction_id=bt.plaid_transaction_id
                LEFT JOIN internal_transfer_matches itms
                  ON itms.environment=bt.environment
                 AND itms.source_plaid_transaction_id=bt.plaid_transaction_id
                LEFT JOIN internal_transfer_matches itmd
                  ON itmd.environment=bt.environment
                 AND itmd.destination_plaid_transaction_id=bt.plaid_transaction_id
                LEFT JOIN bill_instances itbi
                  ON itbi.id=COALESCE(itms.bill_instance_id, itmd.bill_instance_id)
                WHERE bt.environment=? AND bt.plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()

    def get_reconciliation(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT
                    tr.*,
                    bi.bill_name_snapshot,
                    pp.year,
                    pp.month,
                    pp.cycle
                FROM transaction_reconciliations tr
                LEFT JOIN bill_instances bi ON bi.id=tr.bill_instance_id
                LEFT JOIN pay_periods pp ON pp.id=bi.pay_period_id
                WHERE tr.environment=? AND tr.plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()

    def get_funding_transfer_validation(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT *
                FROM funding_transfer_validations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()

    def validate_funding_transfer(
        self,
        *,
        environment: str,
        plaid_transaction_id: str,
        year: int,
        month: int,
        scope: str,
        expected_cents: int,
        actual_cents: int,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        if scope not in {"1st", "15th", "month"}:
            raise ValueError("scope must be 1st, 15th, or month")
        if expected_cents < 0 or actual_cents < 0:
            raise ValueError("Funding transfer amounts must be non-negative.")

        with self.transaction() as conn:
            tx = conn.execute(
                """
                SELECT bt.*, ba.is_bills_checking
                FROM bank_transactions bt
                LEFT JOIN bank_accounts ba
                  ON ba.environment=bt.environment
                 AND ba.plaid_account_id=bt.plaid_account_id
                WHERE bt.environment=? AND bt.plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            if tx is None:
                raise ValueError("Transaction was not found.")
            if int(tx["pending"]):
                raise ValueError("Pending transfers cannot be validated.")
            if int(tx["amount_cents"]) >= 0:
                raise ValueError("Funding validation requires an incoming transfer.")
            if not int(tx["is_bills_checking"] or 0):
                raise ValueError(
                    "Funding validation requires an incoming transaction to the designated Bills Checking account."
                )
            if abs(int(tx["amount_cents"])) != int(actual_cents):
                raise ValueError("Actual transfer amount no longer matches the bank transaction.")

            now = utc_now()
            conn.execute(
                """
                INSERT INTO funding_transfer_validations(
                    environment, plaid_transaction_id, year, month, scope,
                    expected_cents, actual_cents, difference_cents,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment, plaid_transaction_id) DO UPDATE SET
                    year=excluded.year,
                    month=excluded.month,
                    scope=excluded.scope,
                    expected_cents=excluded.expected_cents,
                    actual_cents=excluded.actual_cents,
                    difference_cents=excluded.difference_cents,
                    updated_at=excluded.updated_at
                """,
                (
                    environment,
                    plaid_transaction_id,
                    int(year),
                    int(month),
                    scope,
                    int(expected_cents),
                    int(actual_cents),
                    int(actual_cents) - int(expected_cents),
                    now,
                    now,
                ),
            )

    def undo_funding_transfer_validation(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                DELETE FROM funding_transfer_validations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            )

    def reconcile_transaction(
        self,
        *,
        environment: str,
        plaid_transaction_id: str,
        bill_instance_id: int,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")

        with self.transaction() as conn:
            tx = conn.execute(
                """
                SELECT bt.*, ba.name AS account_name, ba.mask AS account_mask,
                       ba.account_type, ba.account_subtype, ba.is_bills_checking
                FROM bank_transactions bt
                LEFT JOIN bank_accounts ba
                  ON ba.environment=bt.environment
                 AND ba.plaid_account_id=bt.plaid_account_id
                WHERE bt.environment=? AND bt.plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            if tx is None:
                raise ValueError("Transaction was not found.")
            if int(tx["pending"]):
                raise ValueError("Pending transactions cannot be reconciled.")
            if int(tx["amount_cents"]) <= 0:
                raise ValueError("Only posted outflows can be reconciled to bills.")
            target = conn.execute(
                """
                SELECT bi.*, pp.year, pp.month, pp.cycle
                FROM bill_instances bi
                JOIN pay_periods pp ON pp.id=bi.pay_period_id
                WHERE bi.id=?
                """,
                (bill_instance_id,),
            ).fetchone()
            if target is None:
                raise ValueError("Target bill instance was not found.")

            existing_for_bill = conn.execute(
                """
                SELECT plaid_transaction_id
                FROM transaction_reconciliations
                WHERE environment=? AND disposition='matched'
                  AND bill_instance_id=? AND plaid_transaction_id<>?
                """,
                (environment, bill_instance_id, plaid_transaction_id),
            ).fetchone()
            if existing_for_bill is not None:
                raise ValueError(
                    "That bill instance is already reconciled to another transaction. "
                    "Undo the existing match first."
                )

            existing = conn.execute(
                """
                SELECT * FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()

            now = utc_now()
            if existing is not None and existing["disposition"] == "matched":
                old_bill_id = existing["bill_instance_id"]
                if old_bill_id is not None and int(old_bill_id) != bill_instance_id:
                    conn.execute(
                        """
                        UPDATE bill_instances
                        SET paid_cents=?, status=?, source=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            existing["prior_paid_cents"],
                            existing["prior_status"],
                            existing["prior_source"] or "app",
                            now,
                            int(old_bill_id),
                        ),
                    )
                    existing = None

            if existing is None or existing["disposition"] != "matched" or int(existing["bill_instance_id"] or 0) != bill_instance_id:
                prior_paid = target["paid_cents"]
                prior_status = target["status"]
                prior_source = target["source"]
            else:
                prior_paid = existing["prior_paid_cents"]
                prior_status = existing["prior_status"]
                prior_source = existing["prior_source"]

            conn.execute(
                """
                UPDATE bill_instances
                SET paid_cents=?, status='Paid', source='bank-reconciled', updated_at=?
                WHERE id=?
                """,
                (int(tx["amount_cents"]), now, bill_instance_id),
            )
            conn.execute(
                """
                INSERT INTO transaction_reconciliations(
                    environment, plaid_transaction_id, disposition, bill_instance_id,
                    prior_paid_cents, prior_status, prior_source, created_at, updated_at
                )
                VALUES (?, ?, 'matched', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment, plaid_transaction_id) DO UPDATE SET
                    disposition='matched',
                    bill_instance_id=excluded.bill_instance_id,
                    prior_paid_cents=excluded.prior_paid_cents,
                    prior_status=excluded.prior_status,
                    prior_source=excluded.prior_source,
                    updated_at=excluded.updated_at
                """,
                (
                    environment,
                    plaid_transaction_id,
                    bill_instance_id,
                    prior_paid,
                    prior_status,
                    prior_source,
                    now,
                    now,
                ),
            )

    def record_internal_transfer_match(
        self,
        *,
        environment: str,
        source_plaid_transaction_id: str,
        destination_plaid_transaction_id: str,
        bill_instance_id: int,
        amount_cents: int,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        if source_plaid_transaction_id == destination_plaid_transaction_id:
            raise ValueError("Internal transfer source and destination must differ.")

        with self.transaction() as conn:
            source = conn.execute(
                """
                SELECT * FROM bank_transactions
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, source_plaid_transaction_id),
            ).fetchone()
            destination = conn.execute(
                """
                SELECT * FROM bank_transactions
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, destination_plaid_transaction_id),
            ).fetchone()
            if source is None or destination is None:
                raise ValueError("Both internal-transfer transactions must exist.")
            if int(source["pending"]) or int(destination["pending"]):
                raise ValueError(
                    "Pending transactions cannot verify an internal transfer."
                )
            if (
                int(source["amount_cents"]) <= 0
                or int(destination["amount_cents"]) >= 0
            ):
                raise ValueError(
                    "Internal transfer must be an outgoing source and "
                    "incoming destination."
                )

            amount = int(amount_cents)
            if (
                int(source["amount_cents"]) != amount
                or abs(int(destination["amount_cents"])) != amount
            ):
                raise ValueError(
                    "Internal transfer transaction amounts do not agree."
                )

            reconciliation = conn.execute(
                """
                SELECT * FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                  AND disposition='matched' AND bill_instance_id=?
                """,
                (
                    environment,
                    source_plaid_transaction_id,
                    bill_instance_id,
                ),
            ).fetchone()
            if reconciliation is None:
                raise ValueError(
                    "Outgoing transfer must already be reconciled "
                    "to the target bill."
                )

            conflict = conn.execute(
                """
                SELECT * FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id IN (?, ?)
                    OR destination_plaid_transaction_id IN (?, ?)
                  )
                """,
                (
                    environment,
                    source_plaid_transaction_id,
                    destination_plaid_transaction_id,
                    source_plaid_transaction_id,
                    destination_plaid_transaction_id,
                ),
            ).fetchone()
            if conflict is not None:
                same = (
                    str(conflict["source_plaid_transaction_id"])
                    == source_plaid_transaction_id
                    and str(conflict["destination_plaid_transaction_id"])
                    == destination_plaid_transaction_id
                    and int(conflict["bill_instance_id"])
                    == int(bill_instance_id)
                )
                if not same:
                    raise ValueError(
                        "One side of this internal transfer is already paired "
                        "elsewhere."
                    )
                conn.execute(
                    """
                    UPDATE internal_transfer_matches
                    SET amount_cents=?, updated_at=?
                    WHERE id=?
                    """,
                    (amount, utc_now(), int(conflict["id"])),
                )
                return

            now = utc_now()
            conn.execute(
                """
                INSERT INTO internal_transfer_matches(
                    environment,
                    source_plaid_transaction_id,
                    destination_plaid_transaction_id,
                    bill_instance_id,
                    amount_cents,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    environment,
                    source_plaid_transaction_id,
                    destination_plaid_transaction_id,
                    bill_instance_id,
                    amount,
                    now,
                    now,
                ),
            )

    def get_internal_transfer_match(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT itm.*, bi.bill_name_snapshot
                FROM internal_transfer_matches itm
                JOIN bill_instances bi ON bi.id=itm.bill_instance_id
                WHERE itm.environment=?
                  AND (
                    itm.source_plaid_transaction_id=?
                    OR itm.destination_plaid_transaction_id=?
                  )
                """,
                (
                    environment,
                    plaid_transaction_id,
                    plaid_transaction_id,
                ),
            ).fetchone()

    def ignore_transaction(self, environment: str, plaid_transaction_id: str) -> None:
        with self.transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            now = utc_now()
            if existing is not None and existing["disposition"] == "matched":
                bill_id = existing["bill_instance_id"]
                if bill_id is not None:
                    conn.execute(
                        """
                        UPDATE bill_instances
                        SET paid_cents=?, status=?, source=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            existing["prior_paid_cents"],
                            existing["prior_status"],
                            existing["prior_source"] or "app",
                            now,
                            int(bill_id),
                        ),
                    )

            tx = conn.execute(
                """
                SELECT 1 FROM bank_transactions
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            if tx is None:
                raise ValueError("Transaction was not found.")

            conn.execute(
                """
                DELETE FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id=?
                    OR destination_plaid_transaction_id=?
                  )
                """,
                (
                    environment,
                    plaid_transaction_id,
                    plaid_transaction_id,
                ),
            )

            conn.execute(
                """
                INSERT INTO transaction_reconciliations(
                    environment, plaid_transaction_id, disposition, bill_instance_id,
                    prior_paid_cents, prior_status, prior_source, created_at, updated_at
                )
                VALUES (?, ?, 'ignored', NULL, NULL, NULL, NULL, ?, ?)
                ON CONFLICT(environment, plaid_transaction_id) DO UPDATE SET
                    disposition='ignored',
                    bill_instance_id=NULL,
                    prior_paid_cents=NULL,
                    prior_status=NULL,
                    prior_source=NULL,
                    updated_at=excluded.updated_at
                """,
                (environment, plaid_transaction_id, now, now),
            )

    def undo_reconciliation(
        self,
        environment: str,
        plaid_transaction_id: str,
    ) -> None:
        with self.transaction() as conn:
            requested_transaction_id = plaid_transaction_id
            internal = conn.execute(
                """
                SELECT * FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id=?
                    OR destination_plaid_transaction_id=?
                  )
                """,
                (
                    environment,
                    requested_transaction_id,
                    requested_transaction_id,
                ),
            ).fetchone()
            if (
                internal is not None
                and str(internal["destination_plaid_transaction_id"])
                == requested_transaction_id
            ):
                plaid_transaction_id = str(
                    internal["source_plaid_transaction_id"]
                )

            existing = conn.execute(
                """
                SELECT * FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            ).fetchone()
            if existing is None:
                raise ValueError("Transaction has no reconciliation to undo.")

            if (
                existing["disposition"] == "matched"
                and existing["bill_instance_id"] is not None
            ):
                conn.execute(
                    """
                    UPDATE bill_instances
                    SET paid_cents=?, status=?, source=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        existing["prior_paid_cents"],
                        existing["prior_status"],
                        existing["prior_source"] or "app",
                        utc_now(),
                        int(existing["bill_instance_id"]),
                    ),
                )

            conn.execute(
                """
                DELETE FROM transaction_reconciliations
                WHERE environment=? AND plaid_transaction_id=?
                """,
                (environment, plaid_transaction_id),
            )
            conn.execute(
                """
                DELETE FROM internal_transfer_matches
                WHERE environment=?
                  AND (
                    source_plaid_transaction_id IN (?, ?)
                    OR destination_plaid_transaction_id IN (?, ?)
                  )
                """,
                (
                    environment,
                    requested_transaction_id,
                    plaid_transaction_id,
                    requested_transaction_id,
                    plaid_transaction_id,
                ),
            )

    def get_sync_state(self, environment: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM sync_state WHERE environment=?",
                (environment,),
            ).fetchone()

    def update_sync_state(
        self,
        *,
        environment: str,
        transaction_cursor: str | None,
        transactions_update_status: str | None,
        last_sync_at: str | None,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sync_state(
                    environment, transaction_cursor,
                    transactions_update_status, last_sync_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(environment) DO UPDATE SET
                    transaction_cursor=excluded.transaction_cursor,
                    transactions_update_status=excluded.transactions_update_status,
                    last_sync_at=excluded.last_sync_at
                """,
                (
                    environment,
                    transaction_cursor,
                    transactions_update_status,
                    last_sync_at,
                ),
            )

    def record_workbook_import(
        self,
        *,
        source_path: Path,
        source_sha256: str,
        source_size: int,
        legacy_year: int | None,
        setup_bills: int,
        bill_instances: int,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workbook_imports(
                    source_path, source_sha256, source_size, legacy_year,
                    imported_at, setup_bills, bill_instances
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_sha256, legacy_year) DO UPDATE SET
                    source_path=excluded.source_path,
                    source_size=excluded.source_size,
                    imported_at=excluded.imported_at,
                    setup_bills=excluded.setup_bills,
                    bill_instances=excluded.bill_instances
                """,
                (
                    str(source_path),
                    source_sha256,
                    source_size,
                    legacy_year,
                    utc_now(),
                    setup_bills,
                    bill_instances,
                ),
            )
