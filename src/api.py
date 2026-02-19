import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlalchemy.orm import Session

# Импорт внутренних модулей
from src.service import (
    AppConfig, GoogleSheetClient, NomenclatureService,
    ConfigurationError, SheetAPIError, ServiceError
)
from src.report_generator import ReportGenerator
import src.api_v1 as api_v1
from src.database import get_db, Product, Category
import src.database as database
import src.crud as crud
import src.auth as auth

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("API")

# Инициализация приложения
app = FastAPI(
    title="OBED.STORE Hub API",
    version="1.6.0",
    description="Система управления заказами с серверной авторизацией"
)

# Подключение роутера API
app.include_router(api_v1.router, prefix="/api/v1")

# Глобальный словарь для сервисов
services: Dict[str, Any] = {}
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

@app.on_event("startup")
async def startup_event() -> None:
    try:
        config = AppConfig()
        config.validate()
        google_client = GoogleSheetClient(config)
        services['nomenclature'] = NomenclatureService(google_client)
        reports_dir = os.path.join(BASE_DIR, "reports")
        services['report_gen'] = ReportGenerator(output_dir=reports_dir)
        logger.info("Сервисы инициализированы")
    except Exception as e:
        logger.critical(f"Ошибка старта: {e}", exc_info=True)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def cleanup_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.error(f"Ошибка удаления файла: {e}")

def get_active_config_or_raise(db: Session):
    config = crud.get_active_google_config(db)
    if not config:
        raise HTTPException(status_code=400, detail="Настройте таблицу в админ-панели.")
    return config

# --- ЭНДПОИНТЫ ДАННЫХ ---

@app.get("/api/nomenclature", tags=["Данные"])
async def get_nomenclature_json(db: Session = Depends(get_db), user: Any = Depends(auth.get_current_user)):
    config = get_active_config_or_raise(db)
    try:
        data = services['nomenclature'].get_aggregated_nomenclature(config.spreadsheet_id, config.sheet_name)
        db_products = db.query(Product).all()
        product_categories_map = {p.name_from_sheet: [c.name for c in p.categories] for p in db_products}
        enriched = []
        for item in data:
            enriched.append({**item, "categories": product_categories_map.get(item['full_name'], [])})
        return {"data": enriched}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orders", tags=["Данные"])
async def get_orders_json(date: str, db: Session = Depends(get_db), user: Any = Depends(auth.get_current_user)):
    config = get_active_config_or_raise(db)
    try:
        orders = services['nomenclature'].get_orders_by_date(config.spreadsheet_id, config.sheet_name, date)
        return {"date": date, "count": len(orders), "data": orders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ReportRequest(BaseModel):
    date: str = datetime.now().strftime("%d.%m.%Y")
    excluded_category_ids: List[int] = []

@app.post("/report/pdf", tags=["Отчеты"])
async def generate_pdf_report(
    request: ReportRequest, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: Any = Depends(auth.get_current_user)
):
    config = get_active_config_or_raise(db)
    try:
        data = services['nomenclature'].get_aggregated_nomenclature(config.spreadsheet_id, config.sheet_name)
        if not data: raise HTTPException(status_code=404, detail="Нет данных")
        
        db_products = db.query(Product).all()
        excluded_set = set(request.excluded_category_ids)
        prod_map = {p.name_from_sheet: p.categories for p in db_products}

        grouped_data = {}
        for item in data:
            product_categories = prod_map.get(item['full_name'], [])
            if not product_categories:
                if 0 not in excluded_set:
                    if "БЕЗ КАТЕГОРИИ" not in grouped_data: grouped_data["БЕЗ КАТЕГОРИИ"] = []
                    grouped_data["БЕЗ КАТЕГОРИИ"].append(item)
                continue
            for cat in product_categories:
                if cat.id not in excluded_set:
                    if cat.name not in grouped_data: grouped_data[cat.name] = []
                    grouped_data[cat.name].append(item)

        if not grouped_data: raise HTTPException(status_code=400, detail="Нет данных после фильтра")

        # Функция для нормализации первого слова (группировка Блины/Блинчики)
        def normalize_first_word(name):
            if not name: return ""
            word = name.split()[0].lower()
            import re
            return re.sub(r'(чики|ы|и|чик|ые|ие)$', '', word)

        for cat_name in grouped_data:
            # Сортировка: Группа (нормализованная) -> Кол-во (desc) -> Полное имя
            grouped_data[cat_name].sort(key=lambda x: (
                normalize_first_word(x['main_name']),
                -x['quantity'], 
                x['main_name'].lower()
            ))

        filename = f"report_{request.date.replace('.', '')}_{datetime.now().strftime('%H%M%S')}.pdf"
        pdf_path = services['report_gen'].generate_pdf(grouped_data, request.date, filename)
        background_tasks.add_task(cleanup_file, pdf_path)
        return FileResponse(path=pdf_path, filename=filename, media_type='application/pdf')
    except Exception as e:
        logger.error(f"PDF Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка генерации")

# --- СЕРВЕРНАЯ РОУТИНГ ДЛЯ HTML ---

@app.get("/login", include_in_schema=False)
async def login_page():
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))

async def protected_file_response(filename: str, user: Optional[database.User]):
    if not user: return RedirectResponse(url="/login")
    return FileResponse(os.path.join(STATIC_DIR, filename))

@app.get("/", include_in_schema=False)
async def read_root(user: Optional[database.User] = Depends(auth.get_current_user_html)):
    return await protected_file_response("index.html", user)

@app.get("/{page}", include_in_schema=False)
async def serve_pretty_pages(page: str, user: Optional[database.User] = Depends(auth.get_current_user_html)):
    if page in ["static", "api", "docs", "redoc", "openapi.json", "login"]:
        raise HTTPException(status_code=404)
    file_path = os.path.join(STATIC_DIR, f"{page}.html")
    if os.path.exists(file_path):
        return await protected_file_response(f"{page}.html", user)
    raise HTTPException(status_code=404)

@app.get("/{page}.html", include_in_schema=False)
async def serve_html_pages(page: str, user: Optional[database.User] = Depends(auth.get_current_user_html)):
    if page == "login": return RedirectResponse(url="/login")
    file_path = os.path.join(STATIC_DIR, f"{page}.html")
    if os.path.exists(file_path):
        return await protected_file_response(f"{page}.html", user)
    raise HTTPException(status_code=404)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/{file_name:path}", include_in_schema=False)
async def catch_all_static(file_name: str):
    clean_path = file_name.split('?')[0]
    if not clean_path or clean_path.endswith(".html"): raise HTTPException(status_code=404)
    full_path = os.path.join(STATIC_DIR, clean_path)
    if os.path.exists(full_path) and os.path.isfile(full_path): return FileResponse(full_path)
    raise HTTPException(status_code=404)
