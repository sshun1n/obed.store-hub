import os
import json
import re
import time
import logging
import gspread
from typing import List, Dict, Tuple, Optional, Any, Set
from google.oauth2.service_account import Credentials
from google.auth.exceptions import TransportError
from gspread.exceptions import APIError, WorksheetNotFound

logger = logging.getLogger(__name__)

# --- КЛАССЫ ИСКЛЮЧЕНИЙ ---

class ServiceError(Exception):
    """Базовое исключение для сервиса."""
    pass

class ConfigurationError(ServiceError):
    """Ошибка конфигурации (отсутствие переменных окружения и т.д.)."""
    pass

class SheetAPIError(ServiceError):
    """Ошибка взаимодействия с Google Sheets API."""
    pass

# --- КОНФИГУРАЦИЯ ---

class AppConfig:
    """Управление конфигурацией из переменных окружения."""
    def __init__(self) -> None:
        self.credentials_json: Optional[str] = os.getenv("GOOGLE_CREDENTIALS_JSON")
        self.scopes: List[str] = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

    def validate(self) -> None:
        if not self.credentials_json:
            raise ConfigurationError("Переменная GOOGLE_CREDENTIALS_JSON не установлена")
        try:
            json.loads(self.credentials_json)
        except json.JSONDecodeError:
            raise ConfigurationError("GOOGLE_CREDENTIALS_JSON содержит невалидный JSON")

# --- КЛИЕНТ GOOGLE SHEETS ---

class GoogleSheetClient:
    """Клиент для работы с Google Sheets API."""
    def __init__(self, config: AppConfig) -> None:
        self.config: AppConfig = config
        self._client: Optional[gspread.Client] = None

    def _authenticate(self) -> gspread.Client:
        try:
            creds_dict: Dict[str, Any] = json.loads(self.config.credentials_json)
            creds = Credentials.from_service_account_info(
                creds_dict, 
                scopes=self.config.scopes
            )
            return gspread.authorize(creds)
        except Exception as e:
            logger.error(f"Ошибка авторизации Google: {e}")
            raise SheetAPIError(f"Авторизация не удалась: {e}")

    def get_client(self) -> gspread.Client:
        if self._client is None:
            self._client = self._authenticate()
        return self._client

    def get_worksheet_data(self, spreadsheet_id: str, sheet_name: str, retries: int = 3, delay: int = 2) -> List[List[Any]]:
        """Загружает все данные с листа с поддержкой повторных попыток."""
        client: gspread.Client = self.get_client()
        for attempt in range(retries):
            try:
                sheet: gspread.Spreadsheet = client.open_by_key(spreadsheet_id)
                worksheet: gspread.Worksheet = sheet.worksheet(sheet_name)
                return worksheet.get_all_values()
            except (APIError, TransportError) as e:
                logger.warning(f"Попытка {attempt + 1}/{retries} не удалась: {e}")
                if attempt == retries - 1:
                    raise SheetAPIError(f"Ошибка API после {retries} попыток: {e}")
                time.sleep(delay * (attempt + 1))
            except WorksheetNotFound:
                raise SheetAPIError(f"Лист '{sheet_name}' не найден в таблице.")
        return []

# --- СЕРВИС НОМЕНКЛАТУРЫ ---

class NomenclatureService:
    """Логика обработки номенклатуры и заказов из таблиц."""
    def __init__(self, sheet_client: GoogleSheetClient) -> None:
        self.client: GoogleSheetClient = sheet_client

    def _find_header_row(self, rows: List[List[str]]) -> Tuple[int, int, int]:
        """Ищет строку заголовков (дату и адрес)."""
        for i in range(min(10, len(rows))):
            row: List[str] = rows[i]
            date_idx, addr_idx = -1, -1
            for col_idx, cell_value in enumerate(row):
                val_lower = cell_value.lower().strip()
                if "дата доставки" in val_lower and date_idx == -1: date_idx = col_idx
                elif "адрес доставки" in val_lower and addr_idx == -1: addr_idx = col_idx
            if date_idx != -1 and addr_idx != -1:
                return i, date_idx, addr_idx
        return -1, -1, -1
    
    def _parse_dish_name(self, full_name: str) -> Tuple[str, str]:
        """Разделяет название блюда на основную часть и состав."""
        # Поиск веса (1/100 или 150 гр)
        match = re.search(r'(\d+\s*/\s*\d+)|(\d+\s*гр\.?)', full_name, re.IGNORECASE)
        if match:
            end_pos = match.end()
            return full_name[:end_pos].strip(), full_name[end_pos:].strip()
        return full_name.strip(), ""

    def get_aggregated_nomenclature(self, spreadsheet_id: str, sheet_name: str) -> List[Dict[str, Any]]:
        """Агрегирует количество всех блюд из таблицы."""
        rows: List[List[Any]] = self.client.get_worksheet_data(spreadsheet_id, sheet_name)
        if len(rows) < 2: return []

        header_idx, _, addr_idx = self._find_header_row(rows)
        if header_idx == -1: header_idx = 1
        
        header_row: List[str] = rows[header_idx]
        nomenclature_map: Dict[str, int] = {}
        start_col = (addr_idx + 1) if addr_idx != -1 else 4

        for col_idx in range(start_col, len(header_row)):
            dish_name = header_row[col_idx].replace('\n', ' ').strip()
            if not dish_name or dish_name.lower() == 'nan': continue

            total_qty = 0
            for row_idx in range(header_idx + 1, len(rows)):
                if col_idx >= len(rows[row_idx]): continue
                val = rows[row_idx][col_idx].strip()
                if val.isdigit(): total_qty += int(val)
            
            if total_qty > 0:
                main_name, composition = self._parse_dish_name(dish_name)
                nomenclature_map[dish_name] = {
                    "full_name": dish_name,
                    "main_name": main_name,
                    "composition": composition,
                    "quantity": total_qty
                }

        # Сортировка по убыванию количества
        sorted_items = sorted(nomenclature_map.values(), key=lambda x: x['quantity'], reverse=True)
        return sorted_items

    def get_orders_by_date(self, spreadsheet_id: str, sheet_name: str, target_date: str) -> List[Dict[str, Any]]:
        """Возвращает детализацию заказов по адресам на дату."""
        rows: List[List[Any]] = self.client.get_worksheet_data(spreadsheet_id, sheet_name)
        if len(rows) < 2: return []

        header_idx, date_col, addr_col = self._find_header_row(rows)
        if header_idx == -1: return []
            
        target_variants = {target_date.strip()}
        try:
            d, m, y = target_date.strip().split('.')
            target_variants.update({f"{y}-{m}-{d}", f"{y}/{m}/{d}"})
        except: pass

        orders = []
        header_row = rows[header_idx]
        start_dish_idx = max(date_col, addr_col) + 1
        
        for row_idx in range(header_idx + 1, len(rows)):
            row = rows[row_idx]
            if len(row) <= date_col: continue
            
            clean_date = row[date_col].strip().split(' ')[0]
            if clean_date in target_variants:
                address = row[addr_col].strip() if len(row) > addr_col else "Без адреса"
                items = []
                for col_idx in range(start_dish_idx, len(header_row)):
                    if col_idx >= len(row): break
                    dish_name = header_row[col_idx].strip()
                    val = row[col_idx].strip()
                    if val.isdigit() and int(val) > 0:
                        main_n, comp = self._parse_dish_name(dish_name)
                        items.append({"main_name": main_n, "composition": comp, "quantity": int(val)})
                if items:
                    orders.append({"address": address or "Без адреса", "items": items})
        return orders
