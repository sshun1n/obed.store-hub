import os
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean,
    Date, Float, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

# Загружаем переменные окружения из .env
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("Переменная окружения DATABASE_URL не установлена!")

# Инициализация SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- МОДЕЛИ ТАБЛИЦ БАЗЫ ДАННЫХ ---

class User(Base):
    # Модель пользователя (админы и менеджеры)
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String)
    role = Column(String, index=True) # 'admin' или 'manager'
    phone = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    # Модель категорий (Цеха, Типы меню и т.д.) с поддержкой иерархии
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    # Ссылка на родительскую категорию для древовидной структуры
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship("Category", back_populates="parent")
    products = relationship("Product", secondary="product_category_map", back_populates="categories")

class Product(Base):
    # Справочник уникальных блюд, синхронизированный с Google Таблицами
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    name_from_sheet = Column(String, unique=True, index=True, nullable=False)

    categories = relationship("Category", secondary="product_category_map", back_populates="products")

class ProductCategoryMap(Base):
    # Промежуточная таблица для связи блюд с категориями (многие-ко-многим)
    __tablename__ = "product_category_map"

    product_id = Column(Integer, ForeignKey("products.id"), primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), primary_key=True)

class GoogleConfig(Base):
    # Конфигурация активных Google Таблиц
    __tablename__ = "google_configs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=True) # Имя таблицы (для отображения)
    url = Column(String, nullable=False)   # Полная ссылка на таблицу
    spreadsheet_id = Column(String, nullable=False)
    sheet_name = Column(String, nullable=False)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- МОДЕЛИ УЧЁТА РАБОЧЕГО ВРЕМЕНИ ---

class Employee(Base):
    # Справочник сотрудников: создаются автоматически при загрузке табеля,
    # настройки оплаты и штрафов задаются вручную в интерфейсе
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, unique=True, index=True, nullable=False)
    department = Column(String, nullable=True)   # Отдел из табеля
    position = Column(String, nullable=True)     # Должность из табеля
    tab_number = Column(String, nullable=True)   # Табельный номер
    is_active = Column(Boolean, default=True)    # Архивация без удаления истории

    # Оплата: 'shift' — за смену, 'hour' — за час
    pay_type = Column(String, default='shift')
    pay_rate = Column(Float, default=0.0)

    # Норма минут за смену (например 540 = 9ч). Если не задана —
    # берётся значение из колонки "График" табеля за конкретный день
    norm_minutes = Column(Integer, nullable=True)

    # Штрафы: за час недоработки, допуск без штрафа, фикс за опоздание
    underwork_penalty_per_hour = Column(Float, default=0.0)
    underwork_tolerance_min = Column(Integer, default=0)
    late_penalty = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("WorkSession", back_populates="employee", cascade="all, delete-orphan")

class WorkSession(Base):
    # Одна строка табеля: рабочий день сотрудника
    __tablename__ = "work_sessions"
    __table_args__ = (UniqueConstraint('employee_id', 'work_date', name='uq_employee_workdate'),)

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

    check_in = Column(String, nullable=True)          # Время прихода "7:23"
    check_out = Column(String, nullable=True)         # Время ухода "15:40"
    worked_minutes = Column(Integer, default=0)       # Фактическая наработка
    schedule_minutes = Column(Integer, nullable=True) # Смена по графику
    schedule_worked_minutes = Column(Integer, nullable=True) # Наработка в рамках графика

    late_minutes = Column(Integer, default=0)         # Опоздание, минут
    late_raw = Column(String, nullable=True)          # Исходная строка нарушения
    early_minutes = Column(Integer, default=0)        # Ранний уход, минут
    early_raw = Column(String, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee", back_populates="sessions")

class TimesheetImport(Base):
    # Журнал загрузок табелей (сам файл после разбора удаляется с диска)
    __tablename__ = "timesheet_imports"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    employees_count = Column(Integer, default=0)
    sessions_count = Column(Integer, default=0)
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def init_db():
    # Создает все описанные выше таблицы в PostgreSQL
    Base.metadata.create_all(bind=engine)

def get_db():
    # Зависимость для эндпоинтов (управление сессиями)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
