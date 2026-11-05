"""Снимок Google Таблицы в базе и автосинхронизация по расписанию.

Страницы читают только снимок и не ждут Google. Таблицу читает один этот модуль:
по кнопке, по расписанию и при первом запуске или смене таблицы.
"""
import json
import logging
import threading
import time as time_module
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src import crud, database
from src.service import AppConfig, GoogleSheetClient, NomenclatureService

logger = logging.getLogger("SheetSync")

# Серверы работают в UTC, а расписание задаётся по местному времени. Сдвиг задан
# явно: в России нет перехода на летнее время, а tzdata в образе не гарантирована
LOCAL_TZ = timezone(timedelta(hours=3))

CHECK_EVERY_SECONDS = 20
GOOGLE_TIMEOUT_SECONDS = 60
# Кто нажал кнопку во время идущей синхронизации, ждёт её результат
JOIN_WAIT_SECONDS = 180
# Снимка ещё нет, а Google недоступен: не долбим его при каждой проверке
FIRST_SYNC_RETRY = timedelta(minutes=5)

nomenclature = NomenclatureService()

_lock = threading.Lock()
_client: Optional[GoogleSheetClient] = None
_rows_cache: Optional[Tuple[Tuple[Any, ...], List[List[str]]]] = None
_scheduler_started = False


def local_now() -> datetime:
    """Местное время без пояса: так оно хранится в базе и показывается в интерфейсе."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def _google_client() -> GoogleSheetClient:
    global _client
    if _client is None:
        config = AppConfig()
        config.validate()
        client = GoogleSheetClient(config)
        client.get_client().set_timeout(GOOGLE_TIMEOUT_SECONDS)  # без него зависший запрос держит блокировку вечно
        _client = client
    return _client


def _snapshot_matches(snapshot: Optional[database.SheetSnapshot], config: database.GoogleConfig) -> bool:
    return bool(
        snapshot and snapshot.synced_at
        and snapshot.spreadsheet_id == config.spreadsheet_id
        and snapshot.sheet_name == config.sheet_name
    )


def get_rows(db, config: database.GoogleConfig) -> Tuple[Optional[List[List[str]]], Optional[datetime]]:
    """Строки снимка для подключённой таблицы; (None, None), если снимка ещё нет."""
    global _rows_cache
    snapshot = db.query(database.SheetSnapshot).first()
    if not _snapshot_matches(snapshot, config):
        return None, None
    # Лист бывает большим, поэтому разобранный снимок держим, пока он не сменится
    key = (snapshot.spreadsheet_id, snapshot.sheet_name, snapshot.synced_at)
    cached = _rows_cache
    if cached is None or cached[0] != key:
        cached = (key, json.loads(snapshot.rows_json or "[]"))
        _rows_cache = cached
    return cached[1], snapshot.synced_at


def get_settings(db) -> database.SyncSettings:
    settings = db.query(database.SyncSettings).first()
    if settings is None:
        settings = database.SyncSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_status(db) -> Dict[str, Any]:
    config = crud.get_active_google_config(db)
    snapshot = db.query(database.SheetSnapshot).first()
    settings = get_settings(db)
    matches = bool(config) and _snapshot_matches(snapshot, config)
    return {
        "configured": bool(config),
        "synced_at": snapshot.synced_at.isoformat(timespec="seconds") if matches else None,
        "last_error": snapshot.last_error if snapshot else None,
        "running": _lock.locked(),
        "auto": {
            "enabled": settings.enabled,
            "interval_minutes": settings.interval_minutes,
            "start_time": settings.start_time,
            "end_time": settings.end_time,
        },
    }


def _sync_locked() -> int:
    db = database.SessionLocal()
    try:
        config = crud.get_active_google_config(db)
        if not config:
            return 0
        snapshot = db.query(database.SheetSnapshot).first()
        if snapshot is None:
            snapshot = database.SheetSnapshot(spreadsheet_id=config.spreadsheet_id, sheet_name=config.sheet_name)
            db.add(snapshot)
        try:
            rows = _google_client().get_worksheet_data(config.spreadsheet_id, config.sheet_name)
        except Exception as e:
            # Прежний снимок остаётся: лучше старые данные с пометкой, чем пустая страница
            logger.error(f"Синхронизация не удалась: {e}", exc_info=True)
            snapshot.last_attempt_at = local_now()
            snapshot.last_error = str(e)[:300] or "Google Таблица недоступна"
            db.commit()
            return 0

        now = local_now()
        snapshot.spreadsheet_id = config.spreadsheet_id
        snapshot.sheet_name = config.sheet_name
        snapshot.rows_json = json.dumps(rows, ensure_ascii=False)
        snapshot.synced_at = now
        snapshot.last_attempt_at = now
        snapshot.last_error = None
        names = [item["full_name"] for item in nomenclature.get_aggregated_nomenclature(rows)]
        added = crud.sync_products(db, names)  # сохраняет и снимок
        logger.info(f"Синхронизация: строк {len(rows)}, новых товаров {added}")
        return added
    finally:
        db.close()


def run_sync() -> int:
    """Читает таблицу в снимок и возвращает число новых товаров. Ошибка пишется в снимок."""
    if not _lock.acquire(blocking=False):
        if _lock.acquire(timeout=JOIN_WAIT_SECONDS):
            _lock.release()
        return 0
    try:
        return _sync_locked()
    finally:
        _lock.release()


def is_due(settings: database.SyncSettings, last_attempt: Optional[datetime], now: datetime) -> bool:
    """Наступил ли очередной срок: start, start + интервал, … включая end."""
    if not settings.enabled:
        return False
    start = datetime.combine(now.date(), time.fromisoformat(settings.start_time))
    end = datetime.combine(now.date(), time.fromisoformat(settings.end_time))
    if now < start or now.replace(second=0, microsecond=0) > end:
        return False
    interval = timedelta(minutes=settings.interval_minutes)
    slot = start + ((now - start) // interval) * interval
    return last_attempt is None or last_attempt < slot


def _should_sync_now() -> bool:
    db = database.SessionLocal()
    try:
        config = crud.get_active_google_config(db)
        if not config:
            return False
        snapshot = db.query(database.SheetSnapshot).first()
        last_attempt = snapshot.last_attempt_at if snapshot else None
        now = local_now()
        # Снимка этой таблицы ещё нет: первый запуск или таблицу сменили
        if not _snapshot_matches(snapshot, config):
            return last_attempt is None or now - last_attempt >= FIRST_SYNC_RETRY
        return is_due(get_settings(db), last_attempt, now)
    finally:
        db.close()


def _scheduler_loop() -> None:
    while True:
        try:
            if _should_sync_now():
                run_sync()
        except Exception as e:
            logger.error(f"Ошибка планировщика синхронизации: {e}", exc_info=True)
        time_module.sleep(CHECK_EVERY_SECONDS)


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, name="sheet-sync", daemon=True).start()
