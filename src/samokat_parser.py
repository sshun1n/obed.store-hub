"""Разбор заказа «Самоката» (ООО «Умный ритейл») из письма ordermanager@samokat.ru.

Файл «Заказ_Шунина К.С. ИП ЦФЗ НН_16.11.26.xlsx» — одна таблица на весь день:
строка 1 «Заказ на 18.11.26, ООО Умный ритейл», строка 2 — шапка, строки ниже —
товары. Первые колонки шапки: «Наименование|вес», «Арктикул», «Штрихкод»,
«Цена», «Итого сумма», …, «Итого шт.», дальше по колонке на точку, в шапке её
адрес, в ячейке — штуки. Номеров заказов УР- в файле нет: они приходят
отдельными ORDERS в СБИС, по одному на точку.

Проверено на файле от 16.11.2026: 15 товаров, 56 точек. Колонки ищем по
названиям, точки — всё, что правее последней колонки «Итого…».
Читаем из байтов: файл нигде не сохраняется.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from src.lavka_parser import ParseError, _decimal, _open, _text, clean_name

KIND_SAMOKAT = "samokat"

TITLE_RE = re.compile(r"заказ\s+на\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})", re.IGNORECASE)
FILE_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})")
INDEX_RE = re.compile(r"^\s*\d{6}\s*,\s*")
HEADER_ROWS = 5          # шапку ищем в первых строках
COLUMNS = {
    "name": ("наименование|вес", "наименование"),
    "vendor_code": ("арктикул", "артикул"),
    "barcode": ("штрихкод", "штрих-код"),
    "price_gross": ("цена",),
    "sum_gross": ("итого сумма",),
    "quantity": ("итого шт.", "итого шт"),
}
REQUIRED = ("name", "barcode", "quantity")
CENTS = Decimal("0.01")


@dataclass
class SamokatLine:
    barcode: str
    vendor_code: str
    name: str
    name_raw: str
    quantity: int
    price_gross: Decimal

    @property
    def sum_gross(self) -> Decimal:
        return (self.price_gross * self.quantity).quantize(CENTS)


@dataclass
class SamokatPointOrder:
    address: str
    lines: List[SamokatLine] = field(default_factory=list)

    @property
    def total_quantity(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def total_gross(self) -> Decimal:
        return sum((line.sum_gross for line in self.lines), Decimal("0"))


@dataclass
class SamokatFile:
    delivery_date: date
    order_date: Optional[date]
    buyer: str
    points: List[SamokatPointOrder] = field(default_factory=list)


def point_key(address: str) -> str:
    """Ключ точки: «603136, Нижегородская обл, …» и «Нижегородская обл, …» — одна точка.
    Индекс бывает не у всех адресов, регистр и пробелы — как придётся."""
    text = INDEX_RE.sub("", address or "")
    text = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[,.]", " ", text).split())


def _year(value: str) -> int:
    year = int(value)
    return year + 2000 if year < 100 else year


def _make_date(day: str, month: str, year: str) -> Optional[date]:
    try:
        return date(_year(year), int(month), int(day))
    except ValueError:
        return None


def _title_date(rows: List[tuple]) -> Optional[date]:
    for row in rows:
        for cell in row:
            match = TITLE_RE.search(_text(cell))
            if match:
                return _make_date(*match.groups())
    return None


def _header_index(rows: List[tuple]) -> Optional[int]:
    for idx, row in enumerate(rows):
        cells = [_text(c).lower() for c in row]
        if "штрихкод" in cells and any(c.startswith("итого шт") for c in cells):
            return idx
    return None


def _first_rows(content: bytes) -> List[tuple]:
    book = _open(content)
    try:
        sheet = book.worksheets[0]
        return [row for row in sheet.iter_rows(max_row=HEADER_ROWS, values_only=True)]
    finally:
        book.close()


def is_samokat(content: bytes) -> bool:
    """Заказ «Самоката» — по шапке: «Штрихкод», «Итого шт.» и заголовок «Заказ на …»."""
    try:
        rows = _first_rows(content)
    except ParseError:
        return False
    return _header_index(rows) is not None and _title_date(rows) is not None


def parse(content: bytes, filename: str = "") -> SamokatFile:
    book = _open(content)
    try:
        rows = list(book.worksheets[0].iter_rows(values_only=True))
    finally:
        book.close()

    head = rows[:HEADER_ROWS]
    header_at = _header_index(head)
    delivery = _title_date(head)
    if header_at is None or delivery is None:
        raise ParseError("Не похоже на заказ «Самоката»: нет заголовка «Заказ на …» или шапки со «Штрихкод» и «Итого шт.»")
    title = next((_text(c) for row in head for c in row if TITLE_RE.search(_text(c))), "")
    buyer = title.split(",", 1)[1].strip() if "," in title else ""

    header = [_text(c) for c in rows[header_at]]
    lowered = [h.lower() for h in header]
    mapping: Dict[str, int] = {}
    for key, titles in COLUMNS.items():
        for idx, cell in enumerate(lowered):
            if cell in titles:
                mapping[key] = idx
                break
    missing = [COLUMNS[key][0] for key in REQUIRED if key not in mapping]
    if missing:
        raise ParseError("В файле нет колонок: " + ", ".join(missing))

    # Точки — правее последней колонки «Итого…»
    last_total = max(idx for idx, cell in enumerate(lowered) if cell.startswith("итого"))
    point_cols = [(idx, header[idx]) for idx in range(last_total + 1, len(header)) if header[idx]]
    if not point_cols:
        raise ParseError("В шапке нет ни одной точки доставки")
    keys = [point_key(address) for _, address in point_cols]
    if len(set(keys)) != len(keys):
        raise ParseError("В шапке повторяется адрес точки")

    points = {idx: SamokatPointOrder(address=" ".join(address.split())) for idx, address in point_cols}

    def cell(row: tuple, key: str) -> object:
        idx = mapping.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    for number, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        barcode = _text(cell(row, "barcode")).replace(" ", "")
        name_raw = _text(cell(row, "name"))
        if not barcode or not name_raw:
            continue    # подзаголовок «Наименование» и пустые строки
        if barcode.endswith(".0"):
            barcode = barcode[:-2]
        price = _decimal(cell(row, "price_gross")).quantize(CENTS)
        line_total = 0
        for idx, _ in point_cols:
            value = row[idx] if idx < len(row) else None
            qty = _decimal(value)
            if qty <= 0:
                continue
            if qty != qty.to_integral_value():
                raise ParseError(f"Строка {number}: дробное количество {qty} у «{name_raw}»")
            qty = int(qty)
            line_total += qty
            points[idx].lines.append(SamokatLine(
                barcode=barcode, vendor_code=_text(cell(row, "vendor_code")).replace(" ", ""),
                name=clean_name(name_raw), name_raw=name_raw, quantity=qty, price_gross=price,
            ))
        # Сверка с «Итого шт.»: при расхождении данные лучше не брать вовсе
        stated = _decimal(cell(row, "quantity"))
        if stated and int(stated) != line_total:
            raise ParseError(f"Строка {number}: по точкам {line_total} шт., а в «Итого шт.» {int(stated)} — «{name_raw}»")

    order_date = None
    match = FILE_DATE_RE.search(filename or "")
    if match:
        order_date = _make_date(*match.groups())
    result = SamokatFile(delivery_date=delivery, order_date=order_date, buyer=buyer,
                         points=[p for p in points.values() if p.lines])
    if not result.points:
        raise ParseError("В заказе нет ни одной позиции")
    return result
