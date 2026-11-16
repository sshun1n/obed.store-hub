"""Самокат в кабинете СБИС: черновики реализаций с УПД по заказам ORDERS.

Порядок из справки Saby для Самоката: заказ → «Утвердить» → «Отгрузить»
(реализация из заказа) → номер из своей учётной системы → «Отправить» с подписью.
Здесь только создание черновика: реализация (ДокОтгрИсх) на основании заказа,
номер — из реестра 1С по адресу точки, вложение — УПД, собранный samokat_upd.
Ничего не отправляется: отправка с подписью Рутокеном — отдельный шаг, позже.

Правила те же, что у Лавки (saby_actions):
* документы создаются только по кнопке админа;
* если у заказа уже есть реализация — точку пропускаем, ничего не правим;
* незавершённую реализацию не открываем (saby_client.read_card);
* «проверка» делает всё то же самое, кроме записи в СБИС, и отдаёт файлы УПД.
"""
from __future__ import annotations

import base64
import logging
import threading
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from src import database, samokat_parser, samokat_upd, saby_client as sc, saby_sync, sheet_sync, upd_registry
from src.api_lavka import short_address

logger = logging.getLogger("SamokatSaby")

SAMOKAT_INN = "7811657720"
# Договор поставки — обязателен в УПД (поля номер_договора, дата_договора и основание передачи)
CONTRACT = samokat_upd.Contract("УР-ДД-ЗАК-ФУД-2025/145", "01.04.2025")
ORDER_WINDOW_DAYS = 7     # заказ приходит за 1–3 дня до поставки; берём с запасом
TEMPLATE_WINDOW_DAYS = 30

_progress: Dict = {"running": False, "label": "", "step": "", "done": 0, "total": 0, "error": None,
                   "result": None, "finished_at": None, "day": None}
_previews: Dict[str, Tuple[str, bytes]] = {}   # номер заказа → (имя файла, УПД) последней проверки


class SamokatError(sc.SabyError):
    pass


def progress() -> Dict:
    return {k: v for k, v in _progress.items()}


def preview(order_no: str) -> Optional[Tuple[str, bytes]]:
    return _previews.get(order_no)


def _step(text: str, done: Optional[int] = None, total: Optional[int] = None) -> None:
    _progress["step"] = text
    if done is not None:
        _progress["done"] = done
    if total is not None:
        _progress["total"] = total


def _inn(item: dict) -> str:
    return (((item.get("Контрагент") or {}).get("СвЮЛ")) or {}).get("ИНН") or ""


def _template(sid: str, day: date) -> str:
    """Настоящий УПД нашего ИП, сделанный СБИС, — из завершённой реализации (их открывать можно)."""
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, start=day - timedelta(days=TEMPLATE_WINDOW_DAYS),
                                  end=day, pages=12):
        state = sc.state(item)
        if state not in sc.FINISHED or "УпдСчфДоп" not in sc.attachments(item):
            continue
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.SHIPMENT_TYPE, state=state)
        for attachment in card.get("Вложение") or []:
            link = (attachment.get("Файл") or {}).get("Ссылка")
            if attachment.get("Тип") == "УпдСчфДоп" and link:
                return sc.download(sid, link)
    raise SamokatError("За месяц нет ни одной завершённой реализации с УПД — не с чего взять образец")


def _box(sid: str, party: dict) -> str:
    info = sc._post(sc.DOCS_URL, "СБИС.ИнформацияОКонтрагенте", {"Участник": party}, sid) or {}
    return info.get("Идентификатор") or ""


def item_codes(sid: str, card: dict) -> Dict[str, str]:
    """Код покупателя → код нашей карточки каталога, как строки привязаны в заказе сейчас.

    Берётся из учётного вложения заказа (ВложениеУчета): там СБИС держит заказ уже
    с нашей номенклатурой, и после сопоставления или объединения карточек код в нём
    свежий. Файл ответа на заказ для этого не годится: он отправлен и не меняется.
    """
    codes: Dict[str, str] = {}
    for attachment in card.get("ВложениеУчета") or []:
        link = (attachment.get("Файл") or {}).get("Ссылка")
        if not link or attachment.get("Тип") != "Заказ":
            continue
        root = samokat_upd.ET.fromstring(sc.download(sid, link).encode("windows-1251"))
        for row in root.iter("СтрТабл"):
            if row.get("КодПокупателя") and row.get("КодПоставщика"):
                codes[row.get("КодПокупателя")] = row.get("КодПоставщика")
    return codes


def _samokat_orders(sid: str, day: date) -> List[Tuple[dict, dict, str]]:
    """Заказы Самоката в СБИС с поставкой на этот день: (строка списка, карточка, XML заказа)."""
    found = []
    items = [i for i in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, start=day - timedelta(days=ORDER_WINDOW_DAYS),
                                          end=day, pages=40) if _inn(i) == SAMOKAT_INN]
    for i, item in enumerate(items, start=1):
        _step(f"Читаю заказы Самоката в СБИС: {i} из {len(items)}", i, len(items))
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.ORDER_TYPE)
        xml = sc.xml_attachment(sid, card, "Заказ")
        if not xml:
            continue
        doc = samokat_upd.ET.fromstring(xml.encode("windows-1251")).find("Документ")
        if doc is None or doc.get("Срок") != day.strftime("%d.%m.%Y"):
            continue
        card["Идентификатор"] = item["Идентификатор"]
        found.append((item, card, xml))
    return found


def _point_of(xml: str) -> Tuple[str, str, str]:
    """Адрес, GLN и КПП точки из заказа."""
    doc = samokat_upd.ET.fromstring(xml.encode("windows-1251")).find("Документ")
    point = doc.find("Грузополучатель") if doc is not None else None
    if point is None:
        return "", "", ""
    address = point.find("Адрес")
    ul = point.find("СвЮЛ")
    return ((address.get("АдрТекст") if address is not None else "") or "", point.get("GLN") or "",
            (ul.get("КПП") if ul is not None else "") or "")


def _link_order(db, day: date, address: str, gln: str, order_no: str, saby_id: str) -> Optional[database.SamokatOrder]:
    """Заказ из почты для этой точки: номер УР- и GLN запоминаем, чтобы видеть на странице."""
    key = samokat_parser.point_key(address)
    point = db.query(database.SamokatPoint).filter(database.SamokatPoint.key == key).first()
    if point is None:
        for candidate in db.query(database.SamokatPoint):
            if upd_registry.same_address(candidate.address, address):
                point = candidate
                break
    if point is None:
        return None
    if gln and point.gln != gln:
        point.gln = gln
    order = (db.query(database.SamokatOrder)
             .filter(database.SamokatOrder.delivery_date == day, database.SamokatOrder.point_id == point.id).first())
    if order is not None:
        order.order_no = order_no
        order.saby_order_id = saby_id
    return order


def _registry_row(db, day: date, address: str, used: set) -> Optional[database.UpdRow]:
    for row in db.query(database.UpdRow).filter(database.UpdRow.day == day).order_by(database.UpdRow.number):
        if row.id not in used and upd_registry.same_address(address, row.address or ""):
            return row
    return None


def create_drafts(day: date, user: str, dry_run: bool, only: Optional[List[str]] = None) -> Dict:
    """Черновики реализаций с УПД по заказам Самоката на день. dry_run — всё, кроме записи в СБИС."""
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "skipped": [], "notes": [], "dry_run": dry_run}
    try:
        _step("Вход в СБИС")
        sid = sc.login()
        orders = _samokat_orders(sid, day)
        if only:
            orders = [o for o in orders if str(o[0].get("Номер") or "").strip() in set(only)]
        if not orders:
            raise SamokatError(f"В СБИС нет заказов Самоката с поставкой {day.strftime('%d.%m.%Y')}")
        _step("Образец УПД и ящики ЭДО")
        template = _template(sid, day)
        receiver = _box(sid, {"СвЮЛ": {"ИНН": SAMOKAT_INN, "КПП": "997750001"}})
        sender = _box(sid, {"СвФЛ": {"ИНН": (((orders[0][1].get("НашаОрганизация") or {}).get("СвФЛ")) or {}).get("ИНН", "")}})
        if not receiver or not sender:
            raise SamokatError("СБИС не вернул ящик ЭДО Самоката или наш")
        used: set = set()
        if dry_run:
            _previews.clear()
        for i, (item, card, xml) in enumerate(orders, start=1):
            order_no = str(item.get("Номер") or "").strip()
            address, gln, _ = _point_of(xml)
            label = f"{order_no} · {short_address(address)}"
            _step(f"{'Проверяю' if dry_run else 'Создаю'} реализацию {i} из {len(orders)}", i, len(orders))
            try:
                ours = _link_order(db, day, address, gln, order_no, item["Идентификатор"])
                if ours is None:
                    result["notes"].append(f"{label}: в заказах из почты такой точки нет — проверьте письмо")
                if sc.consequences(card, sc.SHIPMENT_TYPE):
                    result["skipped"].append(label)        # реализация уже есть — ничего не трогаем
                    continue
                if ours is not None and ours.shipment_number:
                    # У заказа в СБИС реализации нет, а у нас записана — её удалили в кабинете
                    result["notes"].append(f"{label}: реализации № {ours.shipment_number} в СБИС больше нет — удалена")
                    ours.shipment_id = ours.shipment_number = ours.shipment_state = ours.shipment_created_by = None
                row = _registry_row(db, day, address, used)
                if row is None:
                    raise SamokatError("нет строки в реестре 1С по этому адресу — загрузите реестр за день")
                used.add(row.id)
                qty, gross = samokat_upd.totals(xml)
                if row.amount is not None and abs(row.amount - gross) > 1:
                    result["notes"].append(f"{label}: сумма в 1С {row.amount}, в заказе {gross}")
                name, content = samokat_upd.build(xml, template, number=row.number, day=day, contract=CONTRACT,
                                                  receiver_box=receiver, sender_box=sender,
                                                  codes=item_codes(sid, card))
                if dry_run:
                    _previews[order_no] = (name, content)
                    result["done"].append(label)
                    continue
                contractor = dict(card.get("Контрагент") or {})
                contractor["Идентификатор"] = receiver
                doc = sc.write_document(sid, sc.DOCS_URL, {
                    "Тип": sc.SHIPMENT_TYPE, "Номер": row.number, "Дата": day.strftime("%d.%m.%Y"),
                    "Примечание": address,
                    # Срок поставки из заказа: без него в карточке реализации поле пустое
                    "Срок": day.strftime("%d.%m.%Y"),
                    "НашаОрганизация": card.get("НашаОрганизация") or {},
                    "Контрагент": contractor,
                    "ДокументОснование": [{"ВидСвязи": "Обычная связь", "Документ": {"Идентификатор": item["Идентификатор"]}}],
                    "Вложение": [{"Файл": {"Имя": f"{name}.xml", "ДвоичныеДанные": base64.b64encode(content).decode()}}],
                })
                if ours is not None:
                    ours.shipment_id = doc.get("Идентификатор")
                    ours.shipment_number = row.number
                    ours.shipment_state = sc.state(doc) or "Черновик"
                    ours.shipment_created_by = user
                result["done"].append(label)
                db.commit()
            except (sc.SabyError, samokat_upd.UpdError) as exc:
                result["errors"][label] = str(exc)
        db.commit()
        return result
    finally:
        db.close()


def _worker(day: date, user: str, dry_run: bool, only: Optional[List[str]]) -> None:
    try:
        # Одна операция с кабинетом за раз — общий замок со страницей «СБИС»
        with saby_sync._lock:
            _progress["result"] = create_drafts(day, user, dry_run, only)
        _progress["error"] = None
    except sc.SabyError as exc:
        _progress["error"] = str(exc)
    except Exception as exc:
        logger.error(f"Ошибка черновиков Самоката за {day}: {exc}", exc_info=True)
        _progress["error"] = f"Сбой: {exc}"
    finally:
        _progress["running"] = False
        _progress["finished_at"] = sheet_sync.local_now().isoformat(timespec="seconds")


def start(day: date, user: str, dry_run: bool, only: Optional[List[str]] = None) -> bool:
    if _progress["running"] or saby_sync._progress.get("running"):
        return False
    _progress.update(running=True, day=day.isoformat(), label="Проверка УПД" if dry_run else "Черновики реализаций",
                     step="Начинаю", done=0, total=0, error=None, result=None)
    threading.Thread(target=_worker, args=(day, user, dry_run, only), name="samokat-saby", daemon=True).start()
    return True
