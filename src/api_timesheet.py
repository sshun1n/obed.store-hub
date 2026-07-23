# Модуль "Учёт рабочего времени": загрузка табеля, сотрудники, расчёт зарплаты.
# Доступ ко всем эндпоинтам — только для администраторов (данные о з/п).

import os
import logging
import tempfile
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from src import auth, schemas
from src.database import get_db, Employee, WorkSession, TimesheetImport
from src.timesheet_parser import parse_timesheet, TimesheetParseError

logger = logging.getLogger("Timesheet")

router = APIRouter(prefix="/timesheet", tags=["Учёт рабочего времени"])

ALLOWED_EXTENSIONS = ('.xls', '.xlsx')
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 МБ — табель весит сотни КБ


# --- РАСЧЁТ ЗАРПЛАТЫ ---

def _day_norm(employee: Employee, session: WorkSession) -> Optional[int]:
    # Норма на день: персональная настройка приоритетнее графика из табеля
    if employee.norm_minutes:
        return employee.norm_minutes
    return session.schedule_minutes


def calc_payroll_row(employee: Employee, sessions: List[WorkSession]) -> schemas.PayrollRow:
    """Считает итог по сотруднику за период по его настройкам оплаты."""
    days_worked = 0
    absences = 0
    worked_minutes = 0
    norm_total = 0
    shortfall_total = 0
    late_count = 0
    early_count = 0
    tolerance = employee.underwork_tolerance_min or 0

    for s in sessions:
        norm = _day_norm(employee, s)
        if s.worked_minutes and s.worked_minutes > 0:
            days_worked += 1
            worked_minutes += s.worked_minutes
            if norm:
                norm_total += norm
                shortfall = norm - s.worked_minutes
                if shortfall > tolerance:
                    shortfall_total += shortfall
            if s.late_minutes and s.late_minutes > 0:
                late_count += 1
            if s.early_minutes and s.early_minutes > 0:
                early_count += 1
        elif norm:
            # По графику должен был работать, но присутствий нет — прогул
            absences += 1

    rate = employee.pay_rate or 0.0
    if employee.pay_type == 'hour':
        base_pay = round(worked_minutes / 60 * rate, 2)
    else:  # 'shift'
        base_pay = round(days_worked * rate, 2)

    underwork_penalty = round(shortfall_total / 60 * (employee.underwork_penalty_per_hour or 0), 2)
    late_penalty_total = round(late_count * (employee.late_penalty or 0), 2)
    total_pay = round(base_pay - underwork_penalty - late_penalty_total, 2)

    return schemas.PayrollRow(
        employee=schemas.Employee.model_validate(employee),
        days_worked=days_worked,
        absences=absences,
        worked_minutes=worked_minutes,
        norm_total_minutes=norm_total,
        shortfall_minutes=shortfall_total,
        late_count=late_count,
        early_count=early_count,
        base_pay=base_pay,
        underwork_penalty=underwork_penalty,
        late_penalty_total=late_penalty_total,
        total_pay=total_pay,
    )


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

    # Записываем в БД: сотрудники создаются автоматически, смены обновляются (upsert)
    employees_by_name = {e.full_name: e for e in db.query(Employee).all()}
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
            # Обновляем справочные поля, если в табеле появились данные
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


# --- СОТРУДНИКИ ---

@router.get("/employees", response_model=List[schemas.Employee])
def list_employees(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    q = db.query(Employee)
    if not include_archived:
        q = q.filter(Employee.is_active == True)
    return q.order_by(Employee.full_name).all()


@router.put("/employees/{employee_id}", response_model=schemas.Employee)
def update_employee(
    employee_id: int,
    data: schemas.EmployeeUpdate,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    update = data.model_dump(exclude_unset=True)
    if 'pay_type' in update and update['pay_type'] not in ('shift', 'hour'):
        raise HTTPException(status_code=400, detail="pay_type должен быть 'shift' или 'hour'")
    for key, value in update.items():
        setattr(emp, key, value)
    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/employees/{employee_id}")
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Полное удаление сотрудника вместе с историей смен."""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    db.delete(emp)
    db.commit()
    return {"ok": True}


@router.get("/employees/{employee_id}/sessions", response_model=List[schemas.WorkSessionOut])
def employee_sessions(
    employee_id: int,
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Детализация по дням для одного сотрудника."""
    return (db.query(WorkSession)
            .filter(WorkSession.employee_id == employee_id,
                    WorkSession.work_date >= start,
                    WorkSession.work_date <= end)
            .order_by(WorkSession.work_date).all())


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
    if end < start:
        raise HTTPException(status_code=400, detail="Конец периода раньше начала")
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Период не может превышать один год")

    q = db.query(Employee)
    if not include_archived:
        q = q.filter(Employee.is_active == True)
    employees = q.order_by(Employee.full_name).all()

    sessions = (db.query(WorkSession)
                .filter(WorkSession.work_date >= start, WorkSession.work_date <= end)
                .all())
    by_employee = {}
    for s in sessions:
        by_employee.setdefault(s.employee_id, []).append(s)

    rows = [calc_payroll_row(emp, by_employee.get(emp.id, [])) for emp in employees]
    # Сотрудники без смен за период — в конец списка
    rows.sort(key=lambda r: (r.days_worked == 0 and r.absences == 0, r.employee.full_name.lower()))

    return schemas.PayrollReport(period_start=start, period_end=end, rows=rows)
