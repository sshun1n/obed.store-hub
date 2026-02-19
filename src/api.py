import os
import logging
from datetime import datetime
from typing import Dict, Any, List
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlalchemy.orm import Session

# Импорт внутренних модулей с учетом новой структуры src
from src.service import (
    AppConfig, GoogleSheetClient, NomenclatureService,
    ConfigurationError, SheetAPIError, ServiceError
)
from src.report_generator import ReportGenerator
import src.api_v1 as api_v1
from src.database import get_db, Product, Category
import src.crud as crud

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("API")

# Инициализация приложения FastAPI
app = FastAPI(
    title="OBED.STORE Hub API",
    version="1.5.0",
    description="Система управления заказами и производством"
)

# Подключение роутера API v1
app.include_router(api_v1.router, prefix="/api/v1")

# Глобальный словарь для сервисов
services: Dict[str, Any] = {}

# Определение базовой директории проекта (на один уровень выше src)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@app.on_event("startup")
async def startup_event() -> None:
    """Инициализация сервисов при запуске сервера."""
    try:
        config = AppConfig()
        config.validate()
        google_client = GoogleSheetClient(config)
        services['nomenclature'] = NomenclatureService(google_client)
        # Путь к отчетам в корне проекта
        reports_dir = os.path.join(BASE_DIR, "reports")
        services['report_gen'] = ReportGenerator(output_dir=reports_dir)
        logger.info("Сервисы успешно инициализированы")
    except Exception as e:
        logger.critical(f"Ошибка при инициализации сервисов: {e}", exc_info=True)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def cleanup_file(path: str) -> None:
    """Удаляет временный файл после отправки пользователю."""
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Временный файл удален: {path}")
    except Exception as e:
        logger.error(f"Ошибка при удалении файла {path}: {e}")

def get_active_config_or_raise(db: Session):
    """Возвращает активную конфигурацию Google Таблиц или выдает ошибку."""
    config = crud.get_active_google_config(db)
    if not config:
        raise HTTPException(
            status_code=400, 
            detail="Активная конфигурация не найдена. Настройте таблицу в админ-панели."
        )
    return config

# --- МОДЕЛИ ЗАПРОСОВ ---

class ReportRequest(BaseModel):
    date: str = datetime.now().strftime("%d.%m.%Y")
    excluded_category_ids: List[int] = []

# --- ЭНДПОИНТЫ ---

@app.get("/health", tags=["Система"])
async def health_check() -> Dict[str, Any]:
    """Проверка работоспособности системы."""
    services_ok = 'nomenclature' in services and 'report_gen' in services
    return {"status": "ok", "services_loaded": services_ok}

@app.get("/nomenclature", tags=["Данные"], response_class=JSONResponse)
async def get_nomenclature_json(db: Session = Depends(get_db)) -> Dict[str, List[Dict[str, Any]]]:
    """Возвращает агрегированную номенклатуру, обогащенную категориями из БД."""
    if 'nomenclature' not in services:
        raise HTTPException(status_code=503, detail="Сервис номенклатуры недоступен")
    
    config = get_active_config_or_raise(db)
    
    try:
        data = services['nomenclature'].get_aggregated_nomenclature(
            config.spreadsheet_id, config.sheet_name
        )
        db_products = db.query(Product).all()
        product_categories_map = {
            p.name_from_sheet: [c.name for c in p.categories] 
            for p in db_products
        }
        enriched_data = []
        for item in data:
            item_categories = product_categories_map.get(item['full_name'], [])
            enriched_data.append({**item, "categories": item_categories})
        return {"data": enriched_data}
    except Exception as e:
        logger.error(f"Ошибка в get_nomenclature_json: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.get("/orders", tags=["Данные"], response_class=JSONResponse)
async def get_orders_json(date: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Возвращает заказы по точкам на указанную дату."""
    if 'nomenclature' not in services:
        raise HTTPException(status_code=503, detail="Сервис заказов недоступен")
    config = get_active_config_or_raise(db)
    try:
        orders = services['nomenclature'].get_orders_by_date(
            config.spreadsheet_id, config.sheet_name, date
        )
        return {"date": date, "count": len(orders), "data": orders}
    except Exception as e:
        logger.error(f"Ошибка в get_orders_json: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при получении заказов")

@app.post("/report/pdf", tags=["Отчеты"], response_class=FileResponse)
async def generate_pdf_report(
    request: ReportRequest, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> FileResponse:
    """Генерирует PDF-отчет с группировкой по категориям и фильтрацией."""
    if 'report_gen' not in services:
        raise HTTPException(status_code=503, detail="Генератор отчетов недоступен")
    
    config = get_active_config_or_raise(db)
    
    try:
        data = services['nomenclature'].get_aggregated_nomenclature(
            config.spreadsheet_id, config.sheet_name
        )
        if not data:
             raise HTTPException(status_code=404, detail="Нет данных для генерации отчета")

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

        if not grouped_data:
            raise HTTPException(status_code=400, detail="Все выбранные товары исключены фильтром")

        for cat_name in grouped_data:
            grouped_data[cat_name].sort(key=lambda x: (
                x['main_name'].split()[0].lower() if x['main_name'].split() else "", 
                -x['quantity'], 
                x['main_name'].lower()
            ))

        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"report_{request.date.replace('.', '')}_{timestamp}.pdf"

        pdf_path = services['report_gen'].generate_pdf(
            grouped_data=grouped_data,
            order_date=request.date,
            filename=filename
        )
        
        background_tasks.add_task(cleanup_file, pdf_path)
        return FileResponse(path=pdf_path, filename=filename, media_type='application/pdf')

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка генерации отчета")

# Подключение статических файлов (путь относительно корня проекта)
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
async def read_root() -> RedirectResponse:
    """Перенаправление на главную страницу."""
    return RedirectResponse(url="/static/index.html")
