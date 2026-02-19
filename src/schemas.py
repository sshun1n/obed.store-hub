from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

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

# Рекурсивная сборка модели категорий
Category.model_rebuild()
