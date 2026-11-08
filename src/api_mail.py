"""Настройки почты, куда Яндекс.Лавка присылает заказы и отчёты.

Пока только хранение и проверка подключения. Ящик открывается на чтение:
письма не помечаются и не удаляются.
"""
import imaplib
import socket
import ssl
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src import auth, schemas, secret_box
from src.database import MailSettings, get_db

router = APIRouter(prefix="/mail", tags=["Администрирование: Почта Лавки"])

IMAP_TIMEOUT = 20  # секунд на соединение и каждый ответ сервера


def get_settings(db: Session) -> MailSettings:
    settings = db.query(MailSettings).first()
    if settings is None:
        settings = MailSettings(id=1, host="imap.yandex.ru", port=993, folder="INBOX")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def settings_view(settings: MailSettings) -> dict:
    stored = secret_box.decrypt(settings.password_enc)
    return {
        "host": settings.host,
        "port": settings.port,
        "username": settings.username or "",
        "folder": settings.folder,
        # Сам пароль наружу не отдаётся
        "password_set": stored is not None,
        # Пароль есть, но не расшифровывается: сменился SECRET_KEY или база скопирована с другого стенда
        "password_unreadable": bool(settings.password_enc) and stored is None,
        "updated_at": settings.updated_at,
        "updated_by": settings.updated_by,
        "checked_at": settings.checked_at,
        "check_ok": settings.check_ok,
        "check_message": settings.check_message,
    }


def _imap_quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_connection(host: str, port: int, username: str, password: str, folder: str) -> str:
    """Входит в ящик и открывает папку на чтение. Возвращает текст результата или бросает ValueError."""
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT, ssl_context=ssl.create_default_context())
    except (socket.gaierror, ConnectionRefusedError):
        raise ValueError(f"Сервер {host}:{port} недоступен. Проверьте адрес и порт")
    except (socket.timeout, TimeoutError):
        raise ValueError(f"Сервер {host}:{port} не ответил за {IMAP_TIMEOUT} секунд")
    except ssl.SSLError as e:
        raise ValueError(f"Ошибка защищённого соединения: {e.reason or e}")
    except OSError as e:
        raise ValueError(f"Не удалось подключиться: {e}")
    try:
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error:
            raise ValueError(
                "Ящик не принял логин или пароль. Нужен пароль приложения, "
                "и в настройках почты должен быть включён доступ по IMAP"
            )
        status, data = conn.select(_imap_quote(folder), readonly=True)
        if status != "OK":
            raise ValueError(f"Папка «{folder}» не найдена")
        count = int(data[0]) if data and data[0] else 0
        return f"Подключение работает. В папке «{folder}» писем: {count}"
    except (socket.timeout, TimeoutError):
        raise ValueError(f"Сервер не ответил за {IMAP_TIMEOUT} секунд")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _apply(settings: MailSettings, data: schemas.MailSettingsUpdate) -> None:
    settings.host = data.host.strip()
    settings.port = data.port
    settings.username = data.username.strip() or None
    settings.folder = data.folder.strip()
    if data.clear_password:
        settings.password_enc = None
    elif data.password:
        settings.password_enc = secret_box.encrypt(data.password)


@router.get("/settings")
def read_mail_settings(db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    return settings_view(get_settings(db))


@router.put("/settings")
def update_mail_settings(data: schemas.MailSettingsUpdate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    settings = get_settings(db)
    _apply(settings, data)
    settings.updated_at = datetime.utcnow()
    settings.updated_by = admin.full_name or admin.login
    # Прежняя проверка относилась к старым настройкам
    settings.checked_at = None
    settings.check_ok = None
    settings.check_message = None
    db.commit()
    db.refresh(settings)
    return settings_view(settings)


@router.post("/check")
def check_mail(data: schemas.MailSettingsUpdate, db: Session = Depends(get_db), admin: schemas.User = Depends(auth.admin_only)):
    """Проверяет значения из формы, не сохраняя их. Пустой пароль — берётся сохранённый."""
    settings = get_settings(db)
    password = None if data.clear_password else (data.password or secret_box.decrypt(settings.password_enc))
    if not data.username.strip():
        raise HTTPException(status_code=400, detail="Укажите адрес ящика")
    if not password:
        raise HTTPException(status_code=400, detail="Введите пароль приложения")
    try:
        message = check_connection(data.host.strip(), data.port, data.username.strip(), password, data.folder.strip())
        ok = True
    except ValueError as e:
        message, ok = str(e), False
    # Результат запоминаем, только если проверяли то, что сохранено
    same_as_saved = (
        data.host.strip() == settings.host and data.port == settings.port
        and data.username.strip() == (settings.username or "") and data.folder.strip() == settings.folder
        and not data.password and not data.clear_password
    )
    if same_as_saved:
        settings.checked_at = datetime.utcnow()
        settings.check_ok = ok
        settings.check_message = message
        db.commit()
    return {"ok": ok, "message": message, "saved": same_as_saved}
