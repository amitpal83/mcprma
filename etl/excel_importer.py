"""Imports transactions from an HDFC-style Excel account statement into SQLite.

Expected input: a single-sheet .xls/.xlsx export with a header row containing
exactly the columns in EXPECTED_COLUMNS, followed by one row per transaction,
optionally followed by bank-generated footer/summary rows (opening balance,
GSTN boilerplate, etc.) which this module ignores.

Usage (from the project root, C:\\RMA):
    python -m etl.excel_importer data/Account_Statement_Sep26.xls 8552 --max-rows 20
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session, sessionmaker

from config.logging_config import configure_logging
from db.models import Account, Transaction
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)

DATE_FORMAT = "%d/%m/%y"

EXPECTED_COLUMNS = [
    "Date",
    "Narration",
    "Chq./Ref.No.",
    "Value Dt",
    "Withdrawal Amt.",
    "Deposit Amt.",
    "Closing Balance",
]


@dataclass
class ImportResult:
    inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), DATE_FORMAT).date()


def _parse_amount(value: object) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount value: {value!r}") from exc


def _load_transaction_rows(excel_path: Path, max_rows: int | None) -> pd.DataFrame:
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    df = pd.read_excel(excel_path, sheet_name=0, header=0)

    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Excel file missing expected columns: {missing}")

    df = df[EXPECTED_COLUMNS]
    if max_rows is not None:
        df = df.head(max_rows)
    return df


def import_statement(
    excel_path: Path,
    account_number: str,
    max_rows: int | None = 20,
    session_factory: sessionmaker | None = None,
) -> ImportResult:
    """Parse excel_path and load up to max_rows transactions for account_number.

    Duplicate rows (same account_number + reference_no + txn_date) are
    skipped rather than re-inserted, so this is safe to re-run.
    """
    factory = session_factory or SessionLocal
    df = _load_transaction_rows(excel_path, max_rows)
    result = ImportResult()

    with factory() as session:  # type: Session
        _get_or_create_account(session, account_number)

        for idx, row in df.iterrows():
            try:
                _import_row(session, account_number, idx, row, result)
            except Exception as exc:
                result.errors.append(f"Row {idx}: {exc}")
                logger.error("Row %s failed to import: %s", idx, exc)

        session.commit()

    logger.info(
        "Import complete: inserted=%s skipped=%s errors=%s",
        result.inserted,
        result.skipped,
        len(result.errors),
    )
    return result


def _get_or_create_account(session: Session, account_number: str) -> Account:
    account = session.get(Account, account_number)
    if account is None:
        account = Account(account_number=account_number)
        session.add(account)
        session.flush()
    return account


def _import_row(session: Session, account_number: str, idx: int, row: pd.Series, result: ImportResult) -> None:
    txn_date = _parse_date(row["Date"])
    value_date = _parse_date(row["Value Dt"])
    withdrawal = _parse_amount(row["Withdrawal Amt."])
    deposit = _parse_amount(row["Deposit Amt."])
    closing_balance = _parse_amount(row["Closing Balance"])
    if closing_balance is None:
        raise ValueError("Closing balance is required")

    reference_no = None if pd.isna(row["Chq./Ref.No."]) else str(row["Chq./Ref.No."]).strip()
    narration = str(row["Narration"]).strip()

    existing = (
        session.query(Transaction)
        .filter_by(account_number=account_number, reference_no=reference_no, txn_date=txn_date)
        .first()
    )
    if existing is not None:
        result.skipped += 1
        logger.info("Row %s: duplicate transaction skipped (ref=%s)", idx, reference_no)
        return

    session.add(
        Transaction(
            account_number=account_number,
            txn_date=txn_date,
            value_date=value_date,
            merchant=narration,
            reference_no=reference_no,
            withdrawal_amount=withdrawal,
            deposit_amount=deposit,
            closing_balance=closing_balance,
        )
    )
    result.inserted += 1


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(description="Import an account statement Excel file into SQLite")
    parser.add_argument("excel_path", type=Path)
    parser.add_argument("account_number")
    parser.add_argument("--max-rows", type=int, default=20)
    args = parser.parse_args()

    init_db()
    result = import_statement(args.excel_path, args.account_number, args.max_rows)

    print(f"Inserted: {result.inserted}, Skipped: {result.skipped}, Errors: {len(result.errors)}")
    for err in result.errors:
        print(" -", err)


if __name__ == "__main__":
    main()
