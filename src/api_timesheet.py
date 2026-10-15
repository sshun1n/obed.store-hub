# Модуль "Учёт рабочего времени": загрузка табеля и расчёт зарплаты.
# Справочник сотрудников, графики и отсутствия — в api_employees.py.
# Доступ ко всем эндпоинтам — только для администраторов (данные о з/п).

import os
import logging
import tempfile
from datetime import date
from typing import Callable, Dict, Iterable, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from src import auth, schemas
from src.database import (
    get_db, Employee, EmployeeAlias, WorkSession, TimesheetImport,
    WorkSchedule, EmployeeAbsence,
)
from src.payroll import compute_days, calc_totals, DayResult
from src.timesheet_parser import parse_timesheet, TimesheetParseError

logger = logging.getLogger("Timesheet")

router = APIRouter(prefix="/timesheet", tags=["Учёт рабочего времени"])

ALLOWED_EXTENSIONS = ('.xls', '.xlsx')
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 МБ — табель весит сотни КБ
MAX_PERIOD_DAYS = 366


# --- ОБЩИЕ ХЕЛПЕРЫ (используются и в api_employees) ---

def validate_period(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(status_code=400, detail="Конец периода раньше начала")
    if (end - start).days > MAX_PERIOD_DAYS:
        raise HTTPException(status_code=400, detail="Период не может превышать один год")


def coverage_checker(db: Session) -> Callable[[date], bool]:
    """Есть ли за день загруженные данные табеля. Будущие дни не покрыты никогда."""
    periods = [
        (p_start, p_end)
        for p_start, p_end in db.query(TimesheetImport.period_start, TimesheetImport.period_end).all()
        if p_start and p_end
    ]
    today = date.today()

    def is_covered(d: date) -> bool:
        return d <= today and any(p_start <= d <= p_end for p_start, p_end in periods)

    return is_covered


def compute_days_for(
    db: Session,
    employees: Iterable[Employee],
    start: date,
    end: date,
) -> Dict[int, List[DayResult]]:
    """План/факт по дням для набора сотрудников — тремя запросами на всех."""
    employees = list(employees)
    ids = [e.id for e in employees]
    if not ids:
        return {}

    sessions: Dict[int, Dict[date, WorkSession]] = {}
    for s in (db.query(WorkSession)
              .filter(WorkSession.employee_id.in_(ids),
                      WorkSession.work_date >= start, WorkSession.work_date <= end)):
        sessions.setdefault(s.employee_id, {})[s.work_date] = s

    schedules: Dict[int, List[WorkSchedule]] = {}
    for s in (db.query(WorkSchedule)
              .filter(WorkSchedule.employee_id.in_(ids),
                      WorkSchedule.date_from <= end)):
        if s.date_to is None or s.date_to >= start:
            schedules.setdefault(s.employee_id, []).append(s)

    absences: Dict[int, List[EmployeeAbsence]] = {}
    for a in (db.query(EmployeeAbsence)
              .filter(EmployeeAbsence.employee_id.in_(ids),
                      EmployeeAbsence.date_from <= end, EmployeeAbsence.date_to >= start)
              .order_by(EmployeeAbsence.date_from)):
        absences.setdefault(a.employee_id, []).append(a)

    is_covered = coverage_checker(db)
    return {
        e.id: compute_days(e, sessions.get(e.id, {}), schedules.get(e.id, []),
                           absences.get(e.id, []), start, end, is_covered)
        for e in employees
    }


def employee_label(e) -> str:
    return (e.display_name or e.full_name or '').strip()


# --- ЗАГРУЗКА ТАБЕЛЯ ---

@router.post("/upload")
async def upload_timesheet(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Принимает Excel-табель, сохраняет данные в БД и удаляет файл с диска."""
    filename = file.filename or "timesheet.xls"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы Excel (.xls, .xlsx)")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Файл слишком большой (лимит 20 МБ)")
    if not content:
        raise HTTPException(status_code=400, detail="Файл пуст")

    # Сохраняем во временный файл только на время разбора, затем удаляем
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            parsed = parse_timesheet(tmp_path)
        except TimesheetParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Ошибка разбора табеля: {e}", exc_info=True)
            raise HTTPException(status_code=400, detail="Не удалось прочитать файл. Проверьте формат табеля.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as e:
                logger.error(f"Не удалось удалить временный файл {tmp_path}: {e}")

    # Сотрудник ищется по ФИО из табеля или по одному из его альтернативных написаний
    employees_by_name = {e.full_name: e for e in db.query(Employee).all()}
    for alias in db.query(EmployeeAlias).all():
        employees_by_name.setdefault(alias.name, alias.employee)
    new_employees = 0
    sessions_saved = 0

    for block in parsed.employees:
        emp = employees_by_name.get(block.full_name)
        if not emp:
            emp = Employee(
                full_name=block.full_name,
                department=block.department,
                position=block.position,
                tab_number=block.tab_number,
            )
            db.add(emp)
            db.flush()  # получаем id
            employees_by_name[block.full_name] = emp
            new_employees += 1
        else:
            # Дополняем только пустые поля — ручные правки не перетираем
            if block.department and not emp.department:
                emp.department = block.department
            if block.position and not emp.position:
                emp.position = block.position
            if block.tab_number and not emp.tab_number:
                emp.tab_number = block.tab_number

        existing = {
            s.work_date: s
            for s in db.query(WorkSession).filter(
                WorkSession.employee_id == emp.id,
                WorkSession.work_date.in_([d.work_date for d in block.days])
            ).all()
        } if block.days else {}

        for day in block.days:
            s = existing.get(day.work_date)
            if not s:
                s = WorkSession(employee_id=emp.id, work_date=day.work_date)
                db.add(s)
                existing[day.work_date] = s
            s.check_in = day.check_in
            s.check_out = day.check_out
            s.worked_minutes = day.worked_minutes
            s.schedule_minutes = day.schedule_minutes
            s.schedule_worked_minutes = day.schedule_worked_minutes
            s.late_minutes = day.late_minutes
            s.late_raw = day.late_raw
            s.early_minutes = day.early_minutes
            s.early_raw = day.early_raw
            sessions_saved += 1

    imp = TimesheetImport(
        filename=filename,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        employees_count=len(parsed.employees),
        sessions_count=sessions_saved,
        uploaded_by=admin.login,
    )
    db.add(imp)
    db.commit()

    return {
        "status": "ok",
        "period_start": parsed.period_start.isoformat() if parsed.period_start else None,
        "period_end": parsed.period_end.isoformat() if parsed.period_end else None,
        "employees_total": len(parsed.employees),
        "employees_new": new_employees,
        "sessions_saved": sessions_saved,
    }


@router.get("/imports", response_model=List[schemas.TimesheetImportOut])
def list_imports(db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    """Журнал последних загрузок табеля."""
    return (db.query(TimesheetImport)
            .order_by(TimesheetImport.created_at.desc())
            .limit(20).all())


# --- ОТЧЁТ ПО ЗАРПЛАТЕ ---

@router.get("/report", response_model=schemas.PayrollReport)
def payroll_report(
    start: date = Query(...),
    end: date = Query(...),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Сводный расчёт по всем сотрудникам за период."""
    validate_period(start, end)

    q = db.query(Employee)
    if not include_archived:
        q = q.filter(Employee.is_active == True)
    employees = q.all()
    days_by_emp = compute_days_for(db, employees, start, end)

    rows = []
    for emp in employees:
        days = days_by_emp.get(emp.id, [])
        totals = calc_totals(emp, days)
        rows.append(schemas.PayrollRow(
            employee=schemas.Employee.model_validate(emp),
            has_schedule=any(d.source == 'schedule' for d in days),
            **vars(totals),
        ))

    # Сотрудники без единого события за период — в конец списка
    def is_empty(r: schemas.PayrollRow) -> bool:
        return not (r.days_worked or r.absences or r.vacation_days or r.sick_days or r.dayoff_days)

    rows.sort(key=lambda r: (is_empty(r), employee_label(r.employee).lower()))
    return schemas.PayrollReport(period_start=start, period_end=end, rows=rows)
