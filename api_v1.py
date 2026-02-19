import re
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import List
from datetime import timedelta

import crud
import schemas
import auth
from database import get_db

# Импорт для работы с Google Sheets напрямую через API
import gspread
from service import AppConfig, GoogleSheetClient

router = APIRouter()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def extract_spreadsheet_id(url: str) -> str:
    """Извлекает уникальный ID таблицы из её URL."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Неверный формат URL Google Таблицы")
    return match.group(1)

# --- АВТОРИЗАЦИЯ ---

@router.post("/auth/login", tags=["Авторизация"])
async def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: Session = Depends(get_db)
):
    """Выполняет вход в систему и устанавливает защищенную Cookie."""
    user = crud.get_user_by_login(db, login=form_data.username)
    if not user or not crud.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.login}, expires_delta=access_token_expires
    )
    
    # Установка HttpOnly Cookie для защиты от XSS атак
    response.set_cookie(
        key="access_token", 
        value=access_token, 
        httponly=True, 
        max_age=auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    
    return {"status": "ok", "role": user.role}

@router.post("/auth/logout", tags=["Авторизация"])
async def logout(response: Response):
    """Удаляет Cookie авторизации (выход из системы)."""
    response.delete_cookie("access_token")
    return {"status": "ok"}

@router.get("/users/me", response_model=schemas.User, tags=["Пользователи"])
async def read_users_me(current_user: schemas.User = Depends(auth.get_current_user)):
    """Возвращает данные текущего авторизованного пользователя."""
    return current_user

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (Только для админов) ---

@router.get("/users/", response_model=List[schemas.User], tags=["Администрирование: Пользователи"])
def read_users(db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    return crud.get_users(db)

@router.post("/users/", response_model=schemas.User, tags=["Администрирование: Пользователи"])
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    if crud.get_user_by_login(db, login=user.login):
        raise HTTPException(status_code=400, detail="Этот логин уже занят")
    return crud.create_user(db=db, user=user)

@router.put("/users/{user_id}", response_model=schemas.User, tags=["Администрирование: Пользователи"])
def update_user(user_id: int, user: schemas.UserUpdate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    db_user = crud.update_user(db, user_id=user_id, user_data=user)
    if not db_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return db_user

@router.delete("/users/{user_id}", tags=["Администрирование: Пользователи"])
def delete_user(user_id: int, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    if not crud.delete_user(db, user_id=user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {"ok": True}

# --- УПРАВЛЕНИЕ КАТЕГОРИЯМИ ---

@router.post("/categories/", response_model=schemas.Category, tags=["Категории"])
def create_category(category: schemas.CategoryCreate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    return crud.create_category(db=db, category=category)

@router.get("/categories/", response_model=List[schemas.Category], tags=["Категории"])
def read_categories(db: Session = Depends(get_db)):
    """Публичный список категорий для фильтров."""
    return crud.get_categories(db)

@router.put("/categories/{category_id}", response_model=schemas.Category, tags=["Категории"])
def update_category(category_id: int, category: schemas.CategoryCreate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    db_category = crud.update_category(db, category_id=category_id, category_data=category)
    if not db_category:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return db_category

@router.delete("/categories/{category_id}", tags=["Категории"])
def delete_category(category_id: int, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    try:
        if not crud.delete_category(db, category_id=category_id):
            raise HTTPException(status_code=404, detail="Категория не найдена")
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- УПРАВЛЕНИЕ ПРОДУКТАМИ ---

@router.get("/products/", response_model=List[schemas.Product], tags=["Продукты"])
def read_products(db: Session = Depends(get_db)):
    return crud.get_products(db)

@router.post("/products/sync", tags=["Продукты"])
def sync_products(names: List[str], db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    added = crud.sync_products(db, names)
    return {"status": "ok", "added_count": added}

@router.put("/products/{product_id}/categories", response_model=schemas.Product, tags=["Продукты"])
def update_product_categories(product_id: int, update_data: schemas.ProductCategoriesUpdate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    product = crud.update_product_categories(db, product_id, update_data.category_ids)
    if not product:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    return product

# --- УПРАВЛЕНИЕ GOOGLE ТАБЛИЦАМИ ---

@router.post("/google/inspect", tags=["Администрирование: Google"])
async def inspect_spreadsheet(request: schemas.SpreadsheetUrlRequest, admin: schemas.User = Depends(auth.admin_only)):
    """Проверяет доступ к новой таблице и возвращает список её листов."""
    spreadsheet_id = extract_spreadsheet_id(request.url)
    try:
        config = AppConfig()
        client = GoogleSheetClient(config).get_client()
        sh = client.open_by_key(spreadsheet_id)
        return {
            "spreadsheet_id": spreadsheet_id,
            "title": sh.title,
            "worksheets": [ws.title for ws in sh.worksheets()]
        }
    except Exception as e:
        if "PERMISSION_DENIED" in str(e):
             raise HTTPException(status_code=403, detail="Доступ запрещен. Дайте права доступа на почту сервисного аккаунта.")
        raise HTTPException(status_code=400, detail=f"Ошибка Google Sheets: {str(e)}")

@router.get("/google/configs/", response_model=List[schemas.GoogleConfig], tags=["Администрирование: Google"])
def read_google_configs(db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    return crud.get_google_configs(db)

@router.post("/google/configs/", response_model=schemas.GoogleConfig, tags=["Администрирование: Google"])
def create_google_config(config: schemas.GoogleConfigCreate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    return crud.create_google_config(db, config)

@router.put("/google/configs/{config_id}/activate", response_model=schemas.GoogleConfig, tags=["Администрирование: Google"])
def activate_google_config(config_id: int, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    config = crud.activate_google_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Конфигурация не найдена")
    return config

@router.delete("/google/configs/{config_id}", tags=["Администрирование: Google"])
def delete_google_config(config_id: int, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    if not crud.delete_google_config(db, config_id):
        raise HTTPException(status_code=404, detail="Конфигурация не найдена")
    return {"ok": True}
