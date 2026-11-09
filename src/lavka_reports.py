"""Отчёты Яндекс.Лавки: «Еженедельный отчет метрик для поставщика» и что из него считаем.

Файл suppliers_report.xlsx приходит от robot-sla-report: лист Metrics — недельные
метрики по позициям (списания, продажи, SLA, лайки), лист Feedback — отзывы
покупателей, Glossary — расшифровки (не храним). Разбор перенесён из ka-webapp
(yandex_reports), формат у Обеда тот же, проверено на отчёте от 29.09.2026.

Отчёт по уровню сервиса здесь не разбирается: владелец решил, что пока нужны
только отзывы и списания.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from src import database
from src.lavka_parser import ParseError, _date, _decimal, _int, _open, _text, clean_name

# Колонка недели: «2026-09-02 - Writeoffs%». Столбцы «Total - …» пропускаем: итог
# считаем сами из тех же недель. Дату приводим к понедельнику: Лавка ставит день
# рассылки, а он гуляет, и одна неделя приезжала бы под двумя датами
WEEK_COLUMN_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s*-\s*(.+)$")
METRIC_FIELDS = {
    "cogs": "cogs",
    "writeoffs%": "writeoffs_pct",
    "sla%": "sla_pct",
    "sales quantity": "sales_qty",
    "like share": "like_share",
    "retention in item%": "retention_pct",
}
# Отчёт старше этого — данных давно нет, страница говорит об этом прямо
STALE_AFTER_DAYS = 10


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class MetricRow:
    week_start: date
    lavka_id: str
    name: str
    category: str
    subcategory: str
    cogs: Decimal = Decimal("0")
    sales_qty: int = 0
    writeoffs_pct: Optional[Decimal] = None
    sla_pct: Optional[Decimal] = None
    like_share: Optional[Decimal] = None
    retention_pct: Optional[Decimal] = None

    @property
    def empty(self) -> bool:
        """Неделя, когда позиции не было в ассортименте: Лавка ставит нули во всё."""
        return not self.sales_qty and not self.cogs and not self.sla_pct and not self.writeoffs_pct


@dataclass
class FeedbackRow:
    fingerprint: str
    feedback_date: date
    order_date: Optional[date]
    lavka_id: str
    name: str
    subcategory: str
    liked: bool
    comment: str


@dataclass
class ParsedMetrics:
    metrics: List[MetricRow] = field(default_factory=list)
    feedback: List[FeedbackRow] = field(default_factory=list)


def _ratio(value: object) -> Optional[Decimal]:
    """Процент из отчёта. Пусто, inf и nan — None, а не ноль."""
    if value is None or _text(value) == "":
        return None
    if isinstance(value, float) and value != value:
        return None
    if _text(value).lower().replace(",", ".").lstrip("+-") in ("inf", "infinity", "nan"):
        return None
    return _decimal(value)


def _header(sheet) -> Dict[str, int]:
    for row in sheet.iter_rows(max_row=1, values_only=True):
        return {_text(cell).lower(): idx for idx, cell in enumerate(row) if cell is not None}
    return {}


def _week_columns(head: tuple) -> Dict[int, tuple]:
    columns = {}
    for idx, cell in enumerate(head):
        match = WEEK_COLUMN_RE.match(_text(cell))
        if not match:
            continue
        field_name = METRIC_FIELDS.get(match.group(4).strip().lower())
        if not field_name:
            continue  # метрика, которой мы ещё не знаем
        try:
            week = monday(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
        columns[idx] = (week, field_name)
    return columns


def _parse_metrics_sheet(sheet) -> List[MetricRow]:
    rows = sheet.iter_rows(values_only=True)
    head = next(rows, None)
    if not head:
        return []
    header = {_text(cell).lower(): idx for idx, cell in enumerate(head) if cell is not None}
    week_columns = _week_columns(head)
    if not week_columns:
        raise ParseError("В листе метрик нет ни одной недельной колонки")

    def cell(row, key):
        idx = header.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    out = []
    for row in rows:
        lavka_id = _text(cell(row, "item id"))
        if not lavka_id:
            continue
        by_week: Dict[date, MetricRow] = {}
        for idx, (week, field_name) in week_columns.items():
            value = row[idx] if idx < len(row) else None
            item = by_week.get(week)
            if item is None:
                item = by_week[week] = MetricRow(
                    week_start=week, lavka_id=lavka_id, name=clean_name(cell(row, "item name")),
                    category=_text(cell(row, "category")), subcategory=_text(cell(row, "subcategory")))
            if field_name == "sales_qty":
                item.sales_qty = _int(value)
            elif field_name == "cogs":
                item.cogs = _decimal(value)
            else:
                setattr(item, field_name, _ratio(value))
        out.extend(item for item in by_week.values() if not item.empty)
    return out


def _parse_feedback_sheet(sheet) -> List[FeedbackRow]:
    header = _header(sheet)

    def cell(row, key):
        idx = header.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    out = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        when = _date(cell(row, "feedback_date"))
        if when is None:
            continue
        lavka_id = _text(cell(row, "item_id"))
        liked = _text(cell(row, "feedback")).lower().startswith("like")
        comment = _text(cell(row, "comment"))
        raw = f"{when.isoformat()}|{lavka_id}|{int(liked)}|{comment}"
        out.append(FeedbackRow(
            fingerprint=hashlib.sha256(raw.encode()).hexdigest(), feedback_date=when,
            order_date=_date(cell(row, "order_date")), lavka_id=lavka_id,
            name=clean_name(cell(row, "item_name")), subcategory=_text(cell(row, "lvl4_subcategory_name")),
            liked=liked, comment=comment))
    return out


def parse_metrics(content: bytes) -> ParsedMetrics:
    book = _open(content)
    try:
        sheets = {name.lower(): book[name] for name in book.sheetnames}
        if "metrics" not in sheets:
            raise ParseError("В файле нет листа Metrics")
        return ParsedMetrics(
            metrics=_parse_metrics_sheet(sheets["metrics"]),
            feedback=_parse_feedback_sheet(sheets["feedback"]) if "feedback" in sheets else [],
        )
    finally:
        book.close()


def save_metrics(db, parsed: ParsedMetrics, report_date: Optional[date]) -> tuple:
    """Пишет метрики и новые отзывы. Не коммитит. Возвращает (строк метрик, новых отзывов).

    Неделю, уже пришедшую в более свежем отчёте, старый отчёт не перетирает: история
    может вычитываться не по порядку, а цифры Лавка уточняет задним числом.
    """
    model = database.LavkaMetric
    weeks = {row.week_start for row in parsed.metrics}
    existing = {}
    if weeks:
        for row in db.query(model).filter(model.week_start.in_(weeks)):
            existing[(row.week_start, row.lavka_id)] = row
    saved = 0
    for item in parsed.metrics:
        row = existing.get((item.week_start, item.lavka_id))
        if row is not None and row.report_date and report_date and row.report_date > report_date:
            continue
        if row is None:
            row = model(week_start=item.week_start, lavka_id=item.lavka_id)
            db.add(row)
            existing[(item.week_start, item.lavka_id)] = row
        row.name = item.name[:300] or item.lavka_id
        row.category = item.category[:150] or None
        row.subcategory = item.subcategory[:150] or None
        row.cogs = item.cogs
        row.sales_qty = item.sales_qty
        row.writeoffs_pct = item.writeoffs_pct
        row.sla_pct = item.sla_pct
        row.like_share = item.like_share
        row.retention_pct = item.retention_pct
        row.report_date = report_date
        saved += 1

    fresh = 0
    prints = {f.fingerprint for f in parsed.feedback}
    known = set()
    if prints:
        known = {fp for (fp,) in db.query(database.LavkaFeedback.fingerprint)
                 .filter(database.LavkaFeedback.fingerprint.in_(prints))}
    for item in parsed.feedback:
        if item.fingerprint in known:
            continue
        known.add(item.fingerprint)
        db.add(database.LavkaFeedback(
            fingerprint=item.fingerprint, feedback_date=item.feedback_date, order_date=item.order_date,
            lavka_id=item.lavka_id or None, name=item.name[:300] or item.lavka_id,
            subcategory=item.subcategory[:150] or None, liked=item.liked, comment=item.comment or None))
        fresh += 1
    return saved, fresh


def weighted_writeoff(cells: List[tuple]) -> Optional[float]:
    """Средний процент списаний с весом по продажам: (процент, продано) по неделям.

    Неделя с тремя проданными штуками и 100 % списаний не должна весить столько же,
    сколько неделя с сотней. Пустые проценты в расчёт не берём: ноль был бы враньём.
    """
    pairs = [(float(pct), qty or 0) for pct, qty in cells if pct is not None]
    if not pairs:
        return None
    sold = sum(qty for _, qty in pairs)
    if sold:
        return sum(pct * qty for pct, qty in pairs) / sold
    return sum(pct for pct, _ in pairs) / len(pairs)
