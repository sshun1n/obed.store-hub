"""Что по нашему дню поставки уже есть в СБИС. Только чтение.

Кабинет общий с Ателье (одно ИП, одна Лавка, общие адреса и нумерация), поэтому
свои документы отбираются только по связи с нашими заказами ЯЛ:

* заказ Лавки (ЗаказВх) — по номеру ЯЛ, в списке за 14 дней до поставки;
* реализация — по ссылке из карточки нашего заказа (ДокументСледствие). Саму
  реализацию не открываем, номер и состояние берём из списка за день;
* ЭТрН — по номеру ЯЛ в поле ORDERS титула; без номера — по грузу «Готовые обеды»;
* заказ на перевозку — по грузу в заявке, у Ателье «Кондитерские изделия».
  Его ЭТрН перечислены в карточке, поэтому накладные чужого заказа не открываем.

Документ, по которому работа закончена, второй раз не открывается: он в кэше.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, Optional, Set

from src import database, saby_client as sc, sheet_sync

logger = logging.getLogger("SabySync")

# Груз Обеда в заявке и ЭТрН. На этапе 6 переедет в настройки перевозки
OBED_CARGO = "готовые обеды"
ORDER_LOOKBACK_DAYS = 14   # заказ в СБИС датирован днём, когда Лавка его прислала

_lock = threading.Lock()
_progress: Dict = {"running": False, "day": None, "label": "", "step": "", "done": 0, "total": 0,
                   "error": None, "result": None, "finished_at": None}


def progress() -> Dict:
    return dict(_progress)


def _step(text: str, done: Optional[int] = None, total: Optional[int] = None) -> None:
    _progress["step"] = text
    if done is not None:
        _progress["done"] = done
    if total is not None:
        _progress["total"] = total


def _date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(str(value or ""), "%d.%m.%Y").date()
    except ValueError:
        return None


def _money(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value).replace(",", ".")) if str(value or "").strip() else None
    except InvalidOperation:
        return None


def _finished(doc: Optional[database.SabyDoc]) -> bool:
    return bool(doc and doc.state in sc.FINISHED)


class _Store:
    """Кэш saby_docs одного обновления: всё нужное читается одним запросом."""

    def __init__(self, db, day: date):
        self.db = db
        self.day = day
        self.rows = {d.saby_id: d for d in db.query(database.SabyDoc).filter(database.SabyDoc.delivery_date == day)}

    def get(self, saby_id: str) -> Optional[database.SabyDoc]:
        return self.rows.get(saby_id)

    def put(self, saby_id: str, kind: str, item: Optional[dict] = None, **fields) -> database.SabyDoc:
        row = self.rows.get(saby_id) or self.db.query(database.SabyDoc).filter_by(saby_id=saby_id).first()
        if row is None:
            row = database.SabyDoc(saby_id=saby_id, kind=kind, delivery_date=self.day)
            self.db.add(row)
        self.rows[saby_id] = row
        row.kind, row.delivery_date = kind, self.day
        if item is not None:
            row.number = str(item.get("Номер") or "").strip() or row.number
            row.doc_date = _date(item.get("Дата")) or row.doc_date
            row.state = sc.state(item) or row.state
            row.attachments = ",".join(sc.attachments(item))[:300]
            row.link = item.get("СсылкаДляНашаОрганизация") or row.link
            if item.get("Удален") == "Да":
                row.state = "Удалён"
            amount = _money(item.get("Сумма"))
            if amount is not None:
                row.amount = amount
            if item.get("Примечание") and kind in ("order", "shipment"):
                row.address = str(item["Примечание"])[:400]
        for key, value in fields.items():
            setattr(row, key, value)
        row.checked_at = datetime.utcnow()
        return row


def _orders(sid: str, store: _Store, ours: Set[str]) -> Dict[str, dict]:
    """Наши заказы Лавки в СБИС и ссылки из них на реализации."""
    _step("Ищу заказы Лавки")
    found: Dict[str, dict] = {}
    start = store.day - timedelta(days=ORDER_LOOKBACK_DAYS)
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, start=start, end=store.day):
        number = str(item.get("Номер") or "").strip()
        if number in ours:
            found[number] = item
    # Кого не нашли по датам — ищем маской: заказ мог прийти раньше окна
    for number in sorted(ours - set(found)):
        for item in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, mask=number, pages=1):
            if str(item.get("Номер") or "").strip() == number:
                found[number] = item
                break

    links: Dict[str, dict] = {}
    total = len(found)
    for i, (number, item) in enumerate(sorted(found.items()), start=1):
        _step(f"Читаю заказы Лавки: {i} из {total}", i, total)
        cached = store.get(item["Идентификатор"])
        known = [d for d in store.rows.values() if d.kind == "shipment" and d.parent_id == item["Идентификатор"]]
        if _finished(cached) and known:
            links[number] = {"id": known[0].saby_id, "number": known[0].number, "date": ""}
            store.put(item["Идентификатор"], "order", item, order_no=number)
            continue
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.ORDER_TYPE)
        store.put(item["Идентификатор"], "order", item, order_no=number, read_at=datetime.utcnow())
        shipments = sc.consequences(card, sc.SHIPMENT_TYPE)
        if shipments:
            links[number] = shipments[-1]
    return links


def _shipments(sid: str, store: _Store, links: Dict[str, dict]) -> None:
    """Реализации по ссылкам из заказов — только из списка, без открытия."""
    _step("Смотрю реализации в списке за день")
    by_id = {}
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, start=store.day, end=store.day):
        by_id[item["Идентификатор"]] = item
    for order_no, link in links.items():
        item = by_id.get(link["id"])
        if item is None and link.get("number"):
            # Реализация датирована не днём поставки — ищем маской по номеру, тоже списком
            for candidate in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, mask=link["number"], pages=1):
                if candidate["Идентификатор"] == link["id"]:
                    item = candidate
                    break
        parent = next((d.saby_id for d in store.rows.values() if d.kind == "order" and d.order_no == order_no), None)
        store.put(link["id"], "shipment", item, order_no=order_no, parent_id=parent,
                  number=(item or {}).get("Номер") or link.get("number"))


def _transport(sid: str, store: _Store, ours: Set[str]) -> None:
    """Заказы на перевозку дня и их ЭТрН. Свои — по грузу, накладные — по ORDERS."""
    _step("Смотрю заказы на перевозку")
    transports = list(sc.list_documents(sid, sc.TMS_URL, sc.TRANSPORT_TYPE, start=store.day, end=store.day))
    foreign_waybills: Set[str] = set()
    our_waybills: Dict[str, str] = {}
    # Карточки заказов на перевозку читаем каждый раз: их два в день, а только из
    # них видно, какие ЭТрН чужие и открывать их не нужно
    for item in transports:
        card = sc.read_card(sid, sc.TMS_URL, item["Идентификатор"], sc.TRANSPORT_TYPE)
        xml = sc.attachment_xml(sid, card, "ЗаказЗаявка")
        cargo = (sc.CARGO_RE.search(xml).group(1) if sc.CARGO_RE.search(xml) else "")
        mine = cargo.strip().lower() == OBED_CARGO
        store.put(item["Идентификатор"], "transport", item, cargo=cargo[:150] or None, ours=mine,
                  party=sc.party_name(card)[:300] or None, read_at=datetime.utcnow())
        for child in sc.consequences(card, sc.WAYBILL_TYPE):
            if mine:
                our_waybills[child["id"]] = item["Идентификатор"]
            else:
                foreign_waybills.add(child["id"])

    waybills = list(sc.list_documents(sid, sc.TMS_URL, sc.WAYBILL_TYPE, start=store.day, end=store.day))
    todo = [w for w in waybills if w["Идентификатор"] not in foreign_waybills]
    for i, item in enumerate(todo, start=1):
        _step(f"Читаю ЭТрН: {i} из {len(todo)}", i, len(todo))
        cached = store.get(item["Идентификатор"])
        parent = our_waybills.get(item["Идентификатор"])
        if cached and cached.read_at and (cached.order_no or cached.cargo):
            store.put(item["Идентификатор"], "waybill", item, parent_id=parent or cached.parent_id,
                      ours=(cached.order_no or "") in ours)
            continue
        card = sc.read_card(sid, sc.TMS_URL, item["Идентификатор"], sc.WAYBILL_TYPE)
        facts = sc.waybill_facts(sc.attachment_xml(sid, card, "ЭТрН"))
        # Своя — только по номеру ЯЛ: груз «Готовые обеды» есть и у накладных Самоката
        mine = facts["order_no"] in ours
        store.put(item["Идентификатор"], "waybill", item, order_no=facts["order_no"] or None,
                  cargo=facts["cargo"][:150] or None, address=facts["address"][:400] or None,
                  parent_id=parent, ours=mine, read_at=datetime.utcnow())


def refresh_day(day: date) -> None:
    db = database.SessionLocal()
    try:
        ours = {o.order_no for o in db.query(database.LavkaOrder).filter(database.LavkaOrder.delivery_date == day)}
        if not ours:
            raise sc.SabyError("На этот день наших заказов нет")
        store = _Store(db, day)
        _step("Вход в СБИС")
        sid = sc.login()
        links = _orders(sid, store, ours)
        db.commit()
        _shipments(sid, store, links)
        db.commit()
        _transport(sid, store, ours)
        db.commit()
    finally:
        db.close()


def _worker(day: date, label: str, action: Optional[Callable[[], Dict]]) -> None:
    """Одна операция с кабинетом: действие (если есть), затем перечитать день.

    Перечитываем всегда, даже после ошибки: часть документов могла успеть
    создаться, и страница должна показать кабинет как есть, а не как задумано.
    """
    try:
        with _lock:
            try:
                if action is not None:
                    # Перед записью — свежий кабинет: точку могли провести руками минуту назад
                    _step("Сверяю день с кабинетом")
                    refresh_day(day)
                    _progress["result"] = action() or {}
            finally:
                _step("Перечитываю день")
                refresh_day(day)
        _progress["error"] = None
    except sc.SabyError as exc:
        _progress["error"] = str(exc)
    except Exception as exc:
        logger.error(f"Ошибка операции СБИС «{label}» за {day}: {exc}", exc_info=True)
        _progress["error"] = f"Сбой: {exc}"
    finally:
        _progress["running"] = False
        _progress["finished_at"] = sheet_sync.local_now().isoformat(timespec="seconds")


def start(day: date, label: str = "Чтение СБИС", action: Optional[Callable[[], Dict]] = None) -> bool:
    """Запустить операцию в фоне. Занято — False: одна операция с кабинетом за раз."""
    if _progress["running"]:
        return False
    _progress.update(running=True, day=day.isoformat(), label=label, step="Начинаю", done=0, total=0,
                     error=None, result=None)
    threading.Thread(target=_worker, args=(day, label, action), name="saby-task", daemon=True).start()
    return True


def step(text: str, done: Optional[int] = None, total: Optional[int] = None) -> None:
    _step(text, done, total)


# --- Автообновление по расписанию ---

AUTO_BACK_DAYS = 2    # вчерашние и позавчерашние поставки: Лавка принимает товар, приходят акты
AUTO_AHEAD_DAYS = 3   # ближайшие поставки: по ним сейчас и идёт работа с документами


def auto_days(db) -> list:
    """Дни поставки с нашими заказами вокруг сегодняшнего — их и обновляем."""
    today = sheet_sync.local_now().date()
    rows = (db.query(database.LavkaOrder.delivery_date)
            .filter(database.LavkaOrder.delivery_date.between(today - timedelta(days=AUTO_BACK_DAYS),
                                                              today + timedelta(days=AUTO_AHEAD_DAYS)))
            .distinct().all())
    return sorted(d for (d,) in rows)


def _auto_worker(days: list) -> None:
    done, failed = [], {}
    try:
        with _lock:
            for i, day in enumerate(days, start=1):
                _progress["day"] = day.isoformat()
                _step(f"Обновляю {day.strftime('%d.%m')} ({i} из {len(days)})", i, len(days))
                try:
                    refresh_day(day)
                    done.append(day.strftime("%d.%m"))
                except sc.SabyError as exc:
                    failed[day.strftime("%d.%m")] = str(exc)
                except Exception as exc:
                    logger.error(f"Автообновление СБИС за {day}: {exc}", exc_info=True)
                    failed[day.strftime("%d.%m")] = f"сбой: {exc}"
        _progress["error"] = "; ".join(f"{k}: {v}" for k, v in failed.items()) or None
    finally:
        text = f"обновлено: {', '.join(done) or 'ничего'}" + (f"; ошибки: {len(failed)}" if failed else "")
        db = database.SessionLocal()
        try:
            row = db.query(database.SabySettings).first()
            if row is not None:
                row.sync_last_result = text[:300]
                db.commit()
        finally:
            db.close()
        _progress["running"] = False
        _progress["finished_at"] = sheet_sync.local_now().isoformat(timespec="seconds")


def start_auto(days: list) -> bool:
    if not days or _progress["running"]:
        return False
    _progress.update(running=True, day=days[0].isoformat(), label="Автообновление", step="Начинаю",
                     done=0, total=len(days), error=None, result=None)
    threading.Thread(target=_auto_worker, args=(days,), name="saby-auto", daemon=True).start()
    return True
