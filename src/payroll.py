# Движок учёта времени: план дня по графику/отсутствиям, сопоставление с фактом
# из табеля и расчёт зарплаты со штрафами.
#
# Приоритет источников для дня:
#   1. Отсутствие (отпуск, больничный, отгул) — день уважительный, нормы нет.
#   2. Собственный график сотрудника — рабочий день или выходной, норма и время смены.
#   3. Без своего графика — как раньше: норма из настроек сотрудника или из колонки
#      "График" табеля, нарушения — из табеля.

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set

from src.database import Employee, WorkSession, WorkSchedule, EmployeeAbsence

ABSENCE_LABELS = {'vacation': 'Отпуск', 'sick': 'Больничный', 'dayoff': 'Отгул'}
WEEKDAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']


# --- ВРЕМЯ ---

def parse_hhmm(value: Optional[str]) -> Optional[int]:
    """'9:05' -> 545 минут от полуночи."""
    if not value:
        return None
    try:
        h, m = value.strip().split(':')
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def fmt_hhmm(minutes: Optional[int]) -> str:
    if minutes is None:
        return ''
    return f"{minutes // 60}:{minutes % 60:02d}"


def fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} ч" if not m else f"{h} ч {m} мин"


# --- ГРАФИК ---

def parse_weekdays(value: Optional[str]) -> Set[int]:
    if not value:
        return set()
    return {int(x) for x in value.split(',') if x.strip().isdigit() and 0 <= int(x) <= 6}


def schedule_norm_minutes(s: WorkSchedule) -> Optional[int]:
    """Длительность смены: явная, иначе конец − начало − перерыв."""
    if s.shift_minutes:
        return s.shift_minutes
    start, end = parse_hhmm(s.shift_start), parse_hhmm(s.shift_end)
    if start is None or end is None:
        return None
    duration = end - start
    if duration <= 0:
        duration += 24 * 60  # ночная смена через полночь
    duration -= s.break_minutes or 0
    return duration if duration > 0 else None


def is_working_day(s: WorkSchedule, d: date) -> bool:
    if s.kind == 'cycle':
        work, off = s.cycle_work or 0, s.cycle_off or 0
        if work <= 0:
            return False
        anchor = s.cycle_start or s.date_from
        return (d - anchor).days % (work + off) < work
    return d.weekday() in parse_weekdays(s.weekdays)


def pick_schedule(schedules: Sequence[WorkSchedule], d: date) -> Optional[WorkSchedule]:
    # При пересечении периодов действует график, начавшийся позже
    best = None
    for s in schedules:
        if s.date_from <= d and (s.date_to is None or d <= s.date_to):
            if best is None or (s.date_from, s.id or 0) > (best.date_from, best.id or 0):
                best = s
    return best


def pick_absence(absences: Sequence[EmployeeAbsence], d: date) -> Optional[EmployeeAbsence]:
    for a in absences:
        if a.date_from <= d <= a.date_to:
            return a
    return None


def _describe_weekdays(days: Set[int]) -> str:
    if len(days) == 7:
        return 'ежедневно'
    ordered = sorted(days)
    runs, run = [], [ordered[0]] if ordered else []
    for x in ordered[1:]:
        if x == run[-1] + 1:
            run.append(x)
        else:
            runs.append(run)
            run = [x]
    if run:
        runs.append(run)
    parts = []
    for r in runs:
        if len(r) >= 3:
            parts.append(f"{WEEKDAY_SHORT[r[0]]}–{WEEKDAY_SHORT[r[-1]]}")
        else:
            parts.extend(WEEKDAY_SHORT[x] for x in r)
    return ', '.join(parts)


def describe_schedule(s: WorkSchedule) -> str:
    """Короткое описание для списков: 'Пн–Пт · 9:00–18:00 · 8 ч'."""
    if s.kind == 'cycle':
        days = f"{s.cycle_work or 0}/{s.cycle_off or 0}"
    else:
        days = _describe_weekdays(parse_weekdays(s.weekdays)) or 'дни не выбраны'
    parts = [days]
    if s.shift_start and s.shift_end:
        parts.append(f"{fmt_hhmm(parse_hhmm(s.shift_start))}–{fmt_hhmm(parse_hhmm(s.shift_end))}")
    norm = schedule_norm_minutes(s)
    if norm:
        parts.append(fmt_duration(norm))
    return ' · '.join(parts)


# --- РАСЧЁТ ДНЕЙ ---

@dataclass
class DayResult:
    work_date: date
    status: str                      # work | extra | absent | off | planned | vacation | sick | dayoff | none
    source: Optional[str] = None     # 'schedule' — свой график, 'timesheet' — из табеля
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    norm_minutes: Optional[int] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    worked_minutes: int = 0
    shortfall_minutes: int = 0
    late_minutes: int = 0
    early_minutes: int = 0
    late_counted: bool = False       # опоздание сверх допуска — идёт в штраф
    planned_work: Optional[bool] = None  # по своему графику: True — рабочий, False — выходной
    punch_issue: Optional[str] = None    # no_exit | no_entry | no_marks — см. detect_punch_issue
    absence_paid: bool = False
    absence_note: Optional[str] = None


def detect_punch_issue(session: Optional[WorkSession]) -> Optional[str]:
    """Неполные отметки. Если сотрудник не отметил уход, система учёта продлевает
    присутствие до полуночи (уход 0:00), а следующие дни идут как 0:00–0:00 (24 ч),
    пока не появится отметка. Такая наработка не отражает реальное время."""
    if not session or not (session.worked_minutes or 0):
        return None
    ci, co = session.check_in, session.check_out
    if ci == '0:00' and co == '0:00':
        return 'no_marks'
    if co == '0:00':
        return 'no_exit'
    if ci == '0:00':
        return 'no_entry'
    return None


def _late_early_by_schedule(s: WorkSchedule, session: WorkSession):
    start, end = parse_hhmm(s.shift_start), parse_hhmm(s.shift_end)
    ci, co = parse_hhmm(session.check_in), parse_hhmm(session.check_out)
    late = max(0, ci - start) if start is not None and ci is not None else 0
    early = 0
    if end is not None and co is not None:
        if start is not None:
            if end <= start:
                end += 24 * 60
            if co < start:
                co += 24 * 60
        early = max(0, end - co)
    return late, early


def compute_days(
    employee: Employee,
    sessions: Dict[date, WorkSession],
    schedules: Sequence[WorkSchedule],
    absences: Sequence[EmployeeAbsence],
    start: date,
    end: date,
    is_covered: Callable[[date], bool],
) -> List[DayResult]:
    """План и факт по каждому дню периода.

    is_covered(d) — есть ли за этот день данные табеля. Без них рабочий день
    по графику не считается прогулом (табель ещё не загружен или день в будущем).
    """
    late_tolerance = employee.late_tolerance_min or 0
    under_tolerance = employee.underwork_tolerance_min or 0
    days: List[DayResult] = []

    d = start
    while d <= end:
        session = sessions.get(d)
        worked = (session.worked_minutes or 0) if session else 0
        day = DayResult(work_date=d, status='none', worked_minutes=worked)
        if session:
            day.check_in, day.check_out = session.check_in, session.check_out
            day.punch_issue = detect_punch_issue(session)

        absence = pick_absence(absences, d)
        schedule = pick_schedule(schedules, d)

        if schedule:
            day.source = 'schedule'
            day.planned_work = is_working_day(schedule, d)

        if absence:
            day.status = 'extra' if worked > 0 else absence.kind
            day.absence_paid = bool(absence.paid)
            day.absence_note = absence.note
            if day.planned_work:
                day.norm_minutes = schedule_norm_minutes(schedule) or employee.norm_minutes
        elif schedule:
            if day.planned_work:
                day.planned_start = schedule.shift_start
                day.planned_end = schedule.shift_end
                day.norm_minutes = schedule_norm_minutes(schedule) or employee.norm_minutes
                if worked > 0:
                    day.status = 'work'
                    if schedule.shift_start or schedule.shift_end:
                        day.late_minutes, day.early_minutes = _late_early_by_schedule(schedule, session)
                    else:
                        day.late_minutes = session.late_minutes or 0
                        day.early_minutes = session.early_minutes or 0
                else:
                    day.status = 'absent' if is_covered(d) else 'planned'
            else:
                day.status = 'extra' if worked > 0 else 'off'
        elif session:
            day.source = 'timesheet'
            day.norm_minutes = employee.norm_minutes or session.schedule_minutes
            if worked > 0:
                day.status = 'work'
                day.late_minutes = session.late_minutes or 0
                day.early_minutes = session.early_minutes or 0
            elif day.norm_minutes:
                day.status = 'absent'

        if day.status == 'work' and day.norm_minutes:
            shortfall = day.norm_minutes - worked
            if shortfall > under_tolerance:
                day.shortfall_minutes = shortfall
        day.late_counted = day.status == 'work' and day.late_minutes > late_tolerance

        days.append(day)
        d += timedelta(days=1)
    return days


# --- ЗАРПЛАТА ---

@dataclass
class PayrollTotals:
    days_worked: int = 0
    extra_days: int = 0
    absences: int = 0
    vacation_days: int = 0
    sick_days: int = 0
    dayoff_days: int = 0
    worked_minutes: int = 0
    norm_total_minutes: int = 0
    shortfall_minutes: int = 0
    late_count: int = 0
    early_count: int = 0
    punch_issues: int = 0            # Дни с неполными отметками — требуют проверки
    base_pay: float = 0.0
    absence_pay: float = 0.0
    underwork_penalty: float = 0.0
    late_penalty_total: float = 0.0
    total_pay: float = 0.0


def calc_totals(employee: Employee, days: Sequence[DayResult]) -> PayrollTotals:
    t = PayrollTotals()
    rate = employee.pay_rate or 0.0
    hourly = employee.pay_type == 'hour'

    for day in days:
        if day.status in ('work', 'extra'):
            t.days_worked += 1
            t.worked_minutes += day.worked_minutes
            if day.status == 'extra':
                t.extra_days += 1
            if day.status == 'work' and day.norm_minutes:
                t.norm_total_minutes += day.norm_minutes
            t.shortfall_minutes += day.shortfall_minutes
            if day.late_counted:
                t.late_count += 1
            if day.early_minutes > 0:
                t.early_count += 1
            if day.punch_issue:
                t.punch_issues += 1
        elif day.status == 'absent':
            t.absences += 1
        elif day.status in ABSENCE_LABELS:
            setattr(t, f"{day.status}_days", getattr(t, f"{day.status}_days") + 1)
            # Оплачиваемое отсутствие не оплачивается в дни, выходные по графику
            if day.absence_paid and day.planned_work is not False:
                if hourly:
                    norm = day.norm_minutes or employee.norm_minutes or 0
                    t.absence_pay += norm / 60 * rate
                else:
                    t.absence_pay += rate

    if hourly:
        t.base_pay = t.worked_minutes / 60 * rate
    else:
        t.base_pay = t.days_worked * rate

    t.base_pay = round(t.base_pay, 2)
    t.absence_pay = round(t.absence_pay, 2)
    t.underwork_penalty = round(t.shortfall_minutes / 60 * (employee.underwork_penalty_per_hour or 0), 2)
    t.late_penalty_total = round(t.late_count * (employee.late_penalty or 0), 2)
    t.total_pay = round(t.base_pay + t.absence_pay - t.underwork_penalty - t.late_penalty_total, 2)
    return t
