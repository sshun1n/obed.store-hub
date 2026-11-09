import os
import logging
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean,
    Date, Float, Numeric, Text, UniqueConstraint, inspect, text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

logger = logging.getLogger("Database")

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
    # Полное название из заголовка колонки таблицы — ключ сопоставления, не редактируется.
    # У товаров, добавленных вручную, пусто: с таблицей они не связаны
    name_from_sheet = Column(String, unique=True, index=True, nullable=True)
    # Правки из карточки. Пусто — показываем то, что разобрано из таблицы
    display_name = Column(String, nullable=True)
    composition = Column(String, nullable=True)
    barcode = Column(String(13), unique=True, nullable=True)  # EAN-13
    # Вес порции в граммах. Пусто — берём из названия
    weight_grams = Column(Integer, nullable=True)
    # Белки, жиры и углеводы на 100 г. Калорийность считается из них
    protein = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    # Откуда товар: 'sheet' — программа нашла в таблице, 'manual' — добавил человек
    source = Column(String, default="sheet", nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)  # у старых товаров пусто
    created_by = Column(String, nullable=True)
    # Скрытый товар не показывается в справочнике, но остаётся в базе:
    # синхронизация не создаст его заново, штрих-код и КБЖУ сохраняются
    is_hidden = Column(Boolean, default=False, nullable=True)

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

class SheetSnapshot(Base):
    # Последняя прочитанная копия листа (одна строка). Страницы читают её, а не Google
    __tablename__ = "sheet_snapshots"

    id = Column(Integer, primary_key=True)
    spreadsheet_id = Column(String, nullable=True)
    sheet_name = Column(String, nullable=True)
    rows_json = Column(Text, nullable=True)
    # Местное время. synced_at — последняя удачная синхронизация, last_attempt_at — последняя попытка
    synced_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)

class SyncSettings(Base):
    # Расписание автосинхронизации (одна строка), время местное, ЧЧ:ММ
    __tablename__ = "sync_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=True, nullable=False)
    interval_minutes = Column(Integer, default=30, nullable=False)
    start_time = Column(String, default="08:00", nullable=False)
    end_time = Column(String, default="17:00", nullable=False)
    weekdays = Column(String, default="0,1,2,3,4,5,6")  # 0 — понедельник, как в work_schedules

# Роботы Лавки: заказы шлёт robot-emelya, отчёты — robot-lavka-metrics (у Ателье robot-sla-report)
DEFAULT_LAVKA_SENDERS = "robot-emelya@yandex-team.ru, robot-lavka-metrics@yandex-team.ru, robot-sla-report@yandex-team.ru"

class MailSettings(Base):
    # Ящик, куда Яндекс.Лавка присылает заказы и отчёты (одна строка).
    # Пароль приложения хранится зашифрованным ключом из SECRET_KEY и наружу не отдаётся
    __tablename__ = "mail_settings"

    id = Column(Integer, primary_key=True)
    host = Column(String, default="imap.yandex.ru", nullable=False)
    port = Column(Integer, default=993, nullable=False)
    username = Column(String, nullable=True)
    password_enc = Column(Text, nullable=True)
    folder = Column(String, default="INBOX", nullable=False)
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String, nullable=True)
    # Последняя проверка подключения, время UTC
    checked_at = Column(DateTime, nullable=True)
    check_ok = Column(Boolean, nullable=True)
    check_message = Column(String, nullable=True)

    # Чьи письма разбирать, через запятую. В ящике и уведомления СБИС, и чужая переписка
    senders = Column(Text, nullable=True, default=DEFAULT_LAVKA_SENDERS)
    # Курсор: UID последнего просмотренного письма. Обычный опрос идёт только дальше него
    last_uid = Column(Integer, nullable=True, default=0)
    last_poll_at = Column(DateTime, nullable=True)      # местное время, как у синхронизации
    last_poll_result = Column(String, nullable=True)
    last_poll_error = Column(String, nullable=True)
    # Расписание опроса, местное время. Поля названы как у sync_settings: проверка срока общая
    enabled = Column(Boolean, nullable=True, default=True)
    interval_minutes = Column(Integer, nullable=True, default=30)
    start_time = Column(String, nullable=True, default="08:00")
    end_time = Column(String, nullable=True, default="12:30")
    weekdays = Column(String, nullable=True, default="0,1,2,3,4")

# --- ЗАКАЗЫ ЯНДЕКС.ЛАВКИ (из почты) ---

class LavkaPoint(Base):
    # Лавка-получатель: один склад Яндекса — одна точка доставки
    __tablename__ = "lavka_points"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String(30), unique=True, nullable=False)  # «Склад ИД»
    name = Column(String(150), nullable=False)                     # «Культуры 14»
    address = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class LavkaOrder(Base):
    # Заказ на одну точку. В одном файле их несколько, по числу лавок.
    # Номер ЯЛ… — ключ повторной присылки: тот же номер в новом файле — исправленная версия
    __tablename__ = "lavka_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(30), unique=True, nullable=False)
    order_date = Column(Date, nullable=True)
    delivery_date = Column(Date, nullable=False, index=True)  # «Дата поступления заказа»
    point_id = Column(Integer, ForeignKey("lavka_points.id"), nullable=False, index=True)
    supplier_inn = Column(String(12), nullable=True)
    buyer_inn = Column(String(12), nullable=True)

    # Итоги денормализованы при импорте: список за месяц не пересчитывает строки
    total_quantity = Column(Integer, default=0)
    total_net = Column(Numeric(12, 2), default=0)
    total_gross = Column(Numeric(12, 2), default=0)

    source_message_id = Column(String(255), nullable=True)
    source_filename = Column(String(255), nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    # Заказ переписан более поздним файлом с другим составом — видно на странице
    revised_at = Column(DateTime, nullable=True)

    point = relationship("LavkaPoint")
    lines = relationship("LavkaOrderLine", back_populates="order", cascade="all, delete-orphan",
                         order_by="LavkaOrderLine.id")

class LavkaOrderLine(Base):
    __tablename__ = "lavka_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("lavka_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    # Штрих-код Лавки — ключ сверки со справочником. Товар не храним, а ищем по коду
    # при показе: код в карточку могут внести и после того, как заказ пришёл
    barcode = Column(String(20), nullable=False, index=True)
    vendor_code = Column(String(30), nullable=True)   # «Артикул поставщика», бывает пуст
    lavka_id = Column(String(30), nullable=True)      # «Лавка ИД», SKU Лавки
    name = Column(String(300), nullable=False)        # без невидимых символов и названия марки
    name_raw = Column(String(400), nullable=False)    # как в файле
    quantity = Column(Integer, nullable=False)
    price_net = Column(Numeric(10, 2), nullable=False)
    price_gross = Column(Numeric(10, 2), nullable=False)
    sum_net = Column(Numeric(12, 2), nullable=False)
    sum_gross = Column(Numeric(12, 2), nullable=False)
    vat_rate = Column(Numeric(5, 2), nullable=True)

    order = relationship("LavkaOrder", back_populates="lines")

class LavkaMailFile(Base):
    # Журнал разобранных вложений. Ключ — письмо и файл вместе: в одном письме
    # Лавка присылает два файла с одинаковым именем, и журнал по письму потерял бы второй
    __tablename__ = "lavka_mail_files"
    __table_args__ = (UniqueConstraint("message_id", "attachment_sha256", name="uq_lavka_mail_file"),)

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String(255), nullable=False, index=True)
    attachment_sha256 = Column(String(64), nullable=False, index=True)
    uid = Column(Integer, nullable=True)
    sender = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=True)
    filename = Column(String(255), nullable=True)
    received_at = Column(DateTime, nullable=True)   # UTC
    processed_at = Column(DateTime, default=datetime.utcnow)
    kind = Column(String(20), nullable=True)        # orders | metrics | skipped | error
    orders_found = Column(Integer, default=0)
    note = Column(Text, nullable=True)

class LavkaMetric(Base):
    # Недельная метрика позиции из «Еженедельного отчёта метрик для поставщика».
    # Одна строка — позиция за неделю (неделя приведена к понедельнику). Новый отчёт
    # переписывает свои недели: окно у отчётов четырёхнедельное и внахлёст
    __tablename__ = "lavka_metrics"
    __table_args__ = (UniqueConstraint("week_start", "lavka_id", name="uq_lavka_metric_week_item"),)

    id = Column(Integer, primary_key=True, index=True)
    week_start = Column(Date, nullable=False, index=True)
    lavka_id = Column(String(30), nullable=False, index=True)   # Item ID — тот же «Лавка ИД», что в заказах
    name = Column(String(300), nullable=False)
    category = Column(String(150), nullable=True)
    subcategory = Column(String(150), nullable=True)
    cogs = Column(Numeric(12, 2), default=0)
    sales_qty = Column(Integer, default=0)
    # Пусто — не из чего посчитать (Лавка присылает inf при нулевой выручке). Ноль тут
    # читался бы как «списаний не было», то есть ровно наоборот
    writeoffs_pct = Column(Numeric(8, 2), nullable=True)
    sla_pct = Column(Numeric(6, 2), nullable=True)
    like_share = Column(Numeric(6, 2), nullable=True)
    retention_pct = Column(Numeric(6, 2), nullable=True)
    report_date = Column(Date, nullable=True)       # день письма, из которого строка
    imported_at = Column(DateTime, default=datetime.utcnow)

class LavkaFeedback(Base):
    # Лайк или дизлайк покупателя. Один отзыв приезжает в нескольких отчётах подряд,
    # поэтому ключ — отпечаток содержимого, а не номер строки
    __tablename__ = "lavka_feedback"

    id = Column(Integer, primary_key=True, index=True)
    fingerprint = Column(String(64), unique=True, nullable=False)
    feedback_date = Column(Date, nullable=False, index=True)
    order_date = Column(Date, nullable=True)
    lavka_id = Column(String(30), nullable=True, index=True)
    name = Column(String(300), nullable=False)
    subcategory = Column(String(150), nullable=True)
    liked = Column(Boolean, default=True)
    comment = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

# --- ЮРЛИЦА И ПЕРЕВОЗЧИКИ (Яндекс.Лавка и СБИС) ---

class LegalEntity(Base):
    # Стороны документов: role='own' — мы (продавец, грузоотправитель),
    # role='buyer' — покупатель и грузополучатель (ООО «Яндекс.Лавка»).
    # Реквизиты здесь, а не в коде: юрлиц может стать несколько
    __tablename__ = "legal_entities"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(10), nullable=False, default="own")
    kind = Column(String(10), nullable=False, default="ИП")  # 'ИП' | 'ЮЛ'
    name = Column(String(200), nullable=False)               # короткое имя для списков
    full_name = Column(String(300), nullable=True)           # полное наименование ЮЛ
    inn = Column(String(12), nullable=False, index=True)
    kpp = Column(String(9), nullable=True)                   # только у ЮЛ
    ogrn = Column(String(15), nullable=True)                 # ОГРН или ОГРНИП
    last_name = Column(String(80), nullable=True)            # ФИО — только у ИП
    first_name = Column(String(80), nullable=True)
    middle_name = Column(String(80), nullable=True)
    address = Column(String(400), nullable=True)
    phone = Column(String(50), nullable=True)                # обязателен для ЭТрН по ФЛК
    email = Column(String(150), nullable=True)
    # Идентификатор ящика ЭДО. У Лавки ЭТрН уходят в Контур, без него СБИС выберет свой ящик
    edo_id = Column(String(100), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)  # подставляется в документы по умолчанию
    is_active = Column(Boolean, default=True, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Carrier(Base):
    # Перевозчик: кто везёт заказы до лавок. Машина и права — тут же.
    # Перенесено из ka-webapp, у Обеда перевозчик свой
    __tablename__ = "carriers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)        # как показываем в списках
    kind = Column(String(10), nullable=False, default="ИП")  # 'ИП' | 'ЮЛ' | 'ФЛ'
    inn = Column(String(12), nullable=False, index=True)
    ogrn = Column(String(15), nullable=True)
    kpp = Column(String(9), nullable=True)
    last_name = Column(String(80), nullable=True)
    first_name = Column(String(80), nullable=True)
    middle_name = Column(String(80), nullable=True)
    birth_date = Column(Date, nullable=True)
    birth_place = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(150), nullable=True)
    address = Column(String(300), nullable=True)

    vehicle_model = Column(String(120), nullable=True)
    vehicle_plate = Column(String(20), nullable=True)
    vehicle_type = Column(String(60), nullable=False, default="легковая")
    body_type = Column(String(60), nullable=False, default="закрытый")
    loading_type = Column(String(60), nullable=False, default="ручная")
    capacity_t = Column(Float, nullable=True)          # грузоподъёмность, т
    volume_m3 = Column(Float, nullable=True)           # вместимость, м³

    licence_number = Column(String(30), nullable=True)
    licence_from = Column(Date, nullable=True)
    licence_to = Column(Date, nullable=True)
    licence_region = Column(String(120), nullable=True)
    licence_categories = Column(String(60), nullable=True)
    licence_gibdd = Column(String(20), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)  # «Скрыть» вместо удаления
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- МОДЕЛИ УЧЁТА РАБОЧЕГО ВРЕМЕНИ ---

class Employee(Base):
    # Справочник сотрудников: создаются автоматически при загрузке табеля,
    # настройки оплаты и штрафов задаются вручную в интерфейсе
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    # ФИО ровно как в табеле — по нему сопоставляются строки при загрузке
    full_name = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True) # Отображаемое имя (алиас) в интерфейсе
    department = Column(String, nullable=True)   # Отдел из табеля
    position = Column(String, nullable=True)     # Должность из табеля
    tab_number = Column(String, nullable=True)   # Табельный номер
    phone = Column(String, nullable=True)
    notes = Column(String, nullable=True)
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
    late_tolerance_min = Column(Integer, default=0)  # Опоздание до N минут не штрафуется

    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("WorkSession", back_populates="employee", cascade="all, delete-orphan")
    aliases = relationship("EmployeeAlias", back_populates="employee", cascade="all, delete-orphan")
    schedules = relationship("WorkSchedule", back_populates="employee", cascade="all, delete-orphan")
    absences = relationship("EmployeeAbsence", back_populates="employee", cascade="all, delete-orphan")

class EmployeeAlias(Base):
    # Дополнительные написания ФИО в табелях, которые относятся к этому сотруднику
    # ("Тарасов Михаил" и "Тарасов Михаил Александрович" — один человек)
    __tablename__ = "employee_aliases"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    employee = relationship("Employee", back_populates="aliases")

class WorkSchedule(Base):
    # График работы на период. При пересечении периодов действует тот,
    # что начался позже
    __tablename__ = "work_schedules"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=True)            # None — бессрочно

    kind = Column(String, nullable=False, default='weekly')  # 'weekly' | 'cycle'
    weekdays = Column(String, nullable=True)         # "0,1,2,3,4", 0 — понедельник
    cycle_work = Column(Integer, nullable=True)      # 2/2: рабочих дней в цикле
    cycle_off = Column(Integer, nullable=True)       # 2/2: выходных в цикле
    cycle_start = Column(Date, nullable=True)        # Первый рабочий день цикла

    shift_start = Column(String, nullable=True)      # "09:00"
    shift_end = Column(String, nullable=True)        # "18:00"
    break_minutes = Column(Integer, default=0)
    shift_minutes = Column(Integer, nullable=True)   # Явная длительность смены, приоритетнее времени
    note = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", back_populates="schedules")

class EmployeeAbsence(Base):
    # Уважительное отсутствие: отпуск, больничный, отгул
    __tablename__ = "employee_absences"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)            # 'vacation' | 'sick' | 'dayoff'
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    paid = Column(Boolean, default=False)
    note = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", back_populates="absences")

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

def _sql_default(value) -> str:
    if isinstance(value, bool):
        return " DEFAULT TRUE" if value else " DEFAULT FALSE"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    if isinstance(value, str):
        return " DEFAULT '" + value.replace("'", "''") + "'"
    return ""

def _add_missing_columns():
    # create_all не меняет уже существующие таблицы, поэтому новые колонки
    # моделей добавляем сами (только ADD COLUMN, без удаления и изменения типов)
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in present or col.primary_key:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    default = _sql_default(col.default.arg)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}{default}"))
                logger.info(f"Миграция: добавлена колонка {table.name}.{col.name}")
                # ADD COLUMN выше уникальность не создаёт
                if col.unique:
                    conn.execute(text(f"CREATE UNIQUE INDEX uq_{table.name}_{col.name} ON {table.name} ({col.name})"))

def _unlink_manual_products():
    # Ручные товары не связаны с таблицей. Колонка раньше была обязательной,
    # и в ней лежало введённое название. На SQLite (только локально) база создаётся заново
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE products ALTER COLUMN name_from_sheet DROP NOT NULL"))
        conn.execute(text("UPDATE products SET name_from_sheet = NULL WHERE source = 'manual'"))

# ООО «Яндекс.Лавка» — покупатель и грузополучатель. Реквизиты из её требований
# к ЭТрН (сентябрь 2026), те же, что в ka-webapp. Адрес юридический
YANDEX_LAVKA = dict(
    role="buyer", kind="ЮЛ", name="Яндекс.Лавка", full_name="ООО «Яндекс.Лавка»",
    inn="9718101499", kpp="770301001",
    address="г. Москва, муниципальный округ Пресненский вн.тер.г., 1-й Красногвардейский пр-д, "
            "д. 22, стр. 1, ЭТАЖ 12, ПОМЕЩ. 12-40, 123112",
    phone="+7 (800) 770-04-60",
    edo_id="2BM-9718101499-770501001-201807120551103851590",
    is_default=True,
    notes="КПП в идентификаторе ящика (770501001) отличается от КПП в реквизитах (770301001): "
          "так и в ka-webapp, где документы проходят. Ящик — в Контуре.",
)

def _seed_buyer():
    # Покупатель нужен сразу: без него не собрать ни один документ
    db = SessionLocal()
    try:
        if not db.query(LegalEntity).filter(LegalEntity.role == "buyer").first():
            db.add(LegalEntity(**YANDEX_LAVKA))
            db.commit()
    finally:
        db.close()

def init_db():
    # Создает недостающие таблицы и колонки (идемпотентно)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _unlink_manual_products()
    _seed_buyer()

def get_db():
    # Зависимость для эндпоинтов (управление сессиями)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
