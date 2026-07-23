from pydantic import BaseModel
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
    department: Optional[str] = None
    position: Optional[str] = None
    tab_number: Optional[str] = None
    is_active: bool = True
    pay_type: str = 'shift'          # 'shift' — за смену, 'hour' — за час
    pay_rate: float = 0.0
    norm_minutes: Optional[int] = None
    underwork_penalty_per_hour: float = 0.0
    underwork_tolerance_min: int = 0
    late_penalty: float = 0.0

class EmployeeUpdate(BaseModel):
    position: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None
    pay_type: Optional[str] = None
    pay_rate: Optional[float] = None
    norm_minutes: Optional[int] = None
    underwork_penalty_per_hour: Optional[float] = None
    underwork_tolerance_min: Optional[int] = None
    late_penalty: Optional[float] = None

class Employee(EmployeeBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

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
    # Один день в детализации расчёта
    work_date: date
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    worked_minutes: int = 0
    norm_minutes: Optional[int] = None
    shortfall_minutes: int = 0
    late_minutes: int = 0
    early_minutes: int = 0
    absence: bool = False            # График был, присутствия не было

class PayrollRow(BaseModel):
    # Итог по сотруднику за период
    employee: Employee
    days_worked: int = 0
    absences: int = 0
    worked_minutes: int = 0
    norm_total_minutes: int = 0
    shortfall_minutes: int = 0
    late_count: int = 0
    early_count: int = 0
    base_pay: float = 0.0
    underwork_penalty: float = 0.0
    late_penalty_total: float = 0.0
    total_pay: float = 0.0

class PayrollReport(BaseModel):
    period_start: date
    period_end: date
    rows: List[PayrollRow] = []

# Рекурсивная сборка модели категорий
Category.model_rebuild()
