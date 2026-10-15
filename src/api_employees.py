# Справочник сотрудников: карточка, альтернативные ФИО (алиасы), объединение дублей,
# графики работы и отсутствия (отпуск, больничный, отгул).
# Доступ — только для администраторов.

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from src import auth, schemas
from src.api_timesheet import compute_days_for, employee_label, validate_period
from src.database import (
    get_db, Employee, EmployeeAlias, WorkSession, WorkSchedule, EmployeeAbsence,
)
from src.payroll import (
    ABSENCE_LABELS, describe_schedule, parse_hhmm, parse_weekdays,
    pick_absence, pick_schedule, schedule_norm_minutes,
)

router = APIRouter(prefix="/timesheet", tags=["Сотрудники"])

TEXT_FIELDS = ('display_name', 'position', 'department', 'tab_number', 'phone', 'notes')


# --- ХЕЛПЕРЫ ---

def get_employee_or_404(db: Session, employee_id: int) -> Employee:
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    return emp


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _ensure_name_free(db: Session, name: str, allow_employee_id: Optional[int] = None) -> None:
    """ФИО не должно совпадать с другим сотрудником или чужим алиасом."""
    emp = db.query(Employee).filter(Employee.full_name == name).first()
    if emp and emp.id != allow_employee_id:
        raise HTTPException(
            status_code=400,
            detail=f"Уже есть сотрудник «{employee_label(emp)}» с таким ФИО. "
                   f"Если это один человек — используйте объединение.",
        )
    alias = db.query(EmployeeAlias).filter(EmployeeAlias.name == name).first()
    if alias:
        raise HTTPException(
            status_code=400,
            detail=f"Это ФИО уже привязано к сотруднику «{employee_label(alias.employee)}».",
        )


def _fmt_date(d: date) -> str:
    return d.strftime('%d.%m.%Y')


def schedule_out(s: WorkSchedule) -> schemas.ScheduleOut:
    return schemas.ScheduleOut(
        id=s.id,
        date_from=s.date_from,
        date_to=s.date_to,
        kind=s.kind,
        weekdays=sorted(parse_weekdays(s.weekdays)),
        cycle_work=s.cycle_work,
        cycle_off=s.cycle_off,
        cycle_start=s.cycle_start,
        shift_start=s.shift_start,
        shift_end=s.shift_end,
        break_minutes=s.break_minutes or 0,
        shift_minutes=s.shift_minutes,
        norm_minutes=schedule_norm_minutes(s),
        summary=describe_schedule(s),
        note=s.note,
    )


def _normalize_time(value: Optional[str], label: str) -> Optional[str]:
    value = _clean(value)
    if value is None:
        return None
    minutes = parse_hhmm(value)
    if minutes is None:
        raise HTTPException(status_code=400, detail=f"{label}: укажите время в формате ЧЧ:ММ")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _apply_schedule(s: WorkSchedule, data: schemas.ScheduleIn) -> None:
    if data.date_to and data.date_to < data.date_from:
        raise HTTPException(status_code=400, detail="Дата окончания графика раньше даты начала")
    if data.kind not in ('weekly', 'cycle'):
        raise HTTPException(status_code=400, detail="Тип графика: 'weekly' или 'cycle'")

    s.date_from, s.date_to, s.kind = data.date_from, data.date_to, data.kind
    if data.kind == 'weekly':
        days = sorted({d for d in data.weekdays if 0 <= d <= 6})
        if not days:
            raise HTTPException(status_code=400, detail="Выберите хотя бы один рабочий день недели")
        s.weekdays = ','.join(map(str, days))
        s.cycle_work = s.cycle_off = s.cycle_start = None
    else:
        if not data.cycle_work or data.cycle_off is None:
            raise HTTPException(status_code=400, detail="Для сменного графика укажите рабочие и выходные дни")
        s.cycle_work, s.cycle_off = data.cycle_work, data.cycle_off
        s.cycle_start = data.cycle_start or data.date_from
        s.weekdays = None

    s.shift_start = _normalize_time(data.shift_start, "Начало смены")
    s.shift_end = _normalize_time(data.shift_end, "Конец смены")
    s.break_minutes = data.break_minutes or 0
    s.shift_minutes = data.shift_minutes
    s.note = _clean(data.note)


def _apply_absence(a: EmployeeAbsence, data: schemas.AbsenceIn) -> None:
    if data.kind not in ABSENCE_LABELS:
        raise HTTPException(status_code=400, detail="Тип отсутствия: отпуск, больничный или отгул")
    if data.date_to < data.date_from:
        raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")
    a.kind, a.date_from, a.date_to = data.kind, data.date_from, data.date_to
    a.paid = data.paid
    a.note = _clean(data.note)


# --- СПРАВОЧНИК ---

@router.get("/employees", response_model=List[schemas.EmployeeListItem])
def list_employees(
    status: str = Query('active', pattern='^(active|archived|all)$'),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    if include_archived:
        status = 'all'
    q = db.query(Employee)
    if status == 'active':
        q = q.filter(Employee.is_active == True)
    elif status == 'archived':
        q = q.filter(Employee.is_active == False)
    employees = q.all()

    last_work = dict(
        db.query(WorkSession.employee_id, func.max(WorkSession.work_date))
        .filter(WorkSession.worked_minutes > 0)
        .group_by(WorkSession.employee_id).all()
    )
    today = date.today()
    items = []
    for e in employees:
        schedule = pick_schedule(e.schedules, today)
        absence = pick_absence(e.absences, today)
        items.append(schemas.EmployeeListItem(
            **schemas.Employee.model_validate(e).model_dump(),
            aliases=sorted(a.name for a in e.aliases),
            last_work_date=last_work.get(e.id),
            schedule_summary=describe_schedule(schedule) if schedule else None,
            absence_now=(f"{ABSENCE_LABELS[absence.kind]} до {_fmt_date(absence.date_to)}"
                         if absence else None),
        ))
    items.sort(key=lambda x: employee_label(x).lower())
    return items


@router.post("/employees", response_model=schemas.Employee)
def create_employee(
    data: schemas.EmployeeCreate,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Ручное добавление (например, сотрудник ещё не появлялся в табеле)."""
    full_name = _clean(data.full_name)
    if not full_name:
        raise HTTPException(status_code=400, detail="Укажите ФИО")
    _ensure_name_free(db, full_name)
    emp = Employee(
        full_name=full_name,
        display_name=_clean(data.display_name),
        department=_clean(data.department),
        position=_clean(data.position),
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/employees/{employee_id}", response_model=schemas.EmployeeCard)
def employee_card(
    employee_id: int,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    emp = get_employee_or_404(db, employee_id)
    last_work_date, sessions_count = (
        db.query(func.max(WorkSession.work_date), func.count(WorkSession.id))
        .filter(WorkSession.employee_id == emp.id, WorkSession.worked_minutes > 0)
        .one()
    )
    return schemas.EmployeeCard(
        employee=schemas.Employee.model_validate(emp),
        aliases=sorted(emp.aliases, key=lambda a: a.name),
        schedules=[schedule_out(s) for s in sorted(emp.schedules, key=lambda s: s.date_from, reverse=True)],
        absences=sorted(emp.absences, key=lambda a: a.date_from, reverse=True),
        last_work_date=last_work_date,
        sessions_count=sessions_count or 0,
    )


@router.put("/employees/{employee_id}", response_model=schemas.Employee)
def update_employee(
    employee_id: int,
    data: schemas.EmployeeUpdate,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    emp = get_employee_or_404(db, employee_id)
    update = data.model_dump(exclude_unset=True)
    if 'pay_type' in update and update['pay_type'] not in ('shift', 'hour'):
        raise HTTPException(status_code=400, detail="pay_type должен быть 'shift' или 'hour'")
    for key, value in update.items():
        if key in TEXT_FIELDS:
            value = _clean(value)
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
    """Полное удаление сотрудника вместе с историей смен, графиками и отсутствиями."""
    emp = get_employee_or_404(db, employee_id)
    db.delete(emp)
    db.commit()
    return {"ok": True}


# --- АЛИАСЫ И ОБЪЕДИНЕНИЕ ---

@router.post("/employees/{employee_id}/aliases", response_model=schemas.EmployeeAliasOut)
def add_alias(
    employee_id: int,
    data: schemas.AliasCreate,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Привязать ещё одно написание ФИО: строки табеля с ним попадут к этому сотруднику."""
    emp = get_employee_or_404(db, employee_id)
    name = _clean(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="Укажите ФИО, как оно записано в табеле")
    _ensure_name_free(db, name)
    alias = EmployeeAlias(employee_id=emp.id, name=name)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


@router.delete("/employees/{employee_id}/aliases/{alias_id}")
def delete_alias(
    employee_id: int,
    alias_id: int,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    alias = (db.query(EmployeeAlias)
             .filter(EmployeeAlias.id == alias_id, EmployeeAlias.employee_id == employee_id).first())
    if not alias:
        raise HTTPException(status_code=404, detail="Алиас не найден")
    db.delete(alias)
    db.commit()
    return {"ok": True}


@router.post("/employees/{employee_id}/merge", response_model=schemas.Employee)
def merge_employees(
    employee_id: int,
    data: schemas.EmployeeMerge,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Вливает дубль (source) в текущего сотрудника: смены, графики, отсутствия
    и ФИО дубля переходят сюда, дубль удаляется. При совпадении дат остаётся
    смена с большей наработкой."""
    target = get_employee_or_404(db, employee_id)
    source = get_employee_or_404(db, data.source_id)
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Нельзя объединить сотрудника с самим собой")

    # Убранная из коллекции смена удаляется как "сирота" (delete-orphan)
    target_by_date = {s.work_date: s for s in target.sessions}
    to_move = []
    for s in list(source.sessions):
        t = target_by_date.get(s.work_date)
        if t is None:
            to_move.append(s)
        elif (s.worked_minutes or 0) > (t.worked_minutes or 0):
            target.sessions.remove(t)
            to_move.append(s)
        else:
            source.sessions.remove(s)
    db.flush()  # сначала удаляем конфликтующие даты — иначе сработает уникальность (сотрудник, дата)

    for item in to_move + list(source.schedules) + list(source.absences) + list(source.aliases):
        item.employee = target

    # Пустые поля карточки дополняем данными дубля
    for field in ('department', 'position', 'tab_number', 'phone'):
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
    if not target.pay_rate and source.pay_rate:
        for field in ('pay_type', 'pay_rate', 'norm_minutes', 'underwork_penalty_per_hour',
                      'underwork_tolerance_min', 'late_penalty', 'late_tolerance_min'):
            setattr(target, field, getattr(source, field))
    db.flush()

    source_name = source.full_name
    db.delete(source)
    db.flush()
    db.add(EmployeeAlias(employee_id=target.id, name=source_name))
    db.commit()
    db.refresh(target)
    return target


# --- ГРАФИКИ ---

@router.post("/employees/{employee_id}/schedules", response_model=schemas.ScheduleOut)
def create_schedule(
    employee_id: int,
    data: schemas.ScheduleIn,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Новый график. Действующий бессрочный график, начатый раньше,
    закрывается днём перед началом нового."""
    emp = get_employee_or_404(db, employee_id)
    s = WorkSchedule(employee_id=emp.id)
    _apply_schedule(s, data)
    for old in emp.schedules:
        if old.date_to is None and old.date_from < s.date_from:
            old.date_to = s.date_from - timedelta(days=1)
    db.add(s)
    db.commit()
    db.refresh(s)
    return schedule_out(s)


@router.put("/schedules/{schedule_id}", response_model=schemas.ScheduleOut)
def update_schedule(
    schedule_id: int,
    data: schemas.ScheduleIn,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    s = db.query(WorkSchedule).filter(WorkSchedule.id == schedule_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="График не найден")
    _apply_schedule(s, data)
    db.commit()
    db.refresh(s)
    return schedule_out(s)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    s = db.query(WorkSchedule).filter(WorkSchedule.id == schedule_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="График не найден")
    db.delete(s)
    db.commit()
    return {"ok": True}


# --- ОТСУТСТВИЯ ---

@router.post("/employees/{employee_id}/absences", response_model=schemas.AbsenceOut)
def create_absence(
    employee_id: int,
    data: schemas.AbsenceIn,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    emp = get_employee_or_404(db, employee_id)
    a = EmployeeAbsence(employee_id=emp.id)
    _apply_absence(a, data)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.put("/absences/{absence_id}", response_model=schemas.AbsenceOut)
def update_absence(
    absence_id: int,
    data: schemas.AbsenceIn,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    a = db.query(EmployeeAbsence).filter(EmployeeAbsence.id == absence_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    _apply_absence(a, data)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/absences/{absence_id}")
def delete_absence(
    absence_id: int,
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    a = db.query(EmployeeAbsence).filter(EmployeeAbsence.id == absence_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(a)
    db.commit()
    return {"ok": True}


# --- ПЛАН/ФАКТ ПО ДНЯМ ---

@router.get("/employees/{employee_id}/days", response_model=List[schemas.PayrollDay])
def employee_days(
    employee_id: int,
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Каждый день периода: план по графику/отсутствиям и факт из табеля."""
    validate_period(start, end)
    emp = get_employee_or_404(db, employee_id)
    days = compute_days_for(db, [emp], start, end).get(emp.id, [])
    return [schemas.PayrollDay(**vars(d)) for d in days]


@router.get("/employees/{employee_id}/sessions", response_model=List[schemas.WorkSessionOut])
def employee_sessions(
    employee_id: int,
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    admin: schemas.User = Depends(auth.admin_only),
):
    """Сырые строки табеля за период."""
    return (db.query(WorkSession)
            .filter(WorkSession.employee_id == employee_id,
                    WorkSession.work_date >= start,
                    WorkSession.work_date <= end)
            .order_by(WorkSession.work_date).all())
