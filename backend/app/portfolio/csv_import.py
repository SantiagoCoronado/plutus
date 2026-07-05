"""Tolerant CSV transaction importer: preview → column mapping → commit.

The mapping maps *target fields* to *source columns*:
    {"ts": "date", "type": "type", "symbol": "major", "currency": "minor",
     "quantity": "amount", "price": "rate", "fees": "fee", "external_id": "tid"}
A "book" target ("btc_mxn"-style pairs) fills symbol and currency in one go.

Idempotency: every committed row carries an external_id — either the mapped id
column or a content hash — and re-imports land on the partial unique index
(account_id, external_id) as skipped duplicates. Bad rows are reported
per-row and never block the good ones.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Asset, Transaction
from app.models.transaction import ASSET_TRANSACTION_TYPES, TRANSACTION_TYPES

TARGET_FIELDS = (
    "ts",
    "type",
    "symbol",
    "book",
    "currency",
    "quantity",
    "price",
    "fees",
    "external_id",
    "note",
)

# header fragments → suggested target (english + spanish, checked in order)
HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "ts": ("date", "time", "fecha", "created"),
    "type": ("type", "tipo", "side", "operation", "operación"),
    "book": ("book", "market", "pair", "libro"),
    "symbol": ("symbol", "ticker", "asset", "major", "coin", "instrumento"),
    "currency": ("currency", "minor", "moneda", "divisa"),
    "quantity": ("quantity", "amount", "monto", "cantidad", "units", "shares"),
    "price": ("price", "rate", "precio", "tasa"),
    "fees": ("fee", "commission", "comisión", "comision"),
    "external_id": ("tid", "id", "reference", "folio", "order"),
    "note": ("note", "description", "descripción", "concepto"),
}

DEFAULT_TYPE_MAP = {
    "buy": "buy",
    "compra": "buy",
    "sell": "sell",
    "venta": "sell",
    "deposit": "deposit",
    "funding": "deposit",
    "depósito": "deposit",
    "deposito": "deposit",
    "withdrawal": "withdrawal",
    "retiro": "withdrawal",
    "dividend": "dividend",
    "dividendo": "dividend",
    "interest": "interest",
    "interés": "interest",
    "interes": "interest",
    "fee": "fee",
    "transfer_in": "transfer_in",
    "transfer_out": "transfer_out",
}

# NOTE: built from Bitso's documented export shape; the generic mapper is the
# fallback if Bitso changes headers. Data-driven on purpose — fixing it is a
# dict edit, not code.
PRESETS: dict[str, dict] = {
    "bitso": {
        "detect": frozenset({"tid", "type", "major", "minor", "amount", "rate"}),
        "mapping": {
            "external_id": "tid",
            "ts": "date",
            "type": "type",
            "symbol": "major",
            "currency": "minor",
            "quantity": "amount",
            "price": "rate",
            "fees": "fee",
        },
    },
}


@dataclass
class Preview:
    columns: list[str]
    sample_rows: list[dict]
    row_count: int
    preset: str | None
    suggested_mapping: dict[str, str]


@dataclass
class CommitResult:
    created: int = 0
    skipped_duplicates: int = 0
    errors: list[dict] = field(default_factory=list)


def parse_preview(content: str) -> Preview:
    rows, columns = _read_csv(content)
    preset = _detect_preset(columns)
    if preset is not None:
        suggested = dict(PRESETS[preset]["mapping"])
    else:
        suggested = _suggest_mapping(columns)
    return Preview(
        columns=columns,
        sample_rows=rows[:5],
        row_count=len(rows),
        preset=preset,
        suggested_mapping=suggested,
    )


def commit_rows(
    session: Session,
    *,
    account_id: int,
    content: str,
    mapping: dict[str, str],
    tz: str,
) -> CommitResult:
    rows, columns = _read_csv(content)
    result = CommitResult()
    missing = [col for col in mapping.values() if col not in columns]
    if missing:
        result.errors.append({"row": 0, "error": f"mapped columns not in file: {missing}"})
        return result

    assets = _asset_lookup(session)
    zone = ZoneInfo(tz)

    for line_no, raw in enumerate(rows, start=2):  # 1 is the header line
        try:
            record = _row_to_record(raw, mapping, assets, zone, account_id)
        except RowError as exc:
            result.errors.append({"row": line_no, "error": str(exc)})
            continue

        stmt = (
            pg_insert(Transaction.__table__)
            .values(**record)
            .on_conflict_do_nothing(
                # must name the partial unique index's predicate to target it
                index_elements=["account_id", "external_id"],
                index_where=text("external_id IS NOT NULL"),
            )
            # rowcount is unreliable for skipped conflicts through this driver;
            # RETURNING yields a row only when the insert actually happened
            .returning(Transaction.__table__.c.id)
        )
        if session.execute(stmt).first() is not None:
            result.created += 1
        else:
            result.skipped_duplicates += 1
    session.commit()
    return result


class RowError(Exception):
    pass


def _read_csv(content: str) -> tuple[list[dict], list[str]]:
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    columns = [(name or "").strip() for name in (reader.fieldnames or [])]
    reader.fieldnames = columns
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    return rows, columns


def _detect_preset(columns: list[str]) -> str | None:
    lowered = {c.lower() for c in columns}
    for name, preset in PRESETS.items():
        if preset["detect"] <= lowered:
            return name
    return None


def _suggest_mapping(columns: list[str]) -> dict[str, str]:
    suggested: dict[str, str] = {}
    for target, hints in HEADER_HINTS.items():
        for column in columns:
            lowered = column.lower()
            if any(hint in lowered for hint in hints):
                if target not in suggested and column not in suggested.values():
                    suggested[target] = column
                break
    # a book column replaces separate symbol/currency suggestions
    if "book" in suggested:
        suggested.pop("symbol", None)
        suggested.pop("currency", None)
    return suggested


def _asset_lookup(session: Session) -> dict[str, list[Asset]]:
    lookup: dict[str, list[Asset]] = {}
    for asset in session.scalars(select(Asset)).all():
        lookup.setdefault(asset.symbol.upper(), []).append(asset)
    return lookup


def _value(raw: dict, mapping: dict[str, str], target: str) -> str:
    column = mapping.get(target)
    if not column:
        return ""
    return (raw.get(column) or "").strip()


def _number(raw: dict, mapping: dict[str, str], target: str) -> float | None:
    text = _value(raw, mapping, target).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise RowError(f"{target}: '{text}' is not a number") from exc


def _row_to_record(
    raw: dict,
    mapping: dict[str, str],
    assets: dict[str, list[Asset]],
    zone: ZoneInfo,
    account_id: int,
) -> dict:
    type_text = _value(raw, mapping, "type").lower()
    txn_type = DEFAULT_TYPE_MAP.get(type_text, type_text)
    if txn_type not in TRANSACTION_TYPES:
        raise RowError(f"unrecognized transaction type '{type_text}'")

    ts_text = _value(raw, mapping, "ts")
    if not ts_text:
        raise RowError("missing date")
    try:
        ts = pd.to_datetime(ts_text)
    except (ValueError, pd.errors.ParserError) as exc:
        raise RowError(f"could not parse date '{ts_text}'") from exc
    if ts.tzinfo is None:
        ts = ts.tz_localize(zone)

    symbol_text = _value(raw, mapping, "symbol")
    currency = _value(raw, mapping, "currency").upper()
    book = _value(raw, mapping, "book")
    if book and "_" in book:
        base, quote = book.split("_", 1)
        symbol_text = symbol_text or base
        currency = currency or quote.upper()

    quantity = _number(raw, mapping, "quantity")
    if quantity is None or quantity <= 0:
        raise RowError("quantity must be a positive number")
    price = _number(raw, mapping, "price")
    fees = _number(raw, mapping, "fees") or 0.0

    asset_id = None
    if txn_type in ASSET_TRANSACTION_TYPES:
        if not symbol_text:
            raise RowError(f"{txn_type} rows need a symbol")
        matches = assets.get(symbol_text.upper(), [])
        if not matches:
            raise RowError(f"unknown symbol '{symbol_text}' — track the asset first")
        if len(matches) > 1:
            classes = sorted(a.asset_class for a in matches)
            raise RowError(f"symbol '{symbol_text}' is ambiguous across {classes}")
        asset_id = matches[0].id
        if txn_type in ("buy", "sell") and price is None:
            raise RowError(f"{txn_type} rows need a price")

    if not currency:
        raise RowError("missing currency")

    external_id = _value(raw, mapping, "external_id") or _content_hash(
        account_id, ts.isoformat(), txn_type, symbol_text, quantity, price, currency
    )

    return {
        "account_id": account_id,
        "asset_id": asset_id,
        "type": txn_type,
        "ts": ts.to_pydatetime(),
        "quantity": quantity,
        "price": price,
        "fees": fees,
        "currency": currency,
        "note": _value(raw, mapping, "note") or None,
        "external_id": external_id,
        "lot_links": None,
    }


def _content_hash(*parts) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]
