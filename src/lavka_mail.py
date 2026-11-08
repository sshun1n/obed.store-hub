"""Заказы Яндекс.Лавки из почты: чтение ящика, разбор вложений, запись в базу.

Перенесено из ka-webapp (yandex_mail, yandex_import) с двумя отличиями:
* журнал ведётся по паре «письмо + файл»: Лавка кладёт в одно письмо два файла
  с одинаковым именем, и журнал по письму пропустил бы второй;
* отчёты Лавки (уровень сервиса, метрики) пока не разбираются и в журнал не
  пишутся: когда появится их разбор, «Загрузить историю» подберёт их.

Ящик открывается только на чтение: письма не помечаются и не удаляются.
От повторов три защиты: письмо+файл в журнале, тот же файл в другом письме,
номер заказа (повтор номера — исправленная версия, строки заменяются).
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
import socket
import ssl
import threading
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Dict, List, Optional, Set, Tuple

from src import database, secret_box, sheet_sync
from src.lavka_parser import KIND_ORDERS, ParsedOrder, ParseError, detect_kind, parse_orders

logger = logging.getLogger("LavkaMail")

IMAP_TIMEOUT = 30
MAX_ATTACHMENT = 8 * 1024 * 1024   # больше заказ быть не может; защита от видео в памяти
BATCH = 40                          # писем за один заход
HISTORY_MAX_BATCHES = 80
TABLE_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",     # некоторые отправители тип не ставят
}
MESSAGE_ID_RE = re.compile(rb"message-id:\s*(<[^>]+>)", re.IGNORECASE)

# Одна операция с ящиком за раз: опрос по расписанию, кнопка и история не пересекаются
_lock = threading.Lock()
_history = {"running": False, "done": 0, "remaining": 0}
_scheduler_started = False


class MailError(Exception):
    pass


@dataclass
class Attachment:
    filename: str
    content: bytes


@dataclass
class Letter:
    uid: int
    message_id: str
    sender: str
    subject: str
    received_at: Optional[datetime]
    attachments: List[Attachment] = field(default_factory=list)


# --- Чтение ящика ---

def _decode(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw


def _attachments(message: Message) -> List[Attachment]:
    found = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode(part.get_filename())
        if not filename:
            continue
        if not (filename.lower().endswith((".xlsx", ".xls")) or part.get_content_type() in TABLE_TYPES):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # битое вложение не роняет весь опрос
            continue
        if payload and len(payload) <= MAX_ATTACHMENT:
            found.append(Attachment(filename=filename, content=payload))
    return found


def _received_at(message: Message) -> Optional[datetime]:
    try:
        value = parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError):
        return None
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def senders_of(settings: database.MailSettings) -> List[str]:
    raw = settings.senders if settings.senders is not None else database.DEFAULT_LAVKA_SENDERS
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def _from_criteria(senders: List[str]) -> str:
    """«От любого из адресов». OR в IMAP префиксный и на два аргумента — сворачиваем вправо."""
    if not senders:
        return "ALL"
    criteria = f'(FROM "{senders[-1]}")'
    for sender in reversed(senders[:-1]):
        criteria = f'(OR (FROM "{sender}") {criteria})'
    return criteria


def _connect(settings: database.MailSettings) -> imaplib.IMAP4_SSL:
    password = secret_box.decrypt(settings.password_enc)
    if not settings.username or not password:
        raise MailError("Почта не настроена: укажите ящик и пароль приложения в админ-панели")
    try:
        client = imaplib.IMAP4_SSL(settings.host, settings.port, timeout=IMAP_TIMEOUT,
                                   ssl_context=ssl.create_default_context())
    except (OSError, ssl.SSLError) as exc:
        raise MailError(f"Не удалось подключиться к {settings.host}: {exc}") from exc
    try:
        client.login(settings.username, password)
    except imaplib.IMAP4.error as exc:
        raise MailError("Ящик не принял логин или пароль приложения") from exc
    folder = '"' + settings.folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    status, _ = client.select(folder, readonly=True)
    if status != "OK":
        raise MailError(f"Не удалось открыть папку «{settings.folder}»")
    return client


def fetch_letters(settings: database.MailSettings, since_uid: int, known_ids: Set[str],
                  limit: int = BATCH) -> Tuple[List[Letter], int, int]:
    """Письма отправителей Лавки после since_uid: (письма, последний просмотренный UID, осталось).

    Берём самые старые из новых: курсор ползёт вперёд и за несколько заходов
    вычитывает ящик целиком. Тела качаем только у писем, которых нет в журнале.
    """
    client = _connect(settings)
    try:
        status, data = client.uid("SEARCH", None, f"UID {since_uid + 1}:*", _from_criteria(senders_of(settings)))
        if status != "OK":
            raise MailError("Поиск писем не удался")
        # На «UID N:*» сервер отдаёт и последнее письмо, даже если оно старее N
        pending = sorted(uid for uid in (int(x) for x in (data[0] or b"").split()) if uid > since_uid)
        batch = pending[:limit]
        if not batch:
            return [], since_uid, 0

        status, payload = client.uid("FETCH", ",".join(map(str, batch)), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        seen: Dict[int, str] = {}
        if status == "OK":
            index = 0
            for part in payload:
                if not isinstance(part, tuple):
                    continue
                match = MESSAGE_ID_RE.search(part[1] or b"")
                if index < len(batch):
                    seen[batch[index]] = match.group(1).decode(errors="replace") if match else ""
                index += 1

        letters = []
        for uid in batch:
            if seen.get(uid) and seen[uid] in known_ids:
                continue
            # PEEK: письмо не помечается прочитанным
            status, payload = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            letters.append(Letter(
                uid=uid,
                message_id=_decode(message.get("Message-ID")) or f"uid-{uid}",
                sender=parseaddr(_decode(message.get("From")))[1],
                subject=_decode(message.get("Subject")),
                received_at=_received_at(message),
                attachments=_attachments(message),
            ))
        return letters, batch[-1], len(pending) - len(batch)
    except (socket.timeout, TimeoutError, imaplib.IMAP4.abort) as exc:
        raise MailError(f"Почтовый сервер оборвал связь: {exc}") from exc
    finally:
        try:
            client.logout()
        except Exception:
            pass


# --- Запись в базу ---

@dataclass
class ImportResult:
    created: List[str] = field(default_factory=list)
    revised: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    reports: int = 0          # отчёты Лавки: отложены до их разбора
    skipped: int = 0
    errors: List[str] = field(default_factory=list)

    def add(self, other: "ImportResult") -> None:
        self.created += other.created
        self.revised += other.revised
        self.unchanged += other.unchanged
        self.reports += other.reports
        self.skipped += other.skipped
        self.errors += other.errors


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _signature(lines) -> str:
    """Отпечаток состава: отличает настоящее исправление от повторной присылки того же."""
    cents = Decimal("0.01")
    parts = sorted(f"{l.barcode}:{l.quantity}:{Decimal(l.sum_gross).quantize(cents)}" for l in lines)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _resolve_point(db, parsed: ParsedOrder) -> database.LavkaPoint:
    point = db.query(database.LavkaPoint).filter(database.LavkaPoint.external_id == parsed.point_external_id).first()
    if point is None:
        point = database.LavkaPoint(external_id=parsed.point_external_id,
                                    name=parsed.point_name or parsed.point_address or parsed.point_external_id,
                                    address=parsed.point_address or None)
        db.add(point)
        db.flush()
    elif parsed.point_name and point.name != parsed.point_name:
        # Лавку могли переименовать — справочник держим свежим
        point.name = parsed.point_name
        point.address = parsed.point_address or point.address
    return point


def save_orders(db, parsed_orders: List[ParsedOrder], message_id: str = "", filename: str = "") -> ImportResult:
    result = ImportResult()
    for parsed in parsed_orders:
        point = _resolve_point(db, parsed)
        order = db.query(database.LavkaOrder).filter(database.LavkaOrder.order_no == parsed.order_no).first()
        if order is None:
            order = database.LavkaOrder(order_no=parsed.order_no)
            db.add(order)
            result.created.append(parsed.order_no)
        else:
            # Файл всегда приходит полным: старые строки заменяем целиком
            if _signature(order.lines) != _signature(parsed.lines):
                order.revised_at = datetime.utcnow()
                result.revised.append(parsed.order_no)
            else:
                result.unchanged.append(parsed.order_no)
            order.lines.clear()
            db.flush()

        order.order_date = parsed.order_date
        order.delivery_date = parsed.delivery_date
        order.point_id = point.id
        order.supplier_inn = parsed.supplier_inn or None
        order.buyer_inn = parsed.buyer_inn or None
        order.total_quantity = parsed.total_quantity
        order.total_net = parsed.total_net
        order.total_gross = parsed.total_gross
        order.source_message_id = message_id or None
        order.source_filename = filename or None
        for line in parsed.lines:
            order.lines.append(database.LavkaOrderLine(
                barcode=line.barcode, vendor_code=line.vendor_code or None, lavka_id=line.lavka_id or None,
                name=line.name, name_raw=line.name_raw, quantity=line.quantity,
                price_net=line.price_net, price_gross=line.price_gross,
                sum_net=line.sum_net, sum_gross=line.sum_gross, vat_rate=line.vat_rate,
            ))
        db.flush()
    return result


def import_attachment(db, content: bytes, *, filename: str = "", message_id: str = "", uid: Optional[int] = None,
                      sender: str = "", subject: str = "", received_at: Optional[datetime] = None) -> ImportResult:
    """Одно вложение: проверка на повтор, разбор, запись. Коммитит сам."""
    digest = sha256(content)
    journal = database.LavkaMailFile
    if message_id and db.query(journal).filter(journal.message_id == message_id, journal.attachment_sha256 == digest).first():
        return ImportResult(skipped=1)

    def note(kind: str, text: str, found: int = 0) -> None:
        if not message_id:
            return
        db.add(journal(message_id=message_id, attachment_sha256=digest, uid=uid, sender=sender or None,
                       subject=(subject or "")[:500] or None, filename=(filename or "")[:255] or None,
                       received_at=received_at, kind=kind, orders_found=found, note=text or None))

    # Тот же файл байт в байт уже приходил другим письмом
    if db.query(journal).filter(journal.attachment_sha256 == digest, journal.kind == KIND_ORDERS).first():
        note("skipped", "такой же файл уже приходил")
        db.commit()
        return ImportResult(skipped=1)

    try:
        kind = detect_kind(content)
        if kind and kind != KIND_ORDERS:
            # Отчёты Лавки разберём на следующем этапе. В журнал не пишем, чтобы «Загрузить историю» их потом подобрала
            return ImportResult(reports=1)
        if not kind:
            note("skipped", "не файл заказа Лавки")
            db.commit()
            return ImportResult(skipped=1)
        parsed = parse_orders(content, filename=filename)
    except ParseError as exc:
        db.rollback()
        note("error", f"ошибка разбора: {exc}")
        db.commit()
        return ImportResult(errors=[f"{filename}: {exc}"])

    try:
        result = save_orders(db, parsed, message_id=message_id, filename=filename)
        parts = []
        if result.created:
            parts.append("новые: " + ", ".join(result.created))
        if result.revised:
            parts.append("исправлены: " + ", ".join(result.revised))
        if result.unchanged:
            parts.append(f"без изменений: {len(result.unchanged)}")
        note(KIND_ORDERS, "; ".join(parts), len(parsed))
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        logger.error(f"Не удалось записать заказы из {filename}: {exc}", exc_info=True)
        return ImportResult(errors=[f"{filename}: не удалось записать ({exc})"])


def get_settings(db) -> database.MailSettings:
    settings = db.query(database.MailSettings).first()
    if settings is None:
        settings = database.MailSettings(id=1, host="imap.yandex.ru", port=993, folder="INBOX")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _summary(letters: int, result: ImportResult, remaining: int) -> str:
    if not letters and not remaining:
        return "новых писем нет"
    parts = [f"писем: {letters}"]
    if result.created:
        parts.append(f"новых заказов: {len(result.created)}")
    if result.revised:
        parts.append(f"исправлено: {len(result.revised)}")
    if result.reports:
        parts.append(f"отчётов Лавки отложено: {result.reports}")
    if result.errors:
        parts.append(f"ошибок: {len(result.errors)}")
    if not (result.created or result.revised or result.errors) and letters:
        parts.append("новых заказов нет")
    if remaining:
        parts.append(f"осталось писем: {remaining}")
    return ", ".join(parts)


def _check_locked(db, batch: int = BATCH) -> Tuple[str, int, ImportResult]:
    """Очередная порция писем. Курсор двигается по просмотренным письмам, а не по удачно
    разобранным: письмо с ошибкой записано в журнал и очередь не держит."""
    settings = get_settings(db)
    settings.last_poll_at = sheet_sync.local_now()
    known = {row[0] for row in db.query(database.LavkaMailFile.message_id).distinct()}
    try:
        letters, last_uid, remaining = fetch_letters(settings, settings.last_uid or 0, known, batch)
    except MailError as exc:
        settings.last_poll_error = str(exc)
        settings.last_poll_result = "ошибка связи с почтой"
        db.commit()
        raise
    settings.last_poll_error = None
    total = ImportResult()
    for letter in letters:
        for attachment in letter.attachments:
            total.add(import_attachment(
                db, attachment.content, filename=attachment.filename, message_id=letter.message_id,
                uid=letter.uid, sender=letter.sender, subject=letter.subject, received_at=letter.received_at,
            ))
    settings = get_settings(db)
    settings.last_uid = max(settings.last_uid or 0, last_uid)
    summary = _summary(len(letters), total, remaining)
    if total.errors:
        settings.last_poll_error = "; ".join(total.errors)[:900]
    settings.last_poll_result = summary
    settings.last_poll_at = sheet_sync.local_now()
    db.commit()
    return summary, remaining, total


def check_mail() -> Dict:
    """Кнопка «Проверить почту» и опрос по расписанию. Занято — ждём идущую проверку."""
    with _lock:
        db = database.SessionLocal()
        try:
            summary, remaining, result = _check_locked(db)
            return {"ok": True, "summary": summary, "remaining": remaining,
                    "created": len(result.created), "revised": len(result.revised), "errors": result.errors}
        except MailError as exc:
            return {"ok": False, "summary": str(exc), "remaining": 0, "created": 0, "revised": 0, "errors": [str(exc)]}
        finally:
            db.close()


def _history_worker() -> None:
    """Вычитка ящика с начала: курсор в ноль, дальше порциями. Уже разобранное
    отсеивается по журналу на заголовках, поэтому повторный запуск почти бесплатен."""
    try:
        with _lock:
            db = database.SessionLocal()
            try:
                settings = get_settings(db)
                saved_uid = settings.last_uid or 0
                settings.last_uid = 0
                db.commit()
                for _ in range(HISTORY_MAX_BATCHES):
                    try:
                        _, remaining, _ = _check_locked(db)
                    except MailError:
                        break
                    _history["done"] += 1
                    _history["remaining"] = remaining
                    if not remaining:
                        break
                # Курсор не должен откатиться назад, если вычитка оборвалась
                settings = get_settings(db)
                settings.last_uid = max(settings.last_uid or 0, saved_uid)
                db.commit()
            finally:
                db.close()
    except Exception as exc:
        logger.error(f"Ошибка загрузки истории почты: {exc}", exc_info=True)
    finally:
        _history["running"] = False


def start_history() -> bool:
    if _history["running"]:
        return False
    _history.update(running=True, done=0, remaining=0)
    threading.Thread(target=_history_worker, name="lavka-mail-history", daemon=True).start()
    return True


def get_status(db) -> Dict:
    settings = get_settings(db)
    weekdays = sheet_sync._weekdays(settings)
    password_set = secret_box.decrypt(settings.password_enc) is not None
    return {
        "configured": bool(settings.username and password_set),
        "last_poll_at": settings.last_poll_at.isoformat(timespec="seconds") if settings.last_poll_at else None,
        "last_poll_result": settings.last_poll_result,
        "last_poll_error": settings.last_poll_error,
        "running": _lock.locked(),
        "history": dict(_history),
        "senders": senders_of(settings),
        "auto": {
            "enabled": bool(settings.enabled),
            "interval_minutes": settings.interval_minutes or 30,
            "start_time": settings.start_time or "08:00",
            "end_time": settings.end_time or "12:30",
            "weekdays": weekdays,
            "summary": f"{sheet_sync._weekdays_text(weekdays)}, {sheet_sync._interval_text(settings.interval_minutes or 30)}, "
                       f"{settings.start_time or '08:00'}–{settings.end_time or '12:30'}",
        },
    }


# --- Расписание ---

def _should_poll_now() -> bool:
    db = database.SessionLocal()
    try:
        settings = db.query(database.MailSettings).first()
        if settings is None or not settings.username or not settings.password_enc:
            return False
        if settings.enabled is None or settings.interval_minutes is None:
            return False
        return sheet_sync.is_due(settings, settings.last_poll_at, sheet_sync.local_now())
    finally:
        db.close()


def _scheduler_loop() -> None:
    while True:
        try:
            if not _history["running"] and _should_poll_now():
                check_mail()
        except Exception as exc:
            logger.error(f"Ошибка опроса почты Лавки: {exc}", exc_info=True)
        time_module.sleep(sheet_sync.CHECK_EVERY_SECONDS)


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, name="lavka-mail", daemon=True).start()
