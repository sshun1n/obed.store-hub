from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime, date

# --- СХЕМЫ КАТЕГОРИЙ ---

class CategoryBase(BaseModel):
    name: str
    parent_id: Optional[int] = None

class CategoryCreate(CategoryBase):
    pass

class Category(CategoryBase):
    id: int
    children: List['Category'] = []

    class Config:
        from_attributes = True

# --- СХЕМЫ ПРОДУКТОВ ---

class ProductBase(BaseModel):
    name_from_sheet: str

class Product(ProductBase):
    id: int
    categories: List[Category] = []

    class Config:
        from_attributes = True

class ProductCategoriesUpdate(BaseModel):
    category_ids: List[int]

# --- СХЕМЫ ПОЛЬЗОВАТЕЛЕЙ ---

class UserBase(BaseModel):
    login: str
    full_name: Optional[str] = None
    role: Optional[str] = 'manager' # 'admin' или 'manager'
    phone: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    login: Optional[str] = None
    password: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None

class User(UserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

# --- СХЕМЫ АВТОРИЗАЦИИ ---

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    login: Optional[str] = None

# --- СХЕМЫ GOOGLE ТАБЛИЦ ---

class GoogleConfigBase(BaseModel):
    title: Optional[str] = None
    url: str
    spreadsheet_id: str
    sheet_name: str

class GoogleConfigCreate(GoogleConfigBase):
    pass

class GoogleConfig(GoogleConfigBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class SpreadsheetUrlRequest(BaseModel):
    url: str

# --- СХЕМЫ УЧЁТА РАБОЧЕГО ВРЕМЕНИ ---

class EmployeeBase(BaseModel):
    full_name: str
    display_name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    tab_number: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True
    pay_type: str = 'shift'          # 'shift' — за смену, 'hour' — за час
    pay_rate: float = 0.0
    norm_minutes: Optional[int] = None
    underwork_penalty_per_hour: float = 0.0
    underwork_tolerance_min: int = 0
    late_penalty: float = 0.0
    late_tolerance_min: int = 0

    @field_validator('pay_rate', 'underwork_penalty_per_hour', 'late_penalty',
                     'underwork_tolerance_min', 'late_tolerance_min', mode='before')
    @classmethod
    def _null_to_zero(cls, v):
        # Старые строки БД могут содержать NULL в числовых полях
        return 0 if v is None else v

    @field_validator('pay_type', mode='before')
    @classmethod
    def _null_pay_type(cls, v):
        return v or 'shift'

    @field_validator('is_active', mode='before')
    @classmethod
    def _null_active(cls, v):
        return True if v is None else v

class EmployeeCreate(BaseModel):
    full_name: str
    display_name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None

class EmployeeUpdate(BaseModel):
    display_name: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    tab_number: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    pay_type: Optional[str] = None
    pay_rate: Optional[float] = Field(None, ge=0)
    norm_minutes: Optional[int] = Field(None, ge=1, le=24 * 60)
    underwork_penalty_per_hour: Optional[float] = Field(None, ge=0)
    underwork_tolerance_min: Optional[int] = Field(None, ge=0)
    late_penalty: Optional[float] = Field(None, ge=0)
    late_tolerance_min: Optional[int] = Field(None, ge=0)

class Employee(EmployeeBase):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class EmployeeAliasOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class AliasCreate(BaseModel):
    name: str

class EmployeeMerge(BaseModel):
    source_id: int                   # Этот сотрудник будет влит в текущего и удалён

class ScheduleIn(BaseModel):
    date_from: date
    date_to: Optional[date] = None
    kind: str = 'weekly'             # 'weekly' | 'cycle'
    weekdays: List[int] = []         # 0 — понедельник
    cycle_work: Optional[int] = Field(None, ge=1, le=30)
    cycle_off: Optional[int] = Field(None, ge=0, le=30)
    cycle_start: Optional[date] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    break_minutes: int = Field(0, ge=0, le=600)
    shift_minutes: Optional[int] = Field(None, ge=1, le=24 * 60)
    note: Optional[str] = None

class ScheduleOut(BaseModel):
    id: int
    date_from: date
    date_to: Optional[date] = None
    kind: str
    weekdays: List[int] = []
    cycle_work: Optional[int] = None
    cycle_off: Optional[int] = None
    cycle_start: Optional[date] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    break_minutes: int = 0
    shift_minutes: Optional[int] = None
    norm_minutes: Optional[int] = None   # Вычисленная длительность смены
    summary: str = ''
    note: Optional[str] = None

class AbsenceIn(BaseModel):
    kind: str                        # 'vacation' | 'sick' | 'dayoff'
    date_from: date
    date_to: date
    paid: bool = False
    note: Optional[str] = None

class AbsenceOut(AbsenceIn):
    id: int

    class Config:
        from_attributes = True

class EmployeeListItem(Employee):
    # Строка справочника сотрудников
    aliases: List[str] = []
    last_work_date: Optional[date] = None
    schedule_summary: Optional[str] = None   # График, действующий сегодня
    absence_now: Optional[str] = None        # "Отпуск до 20.10.2026"

class EmployeeCard(BaseModel):
    employee: Employee
    aliases: List[EmployeeAliasOut] = []
    schedules: List[ScheduleOut] = []
    absences: List[AbsenceOut] = []
    last_work_date: Optional[date] = None
    sessions_count: int = 0

class WorkSessionOut(BaseModel):
    id: int
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

    class Config:
        from_attributes = True

class TimesheetImportOut(BaseModel):
    id: int
    filename: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    employees_count: int
    sessions_count: int
    uploaded_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class PayrollDay(BaseModel):
    # Один день: план (график/отсутствие) и факт (табель)
    work_date: date
    status: str                      # work | extra | absent | off | planned | vacation | sick | dayoff | none
    source: Optional[str] = None     # 'schedule' | 'timesheet'
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    norm_minutes: Optional[int] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    worked_minutes: int = 0
    shortfall_minutes: int = 0
    late_minutes: int = 0
    early_minutes: int = 0
    late_counted: bool = False
    planned_work: Optional[bool] = None
    absence_paid: bool = False
    absence_note: Optional[str] = None

class PayrollRow(BaseModel):
    # Итог по сотруднику за период
    employee: Employee
    has_schedule: bool = False       # Есть ли свой график, действующий в периоде
    days_worked: int = 0
    extra_days: int = 0              # Отработано вне графика
    absences: int = 0                # Прогулы
    vacation_days: int = 0
    sick_days: int = 0
    dayoff_days: int = 0
    worked_minutes: int = 0
    norm_total_minutes: int = 0
    shortfall_minutes: int = 0
    late_count: int = 0
    early_count: int = 0
    base_pay: float = 0.0
    absence_pay: float = 0.0         # Оплата отпусков/больничных с признаком "оплачиваемый"
    underwork_penalty: float = 0.0
    late_penalty_total: float = 0.0
    total_pay: float = 0.0

class PayrollReport(BaseModel):
    period_start: date
    period_end: date
    rows: List[PayrollRow] = []

# Рекурсивная сборка модели категорий
Category.model_rebuild()
