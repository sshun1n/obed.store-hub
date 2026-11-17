"""Реестр реализаций из 1С и сверка адресов. Перенесено из ka-webapp (upd_registry, saby.same_address).

Реестр «Реализация (акт, накладная, УПД)» — источник номеров УПД: номер живёт
только в 1С, интеграции с её облаком нет. У Обеда реестр свой: 1С отбирает
строки по ответственному (образец за 02–08.11.2026, 66 строк).

С 19.11.2026 реестр выгружается общим, с колонкой «Контрагент»: точки Самоката и
Лавки бывают по одному адресу, и сверка только по адресу путала их. Строка
относится к покупателю, у которого «Наименование в 1С» совпало с «Контрагентом»;
строки других контрагентов не загружаются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

import xlrd


class RegistryError(Exception):
    pass


@dataclass
class RegistryRow:
    day: date
    number: str
    amount: Decimal
    address: str
    party: str = ""      # колонка «Контрагент»; пусто в старом реестре


def _cell(sheet, row: int, col: int) -> str:
    try:
        return str(sheet.cell_value(row, col)).strip()
    except IndexError:
        return ""


def _day(value: str) -> Optional[date]:
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _amount(value: str) -> Optional[Decimal]:
    text = value.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_registry(content: bytes) -> List[RegistryRow]:
    """Шапку ищем по названиям колонок: сверху заголовок с организацией и отбором, высота гуляет."""
    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception as exc:
        raise RegistryError("Не удалось открыть файл — ожидается .xls из 1С") from exc
    sheet = book.sheet_by_index(0)
    header_row, columns = None, {}
    for row in range(min(sheet.nrows, 30)):
        titles = {_cell(sheet, row, col).lower(): col for col in range(sheet.ncols)}
        if "номер" in titles and "сумма" in titles and "дата" in titles:
            header_row, columns = row, titles
            break
    if header_row is None:
        raise RegistryError("Не нашёл шапку реестра: нужны колонки «Дата», «Номер», «Сумма» и «Комментарий»")

    rows = []
    for row in range(header_row + 1, sheet.nrows):
        if _cell(sheet, row, 0).lower().startswith("итого"):
            break
        day = _day(_cell(sheet, row, columns["дата"]))
        number = _cell(sheet, row, columns["номер"])
        amount = _amount(_cell(sheet, row, columns["сумма"]))
        if day is None or not number or amount is None:
            continue
        if number.endswith(".0"):   # номер приходит числом: «5213.0»
            number = number[:-2]
        rows.append(RegistryRow(day, number, amount, _cell(sheet, row, columns.get("комментарий", -1)),
                                _cell(sheet, row, columns["контрагент"]) if "контрагент" in columns else ""))
    if not rows:
        raise RegistryError("В реестре нет ни одной строки с датой, номером и суммой")
    return rows


def party_key(name: str) -> str:
    """«Умный Ритейл ООО» и «умный  ритейл ООО» — один контрагент."""
    return " ".join((name or "").lower().replace("ё", "е").replace('"', " ").replace("«", " ").replace("»", " ").split())


def rows_for(db, day: date, inn: str):
    """Строки реестра дня для покупателя: его и старые, без контрагента (их сверяем по адресу)."""
    from src import database
    return (db.query(database.UpdRow)
            .filter(database.UpdRow.day == day,
                    (database.UpdRow.buyer_inn.is_(None)) | (database.UpdRow.buyer_inn == inn))
            .order_by(database.UpdRow.number).all())


LAVKA_INN = "9718101499"


# Сверка адресов: дом с корпусом и значимые слова. Пишут по-разному —
# «Анкудиновское шоссе, 40» и «Анкудиновское ш, дом 40», «Бориса Панина, 5к6» и
# «Бориса Панина ул, дом 5, корпус 6», — поэтому сравниваем смысл, а не строки
HOUSE_PREFIX_RE = re.compile(r"\b(?:дом|д)\.?\s*(?=\d)", re.IGNORECASE)
CORPUS_RE = re.compile(r"(\d)\s*(?:корпус|корп|к)\.?\s*(\d)", re.IGNORECASE)
HOUSE_RE = re.compile(r"\b(\d+[а-я]?)\b", re.IGNORECASE)
INDEX_RE = re.compile(r"\b\d{6}\b")
WORD_RE = re.compile(r"[а-яё]{4,}", re.IGNORECASE)
COMMON = {"область", "обл", "город", "нижний", "новгород", "улица", "шоссе", "бульвар",
          "набережная", "проезд", "корпус", "квартал", "нижегородская"}


# «Спутника 11 в» в 1С и «Спутника ул, дом 11В» в заказе — один дом
LETTER_RE = re.compile(r"(\d)\s+([а-я])(?![а-я])", re.IGNORECASE)
# «6 мкр дом 21» и «6-й мкр, дом 21»: «мкр» короче четырёх букв и иначе не считается словом
DISTRICT_RE = re.compile(r"\b(?:мкр|микрорайон)[а-я]*\.?", re.IGNORECASE)


def _fingerprint(address: str):
    text = (address or "").lower().replace("ё", "е")
    text = INDEX_RE.sub(" ", text)
    text = DISTRICT_RE.sub(" микрорайон ", text)
    text = LETTER_RE.sub(r"\1\2", text)
    text = HOUSE_PREFIX_RE.sub(" ", text)
    text = CORPUS_RE.sub(r"\1-\2", text)
    house = "-".join(HOUSE_RE.findall(text))
    return house, frozenset(set(WORD_RE.findall(text)) - COMMON)


def same_address(left: str, right: str) -> bool:
    left_house, left_words = _fingerprint(left)
    right_house, right_words = _fingerprint(right)
    if not left_house or left_house != right_house:
        return False
    return bool(left_words & right_words)
