"""Разбор файла «Заказ поставщику на DD.MM.YYYY.xlsx» от Яндекс.Лавки.

Перенесено из ka-webapp (yandex_parser): формат у Обеда тот же, проверено
на файле от 10.11.2026. Файл — плоская таблица 1С, шапка в первой строке,
по одному заказу на лавку, ключ — «Номер заказа» вида ЯЛ261226308.

Читаем из байтов: файл нигде не сохраняется.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from math import isfinite
from typing import Dict, List, Optional

import openpyxl

COLUMNS = {
    "supplier_inn": "ИНН поставщика",
    "order_no": "Номер заказа",
    "order_date": "Дата заказа",
    "delivery_date": "Дата поступления заказа",
    "point_external_id": "Склад ИД",
    "point_name": "Название склада",
    "point_address": "Адрес склада",
    "barcode": "Штрихкод",
    "vendor_code": "Артикул поставщика",
    "lavka_id": "Лавка ИД",
    "name": "Номенклатура",
    "quantity": "Количество",
    "price_net": "Цена штуки без НДС",
    "price_gross": "Цена штуки с НДС",
    "sum_net": "Сумма без НДС",
    "vat_rate": "НДС",
    "sum_gross": "Сумма с НДС",
    "buyer_inn": "ИНН покупателя",
}
REQUIRED = ("order_no", "delivery_date", "point_external_id", "barcode", "name", "quantity")

# Тип файла — по шапке, а не по имени: одним ящиком идут заказы и отчёты Лавки
KIND_ORDERS = "orders"
KIND_SERVICE = "service"     # «Отчет по уровню сервиса»
KIND_METRICS = "metrics"     # еженедельные метрики, отзывы, глоссарий
ORDER_MARKERS = {"номер заказа", "штрихкод", "номенклатура"}
SERVICE_MARKERS = {"order doc number", "fact_qnt", "plan_qnt"}

# Марка в названии: «Обедстор», «Обед.Стор», в кавычках и без. Различать товары не помогает
BRAND_RE = re.compile(r"[«\"]?\s*обед\.?\s*стор\s*[»\"]?", re.IGNORECASE)
# Невидимые символы: U+200B сидит после марки и ломает сверку и группировку
INVISIBLE = dict.fromkeys(map(ord, "​‌‍﻿­"), None)


class ParseError(Exception):
    pass


@dataclass
class ParsedLine:
    barcode: str
    vendor_code: str
    lavka_id: str
    name: str
    name_raw: str
    quantity: int
    price_net: Decimal
    price_gross: Decimal
    sum_net: Decimal
    sum_gross: Decimal
    vat_rate: Decimal


@dataclass
class ParsedOrder:
    order_no: str
    order_date: Optional[date]
    delivery_date: date
    point_external_id: str
    point_name: str
    point_address: str
    supplier_inn: str = ""
    buyer_inn: str = ""
    lines: List[ParsedLine] = field(default_factory=list)

    @property
    def total_quantity(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def total_net(self) -> Decimal:
        return sum((line.sum_net for line in self.lines), Decimal("0"))

    @property
    def total_gross(self) -> Decimal:
        return sum((line.sum_gross for line in self.lines), Decimal("0"))


def clean_name(value: object) -> str:
    """Без невидимых символов, марки и лишних пробелов: «Сырники с курагой…, 180 г»."""
    text = str(value or "").translate(INVISIBLE)
    text = BRAND_RE.sub(" ", text)
    text = " ".join(text.split())
    return re.sub(r"\s+([,.])", r"\1", text).strip(" ,")


def _text(value: object) -> str:
    return str(value if value is not None else "").translate(INVISIBLE).strip()


def _decimal(value: object) -> Decimal:
    """Числа приходят вперемешку: «172,00» строкой рядом с 180.6 числом."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value if value.is_finite() else Decimal("0")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if isfinite(value) else Decimal("0")
    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation:
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _int(value: object) -> int:
    return int(_decimal(value))


def _date(value: object) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def date_from_filename(filename: str) -> Optional[date]:
    """Дата из «Заказ поставщику на 10.11.2026.xlsx» — если колонка даты пуста."""
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", filename or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _open(content: bytes):
    try:
        return openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # zip, xml — что угодно из недр openpyxl
        raise ParseError(f"Не удалось открыть файл: {exc}") from exc


def _first_row(sheet) -> tuple:
    for row in sheet.iter_rows(max_row=1, values_only=True):
        return row
    return ()


def detect_kind(content: bytes) -> str:
    """Что за файл: заказ, отчёт по уровню сервиса, метрики. Пусто — не Лавки."""
    book = _open(content)
    try:
        names = {name.lower() for name in book.sheetnames}
        if {"metrics", "feedback"} <= names:
            return KIND_METRICS
        header = {_text(cell).lower() for cell in _first_row(book.worksheets[0]) if cell is not None}
        if SERVICE_MARKERS <= header:
            return KIND_SERVICE
        if ORDER_MARKERS <= header:
            return KIND_ORDERS
        return ""
    finally:
        book.close()


def _map_header(row: tuple) -> Dict[str, int]:
    """Номера колонок по названиям: Лавка может добавить колонку, и порядок съедет."""
    header = {_text(cell).lower(): idx for idx, cell in enumerate(row) if cell is not None}
    mapping = {key: header[title.lower()] for key, title in COLUMNS.items() if title.lower() in header}
    missing = [COLUMNS[key] for key in REQUIRED if key not in mapping]
    if missing:
        raise ParseError("В файле нет обязательных колонок: " + ", ".join(missing))
    return mapping


def parse_orders(content: bytes, filename: str = "") -> List[ParsedOrder]:
    """По одному ParsedOrder на номер заказа."""
    book = _open(content)
    try:
        rows = book.worksheets[0].iter_rows(values_only=True)
        try:
            mapping = _map_header(next(rows))
        except StopIteration:
            raise ParseError("Файл пустой") from None

        fallback_date = date_from_filename(filename)
        orders: Dict[str, ParsedOrder] = {}

        def cell(row: tuple, key: str) -> object:
            idx = mapping.get(key)
            return row[idx] if idx is not None and idx < len(row) else None

        for row in rows:
            order_no = _text(cell(row, "order_no"))
            name_raw = _text(cell(row, "name"))
            quantity = _int(cell(row, "quantity"))
            # Итоговые строки и пустые хвосты 1С: без номера или с нулевым количеством
            if not order_no or not name_raw or quantity <= 0:
                continue

            order = orders.get(order_no)
            if order is None:
                delivery = _date(cell(row, "delivery_date")) or fallback_date
                if delivery is None:
                    raise ParseError(f"У заказа {order_no} не удалось определить дату поступления")
                order = ParsedOrder(
                    order_no=order_no,
                    order_date=_date(cell(row, "order_date")),
                    delivery_date=delivery,
                    point_external_id=_text(cell(row, "point_external_id")),
                    point_name=_text(cell(row, "point_name")),
                    point_address=_text(cell(row, "point_address")),
                    supplier_inn=_text(cell(row, "supplier_inn")),
                    buyer_inn=_text(cell(row, "buyer_inn")),
                )
                if not order.point_external_id:
                    raise ParseError(f"У заказа {order_no} не указан «Склад ИД»")
                orders[order_no] = order

            price_net = _decimal(cell(row, "price_net"))
            price_gross = _decimal(cell(row, "price_gross"))
            order.lines.append(ParsedLine(
                barcode=_text(cell(row, "barcode")),
                vendor_code=_text(cell(row, "vendor_code")),
                lavka_id=_text(cell(row, "lavka_id")),
                name=clean_name(name_raw),
                name_raw=name_raw,
                quantity=quantity,
                price_net=price_net,
                price_gross=price_gross,
                sum_net=_decimal(cell(row, "sum_net")) or price_net * quantity,
                sum_gross=_decimal(cell(row, "sum_gross")) or price_gross * quantity,
                vat_rate=_decimal(cell(row, "vat_rate")),
            ))
    finally:
        book.close()

    if not orders:
        raise ParseError("В файле не нашлось ни одной строки заказа")
    return list(orders.values())
