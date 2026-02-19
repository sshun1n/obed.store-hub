import os
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean
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
