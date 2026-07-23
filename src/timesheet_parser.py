# Парсер "Табеля трудовой дисциплины" (выгрузка из системы учёта рабочего времени).
#
# Структура файла:
#   - Шапка: название отчёта, "Начало периода:", "Конец периода:"
#   - Глобальный заголовок: ФИО | Отдел | Должность | Таб. № | Данные
#   - Блоки по сотрудникам: строка с ФИО и подзаголовком
#     (Дата | Начала... | Окончания... | Фактическая наработка | График |
#      Наработка по графику | Нарушения при поздних... | Нарушения при ранних...),
#     затем строки по датам периода, затем сводка "Обобщенные данные..."
#
# Парсер не привязан к номерам колонок и количеству строк: колонки ищутся
# по текстам заголовков, блоки — по подзаголовку "Дата" рядом с ФИО.

import re
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

logger = logging.getLogger("TimesheetParser")

DATE_RE = re.compile(r'^(\d{2})\.(\d{2})\.(\d{4})')
TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})$')
VIOLATION_RE = re.compile(r'(\d{1,2}):(\d{2})')


class TimesheetParseError(Exception):
    """Файл не удалось распознать как табель."""
    pass


@dataclass
class DayEntry:
    work_date: date
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    worked_minutes: int = 0
    schedule_minutes: Optional[int] = None
    schedule_worked_minutes: Optional[int] = None
    late_minutes: int = 0
    late_raw: Optional[str] = None
    early_minutes: int = 0
    early_raw: Optional[str] = None

    def has_data(self) -> bool:
        # Пустые дни (не было ни графика, ни присутствия) не сохраняем
        return bool(self.check_in or self.check_out or self.worked_minutes
                    or self.schedule_minutes)


@dataclass
class EmployeeBlock:
    full_name: str
    department: Optional[str] = None
    position: Optional[str] = None
    tab_number: Optional[str] = None
    days: List[DayEntry] = field(default_factory=list)


@dataclass
class ParsedTimesheet:
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    employees: List[EmployeeBlock] = field(default_factory=list)


# --- НИЗКОУРОВНЕВЫЕ ХЕЛПЕРЫ ---

def _cell_str(value) -> str:
    # Приводит значение ячейки к строке (xlrd/openpyxl могут отдавать числа/даты)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else str(value)
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value).strip()


def _parse_date(text: str) -> Optional[date]:
    m = DATE_RE.match(text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _parse_clock(text: str) -> Optional[str]:
    # Валидирует время вида "7:23", возвращает нормализованную строку
    m = TIME_RE.match(text.strip())
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if h > 23 or mn > 59:
        return None
    return f"{h}:{mn:02d}"


def _parse_duration_min(text: str) -> Optional[int]:
    # Длительность "8:17" -> 497 минут (часы могут быть > 24 в сводках)
    text = text.strip()
    m = re.match(r'^(\d{1,3}):(\d{2})$', text)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _parse_violation(text: str) -> Tuple[int, Optional[str]]:
    # Нарушение вида "01:20 (15:40)" -> (80, исходная строка)
    text = text.strip()
    if not text:
        return 0, None
    m = VIOLATION_RE.match(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)), text
    return 0, text


def _load_rows(file_path: str) -> List[List[str]]:
    # Читает .xls (xlrd) или .xlsx (openpyxl) в матрицу строк
    with open(file_path, 'rb') as f:
        head = f.read(8)

    if head.startswith(b'\xd0\xcf\x11\xe0'):  # OLE2 -> старый .xls
        import xlrd
        try:
            wb = xlrd.open_workbook(file_path, encoding_override='cp1251')
        except Exception:
            wb = xlrd.open_workbook(file_path)
        sh = wb.sheet_by_index(0)
        return [[_cell_str(sh.cell_value(r, c)) for c in range(sh.ncols)]
                for r in range(sh.nrows)]

    if head.startswith(b'PK'):  # zip -> .xlsx
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        sh = wb.worksheets[0]
        rows = [[_cell_str(v) for v in row] for row in sh.iter_rows(values_only=True)]
        wb.close()
        return rows

    raise TimesheetParseError("Неизвестный формат файла. Ожидается Excel (.xls или .xlsx).")


# --- ОСНОВНОЙ ПАРСЕР ---

def _find_period(rows: List[List[str]]) -> Tuple[Optional[date], Optional[date]]:
    start, end = None, None
    for row in rows[:20]:
        for i, cell in enumerate(row):
            low = cell.lower()
            if 'начало периода' in low:
                for nxt in row[i + 1:]:
                    if nxt:
                        start = _parse_date(nxt)
                        break
            elif 'конец периода' in low:
                for nxt in row[i + 1:]:
                    if nxt:
                        end = _parse_date(nxt)
                        break
    return start, end


def _find_global_header(rows: List[List[str]]) -> dict:
    # Ищет строку "ФИО | Отдел | Должность | Таб. №" и возвращает индексы колонок
    for row in rows[:40]:
        lowered = [c.lower() for c in row]
        if any(c == 'фио' for c in lowered):
            cols = {}
            for i, c in enumerate(lowered):
                if c == 'фио':
                    cols['name'] = i
                elif 'отдел' in c:
                    cols['department'] = i
                elif 'должность' in c:
                    cols['position'] = i
                elif 'таб' in c and '№' in c:
                    cols['tab_number'] = i
            if 'name' in cols:
                return cols
    return {'name': 0, 'department': 2, 'position': 3, 'tab_number': 4}


def _map_block_columns(row: List[str]) -> Optional[dict]:
    # По подзаголовку блока определяет, в каких колонках лежат данные
    cols = {}
    for i, cell in enumerate(row):
        low = cell.lower()
        if low == 'дата':
            cols['date'] = i
        elif 'начала фактическ' in low:
            cols['check_in'] = i
        elif 'окончания фактическ' in low:
            cols['check_out'] = i
        elif low == 'фактическая наработка':
            cols['worked'] = i
        elif low == 'график':
            cols['schedule'] = i
        elif 'наработка по графику' in low:
            cols['schedule_worked'] = i
        elif 'поздних приход' in low:
            cols['late'] = i
        elif 'ранних уход' in low:
            cols['early'] = i
    return cols if 'date' in cols else None


def _get(row: List[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def parse_timesheet(file_path: str) -> ParsedTimesheet:
    """Разбирает файл табеля. Бросает TimesheetParseError, если структура не распознана."""
    rows = _load_rows(file_path)
    if not rows:
        raise TimesheetParseError("Файл пуст.")

    result = ParsedTimesheet()
    result.period_start, result.period_end = _find_period(rows)
    header_cols = _find_global_header(rows)
    name_col = header_cols.get('name', 0)

    r = 0
    while r < len(rows):
        row = rows[r]
        block_cols = _map_block_columns(row)
        full_name = _get(row, name_col)

        # Начало блока: подзаголовок с "Дата" и непустое ФИО в этой же строке
        if block_cols and full_name and full_name.lower() != 'фио':
            emp = EmployeeBlock(
                full_name=full_name,
                department=_get(row, header_cols.get('department')) or None,
                position=_get(row, header_cols.get('position')) or None,
                tab_number=_get(row, header_cols.get('tab_number')) or None,
            )
            # Мусорные значения вида "(нет)" не сохраняем
            if emp.position and emp.position.lower() in ('(нет)', 'нет', '-'):
                emp.position = None

            r += 1
            while r < len(rows):
                day_row = rows[r]
                day_date = _parse_date(_get(day_row, block_cols['date']))
                if not day_date:
                    break

                late_min, late_raw = _parse_violation(_get(day_row, block_cols.get('late')))
                early_min, early_raw = _parse_violation(_get(day_row, block_cols.get('early')))
                entry = DayEntry(
                    work_date=day_date,
                    check_in=_parse_clock(_get(day_row, block_cols.get('check_in'))),
                    check_out=_parse_clock(_get(day_row, block_cols.get('check_out'))),
                    worked_minutes=_parse_duration_min(_get(day_row, block_cols.get('worked'))) or 0,
                    schedule_minutes=_parse_duration_min(_get(day_row, block_cols.get('schedule'))),
                    schedule_worked_minutes=_parse_duration_min(_get(day_row, block_cols.get('schedule_worked'))),
                    late_minutes=late_min,
                    late_raw=late_raw,
                    early_minutes=early_min,
                    early_raw=early_raw,
                )
                if entry.has_data():
                    emp.days.append(entry)
                r += 1

            result.employees.append(emp)
        else:
            r += 1

    if not result.employees:
        raise TimesheetParseError(
            "Не найдено ни одного сотрудника. Убедитесь, что загружен "
            "«Табель трудовой дисциплины (по сотрудникам)»."
        )

    logger.info(f"Табель разобран: {len(result.employees)} сотрудников, "
                f"период {result.period_start} — {result.period_end}")
    return result
