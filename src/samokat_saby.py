"""Самокат в кабинете СБИС: чтение дня и черновики реализаций с УПД по заказам ORDERS.

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

Скорость: день поставки заказа есть прямо в списке (Расширение.СрокПоставки),
поэтому карточки открываются только у заказов нужного дня, и в несколько потоков
(saby_client.pmap). Образец УПД и ящики ЭДО помнятся между операциями.
Документы дня — в saby_docs с видами sm_order и sm_shipment.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from src import database, samokat_parser, samokat_upd, saby_actions, saby_client as sc, saby_sync, sheet_sync, upd_registry
from src.api_lavka import short_address

logger = logging.getLogger("SamokatSaby")

SAMOKAT_INN = "7811657720"
SAMOKAT_KPP = "997750001"
# Договор поставки — обязателен в УПД (поля номер_договора, дата_договора и основание передачи)
CONTRACT = samokat_upd.Contract("УР-ДД-ЗАК-ФУД-2025/145", "01.04.2025")
ORDER_WINDOW_DAYS = 7     # заказ приходит за 1–3 дня до поставки; берём с запасом
TEMPLATE_WINDOW_DAYS = 30
TEMPLATE_TTL = 6 * 3600   # образец УПД меняется редко: держим шесть часов

ORDER_KIND, SHIPMENT_KIND = "sm_order", "sm_shipment"

_progress: Dict = {"running": False, "label": "", "step": "", "done": 0, "total": 0, "error": None,
                   "result": None, "finished_at": None, "day": None, "kind": "", "rows": {}}
_previews: Dict[str, Tuple[str, bytes]] = {}   # номер заказа → (имя файла, УПД) последней проверки
_cache: Dict = {"template": None, "boxes": {}}


class SamokatError(sc.SabyError):
    pass


def progress() -> Dict:
    out = dict(_progress)
    out["rows"] = dict(_progress.get("rows") or {})
    return out


def preview(order_no: str) -> Optional[Tuple[str, bytes]]:
    return _previews.get(order_no)


def _step(text: str, done: Optional[int] = None, total: Optional[int] = None) -> None:
    _progress["step"] = text
    if done is not None:
        _progress["done"] = done
    if total is not None:
        _progress["total"] = total


def _row(order_no: str, state: str, text: str = "") -> None:
    saby_sync.row(_progress, order_no, state, text)


def _inn(item: dict) -> str:
    return (((item.get("Контрагент") or {}).get("СвЮЛ")) or {}).get("ИНН") or ""


def _number(item: dict) -> str:
    return str(item.get("Номер") or "").strip()


def _template(sid: str, day: date) -> str:
    """Настоящий УПД нашего ИП, сделанный СБИС, — из завершённой реализации (их открывать можно)."""
    cached = _cache["template"]
    if cached and time.time() - cached[0] < TEMPLATE_TTL:
        return cached[1]
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, start=day - timedelta(days=TEMPLATE_WINDOW_DAYS),
                                  end=day, pages=12):
        state = sc.state(item)
        if state not in sc.FINISHED or "УпдСчфДоп" not in sc.attachments(item):
            continue
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.SHIPMENT_TYPE, state=state)
        for attachment in card.get("Вложение") or []:
            link = (attachment.get("Файл") or {}).get("Ссылка")
            if attachment.get("Тип") == "УпдСчфДоп" and link:
                text = sc.download(sid, link)
                _cache["template"] = (time.time(), text)
                return text
    raise SamokatError("За месяц нет ни одной завершённой реализации с УПД — не с чего взять образец")


def _box(sid: str, party: dict) -> str:
    """Ящик ЭДО участника. Ящики не меняются — спрашиваем СБИС один раз за жизнь процесса."""
    key = repr(party)
    if key not in _cache["boxes"]:
        info = sc._post(sc.DOCS_URL, "СБИС.ИнформацияОКонтрагенте", {"Участник": party}, sid) or {}
        if not info.get("Идентификатор"):
            return ""
        _cache["boxes"][key] = info["Идентификатор"]
    return _cache["boxes"][key]


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


def _delivery(item: dict) -> str:
    """День поставки из строки списка: «18.11.2026 00.00.00» → «18.11.2026». Пусто — не указан."""
    return str(((item.get("Расширение") or {}).get("СрокПоставки")) or "").split(" ")[0]


def _day_items(sid: str, day: date) -> List[dict]:
    """Заказы Самоката в списке СБИС, у которых поставка в этот день. Карточки не открываем."""
    stamp = day.strftime("%d.%m.%Y")
    items = [i for i in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, start=day - timedelta(days=ORDER_WINDOW_DAYS),
                                          end=day, pages=40) if _inn(i) == SAMOKAT_INN and i.get("Удален") != "Да"]
    # Срок в списке есть у заказов EDI; без него — проверим по файлу заказа
    return [i for i in items if _delivery(i) in (stamp, "")]


def _xml_day(xml: str) -> str:
    doc = samokat_upd.ET.fromstring(xml.encode("windows-1251")).find("Документ")
    return (doc.get("Срок") if doc is not None else "") or ""


def _read_orders(sid: str, items: List[dict], day: date, step: Callable, need_xml: Callable[[dict], bool]
                 ) -> List[Tuple[dict, dict, str]]:
    """Карточки (и, где нужно, файлы) заказов — в несколько потоков: (строка списка, карточка, XML)."""
    def read(item):
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.ORDER_TYPE)
        card["Идентификатор"] = item["Идентификатор"]
        xml = sc.xml_attachment(sid, card, "Заказ") if (need_xml(item) or not _delivery(item)) else ""
        return item, card, xml

    stamp = day.strftime("%d.%m.%Y")
    out = []
    for got in sc.pmap(read, items, lambda i, n: step(f"Читаю заказы Самоката: {i} из {n}", i, n)):
        if isinstance(got, Exception):
            raise got
        item, card, xml = got
        if not _delivery(item) and (not xml or _xml_day(xml) != stamp):
            continue
        out.append(got)
    return out


def _time(xml: str) -> str:
    """Время поставки из заказа в виде СБИС: «00:00» → «00.00.00»."""
    doc = samokat_upd.ET.fromstring(xml.encode("windows-1251")).find("Документ")
    parts = ((doc.get("СрокВремя") if doc is not None else "") or "00:00").split(":")
    parts = (parts + ["00", "00"])[:3]
    return ".".join(p.zfill(2) for p in parts)


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
    if address and point.order_address != address:
        point.order_address = address[:400]
    order = (db.query(database.SamokatOrder)
             .filter(database.SamokatOrder.delivery_date == day, database.SamokatOrder.point_id == point.id).first())
    if order is not None:
        order.order_no = order_no
        order.saby_order_id = saby_id
    return order


def _linked(db, day: date) -> Dict[str, database.SamokatOrder]:
    return {o.order_no: o for o in db.query(database.SamokatOrder)
            .filter(database.SamokatOrder.delivery_date == day, database.SamokatOrder.order_no.isnot(None))}


def _registry_row(db, day: date, address: str, used: set) -> Optional[database.UpdRow]:
    for row in upd_registry.rows_for(db, day, SAMOKAT_INN):
        if row.id not in used and upd_registry.same_address(address, row.address or ""):
            return row
    return None


# --- Чтение кабинета за день ---

def refresh_day(day: date, step: Callable = _step, serial: Optional[int] = None) -> Dict:
    """Что по заказам Самоката на этот день есть в кабинете: заказ, ответ на него, реализация.

    Только чтение. Реализации не открываются — номер и состояние из списка за день.
    serial — у фонового чтения (saby_sync.commit): тогда пустой список заказов ничего
    у нас не удаляет — пустой ответ слишком похож на сбой. Кнопка удаляет, как и раньше.
    """
    background = serial is not None
    db = database.SessionLocal()
    try:
        step("Вход в СБИС")
        sid = sc.login()
        step("Список заказов Самоката")
        items = _day_items(sid, day)
        linked = _linked(db, day)
        # Файл заказа нужен, чтобы впервые связать заказ с точкой из письма и запомнить GLN и адрес из ORDERS
        orders = _read_orders(sid, items, day, step, lambda item: _number(item) not in linked or not (
            linked[_number(item)].point and linked[_number(item)].point.order_address and linked[_number(item)].point.gln))
        store = saby_sync._Store(db, day)
        links: Dict[str, dict] = {}
        for item, card, xml in orders:
            number = _number(item)
            ours = linked.get(number)
            if xml:
                address, gln, _ = _point_of(xml)
                ours = _link_order(db, day, address, gln, number, item["Идентификатор"]) or ours
                if ours is not None:
                    linked[number] = ours
            store.put(item["Идентификатор"], ORDER_KIND, item, order_no=number, ours=ours is not None,
                      address=str(item.get("Примечание") or "")[:400] or None, read_at=datetime.utcnow())
            found = sc.consequences(card, sc.SHIPMENT_TYPE)
            if found:
                links[number] = {**found[-1], "parent": item["Идентификатор"]}
            elif ours is not None and ours.shipment_number:
                # У заказа в СБИС реализации нет, а у нас записана — её удалили в кабинете
                ours.shipment_id = ours.shipment_number = ours.shipment_state = ours.shipment_created_by = None
        if orders or not background:
            # Заказы этого дня, которых в кабинете больше нет
            seen = {_number(i) for i, _, _ in orders}
            for doc in list(store.rows.values()):
                if doc.kind == ORDER_KIND and doc.order_no not in seen:
                    doc.state = "Удалён"
            # Реализации, которых у заказов больше нет (удалены в кабинете)
            alive = {link["id"] for link in links.values()}
            for doc in list(store.rows.values()):
                if doc.kind == SHIPMENT_KIND and doc.saby_id not in alive:
                    doc.state = "Удалён"

        # Наши заказ на перевозку и ЭТрН — состояния из списков TMS
        from src import samokat_transport
        step("Смотрю перевозку и ЭТрН")
        samokat_transport.refresh(sid, db, day, store, background=background)

        step("Смотрю реализации в списке за день")
        by_id = {i["Идентификатор"]: i for i in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE,
                                                                   start=day, end=day)}
        for number, link in links.items():
            item = by_id.get(link["id"])
            if item is None and link.get("number"):
                item = next((c for c in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, mask=link["number"], pages=1)
                             if c["Идентификатор"] == link["id"]), None)
            doc = store.put(link["id"], SHIPMENT_KIND, item, order_no=number, parent_id=link["parent"],
                            number=(item or {}).get("Номер") or link.get("number"))
            ours = linked.get(number)
            if ours is not None:
                ours.shipment_id = link["id"]
                ours.shipment_number = doc.number
                ours.shipment_state = doc.state
        saby_sync.commit(db, serial)
        return {"orders": len(orders), "shipments": len(links)}
    finally:
        db.close()


# --- Черновики реализаций ---

def create_drafts(day: date, user: str, dry_run: bool, only: Optional[List[str]] = None) -> Dict:
    """Черновики реализаций с УПД по заказам Самоката на день. dry_run — всё, кроме записи в СБИС."""
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "skipped": [], "notes": [], "dry_run": dry_run}
    try:
        _step("Вход в СБИС")
        sid = sc.login()
        _step("Список заказов Самоката")
        items = _day_items(sid, day)
        if only:
            items = [i for i in items if _number(i) in set(only)]
        orders = _read_orders(sid, items, day, _step, lambda item: True)
        if not orders:
            raise SamokatError(f"В СБИС нет заказов Самоката с поставкой {day.strftime('%d.%m.%Y')}")
        _step("Образец УПД и ящики ЭДО")
        template = _template(sid, day)
        receiver = _box(sid, {"СвЮЛ": {"ИНН": SAMOKAT_INN, "КПП": SAMOKAT_KPP}})
        sender = _box(sid, {"СвФЛ": {"ИНН": (((orders[0][1].get("НашаОрганизация") or {}).get("СвФЛ")) or {}).get("ИНН", "")}})
        if not receiver or not sender:
            raise SamokatError("СБИС не вернул ящик ЭДО Самоката или наш")

        # Кому делать: у заказа ещё нет реализации
        todo = []
        for item, card, xml in orders:
            number = _number(item)
            address, gln, _ = _point_of(xml)
            ours = _link_order(db, day, address, gln, number, item["Идентификатор"])
            label = f"{number} · {short_address(address)}"
            if ours is None:
                result["notes"].append(f"{label}: в заказах из почты такой точки нет — проверьте письмо")
            if sc.consequences(card, sc.SHIPMENT_TYPE):
                result["skipped"].append(label)        # реализация уже есть — ничего не трогаем
                _row(number, "skip", "реализация уже есть")
                continue
            if ours is not None and ours.shipment_number:
                result["notes"].append(f"{label}: реализации № {ours.shipment_number} в СБИС больше нет — удалена")
                ours.shipment_id = ours.shipment_number = ours.shipment_state = ours.shipment_created_by = None
            todo.append((item, card, xml, ours, label, address))
        db.commit()
        # Коды карточек каталога — в несколько потоков, запись — по одной точке
        codes = sc.pmap(lambda t: item_codes(sid, t[1]), todo,
                        lambda i, n: _step(f"Сопоставление позиций с каталогом: {i} из {n}", i, n))

        # Строки реестра и файлы УПД — здесь, по порядку; запись — по переключателю потоков
        used: set = set()
        if dry_run:
            _previews.clear()
        jobs = []
        for (item, card, xml, ours, label, address), item_map in zip(todo, codes):
            number = _number(item)
            try:
                if isinstance(item_map, Exception):
                    raise item_map
                row = _registry_row(db, day, address, used)
                if row is None:
                    raise SamokatError("нет строки в реестре 1С по этому адресу — загрузите реестр за день")
                used.add(row.id)
                qty, gross = samokat_upd.totals(xml)
                if row.amount is not None and abs(row.amount - gross) > 1:
                    result["notes"].append(f"{label}: сумма в 1С {row.amount}, в заказе {gross}")
                name, content = samokat_upd.build(xml, template, number=row.number, day=day, contract=CONTRACT,
                                                  receiver_box=receiver, sender_box=sender, codes=item_map)
            except (sc.SabyError, samokat_upd.UpdError) as exc:
                result["errors"][label] = str(exc)
                _row(number, "error", str(exc))
                continue
            if dry_run:
                _previews[number] = (name, content)
                result["done"].append(label)
                _row(number, "ok", "УПД собран")
                continue
            _row(number, "wait", "в очереди")
            jobs.append({"item": item, "card": card, "xml": xml, "ours": ours, "label": label, "address": address,
                         "number": number, "doc_number": row.number, "name": name, "content": content})
        if dry_run or not jobs:
            db.commit()
            return result

        total = len(jobs)
        counter = saby_actions._Counter()

        def write(job):
            _row(job["number"], "work", "создаю")
            contractor = dict(job["card"].get("Контрагент") or {})
            contractor["Идентификатор"] = receiver
            try:
                return sc.write_document(sid, sc.DOCS_URL, {
                    "Тип": sc.SHIPMENT_TYPE, "Номер": job["doc_number"], "Дата": day.strftime("%d.%m.%Y"),
                    "Примечание": job["address"],
                    # Срок поставки — как в реализациях, которые СБИС делает сам из заказа:
                    # в Расширении. Поле «Срок» документа СБИС показывает как срок оплаты
                    "Расширение": {"СрокПоставки": f"{day.strftime('%d.%m.%Y')} {_time(job['xml'])}"},
                    "НашаОрганизация": job["card"].get("НашаОрганизация") or {},
                    "Контрагент": contractor,
                    # Связь «Отгрузка», как у СБИС: по ней заказ считает отгруженное
                    "ДокументОснование": [{"ВидСвязи": "Отгрузка", "Документ": {"Идентификатор": job["item"]["Идентификатор"]}}],
                    "Вложение": [{"Файл": {"Имя": f"{job['name']}.xml",
                                           "ДвоичныеДанные": base64.b64encode(job["content"]).decode()}}],
                })
            except sc.SabyError as exc:
                # Обрыв связи: запись могла пройти. Не повторяем — смотрим, есть ли реализация у заказа
                if not sc.is_link_error(exc):
                    raise
                again = sc.read_card(sid, sc.DOCS_URL, job["item"]["Идентификатор"], sc.ORDER_TYPE)
                found = sc.consequences(again, sc.SHIPMENT_TYPE)
                if not found:
                    raise SamokatError(f"{exc}. Реализации у заказа нет — можно повторить «Создать»") from exc
                return {"Идентификатор": found[-1]["id"], "Состояние": {"Название": "Черновик"}}
            finally:
                _step(f"Создаю реализации: {counter.next()} из {total}", counter.value, total)

        _step(f"Создаю реализации: 0 из {total}", 0, total)
        # В базу пишет только этот поток
        for job, doc in zip(jobs, sc.pmap_writes(write, jobs, on_brake=saby_actions._brake(result))):
            if isinstance(doc, Exception):
                result["errors"][job["label"]] = str(doc)
                _row(job["number"], "error", str(doc))
                continue
            ours = job["ours"]
            if ours is not None:
                ours.shipment_id = doc.get("Идентификатор")
                ours.shipment_number = job["doc_number"]
                ours.shipment_state = sc.state(doc) or "Черновик"
                ours.shipment_created_by = user
            result["done"].append(job["label"])
            _row(job["number"], "ok", "черновик создан")
            db.commit()
        db.commit()
        return result
    finally:
        db.close()


# --- Ответ на заказ: «Утверждение» → «Утвердить» ---

APPROVED = "ПодтверждениеЗаказа"     # вложение ответа на заказ (ORDRSP) — его СБИС кладёт при утверждении


def approve_orders(day: date, only: Optional[List[str]] = None) -> Dict:
    """Утвердить заказы Самоката дня — как кнопкой «Утвердить» в кабинете: СБИС отправит
    ответ на заказ «подтверждён без изменений». Утверждённые не трогаем. Если переход
    требует подписи — не делаем его сами, а отдаём странице: она откроет окно подписи."""
    result = {"done": [], "errors": {}, "skipped": [], "notes": [], "need_sign": []}
    _step("Вход в СБИС")
    sid = sc.login()
    _step("Список заказов Самоката")
    items = _day_items(sid, day)
    if only:
        items = [i for i in items if _number(i) in set(only)]
    todo = []
    for item in items:
        number = _number(item)
        if APPROVED in sc.attachments(item):
            result["skipped"].append(number)
            _row(number, "skip", "уже утверждён")
        else:
            todo.append(item)
    cards = sc.pmap(lambda it: sc.read_card(sid, sc.DOCS_URL, it["Идентификатор"], sc.ORDER_TYPE), todo,
                    lambda i, n: _step(f"Читаю заказы: {i} из {n}", i, n))
    ready = []
    for item, card in zip(todo, cards):
        number = _number(item)
        if isinstance(card, Exception):
            result["errors"][number] = str(card)
            _row(number, "error", str(card))
            continue
        stage = next((st for st in card.get("Этап") or [] if st.get("Название") == "Утверждение"
                      and st.get("Служебный") != "Да"), None)
        action = next((a for a in (stage or {}).get("Действие") or [] if a.get("Название") == "Утвердить"), None)
        if action is None:
            result["skipped"].append(number)
            _row(number, "skip", "утверждать нечего")
            continue
        if action.get("ТребуетПодписания") == "Да":
            result["need_sign"].append(number)
            _row(number, "wait", "нужна подпись")
            continue
        ready.append((item, stage.get("Идентификатор", "")))

    total = len(ready)
    counter = saby_actions._Counter()

    def approve(entry):
        item, stage_id = entry
        _row(_number(item), "work", "утверждаю")
        try:
            sc.run_action(sid, sc.DOCS_URL, item["Идентификатор"], "Утверждение", "Утвердить", stage_id)
        except sc.SabyError as exc:
            if not sc.is_link_error(exc):
                raise
            # Обрыв связи: не повторяем — смотрим по списку, ушёл ли ответ на заказ
            again = next((i for i in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, mask=_number(item), pages=1)
                          if i["Идентификатор"] == item["Идентификатор"]), None)
            if again is None or APPROVED not in sc.attachments(again):
                raise SamokatError(f"{exc}. По кабинету заказ не утверждён — можно повторить") from exc
        finally:
            _step(f"Утверждаю заказы: {counter.next()} из {total}", counter.value, total)

    for (item, _), got in zip(ready, sc.pmap_writes(approve, ready, on_brake=saby_actions._brake(result))):
        number = _number(item)
        if isinstance(got, Exception):
            result["errors"][number] = str(got)
            _row(number, "error", str(got))
        else:
            result["done"].append(number)
            _row(number, "ok", "утверждён")
    if result["need_sign"]:
        result["notes"].append(f"Утверждение требует подписи: {len(result['need_sign'])} — откроется окно подписи")
    return result


# --- Фоновая операция ---

LABELS = {"refresh": "Чтение СБИС · Самокат", "check": "Проверка УПД · Самокат",
          "create": "Черновики реализаций · Самокат", "transport": "Заказ на перевозку · Самокат",
          "waybills": "Создание ЭТрН · Самокат", "waybills_check": "Проверка ЭТрН · Самокат",
          "approve": "Утверждение заказов · Самокат"}
WRITE_MODES = ("create", "transport", "waybills", "approve")   # check и waybills_check в кабинет не пишут
ROW_KINDS = {"check": "shipment", "create": "shipment", "waybills": "waybill", "waybills_check": "waybill",
             "approve": "order"}


def _run(day: date, user: str, mode: str, only: Optional[List[str]], carrier_id: Optional[int]) -> Dict:
    from src import samokat_transport as st
    if mode in ("check", "create"):
        return create_drafts(day, user, mode == "check", only)
    if mode == "transport":
        return st.create_transport(day, only or [], carrier_id, user)
    if mode == "approve":
        return approve_orders(day, only)
    return st.create_waybills(day, only or [], user, dry_run=mode == "waybills_check")


def _worker(day: date, user: str, mode: str, only: Optional[List[str]], carrier_id: Optional[int] = None) -> None:
    try:
        # Одна операция с кабинетом за раз — общий замок со страницей «СБИС»
        with saby_sync._lock:
            try:
                if mode in WRITE_MODES:
                    with sc.writing(LABELS[mode]):
                        _progress["result"] = _run(day, user, mode, only, carrier_id)
                elif mode != "refresh":
                    _progress["result"] = _run(day, user, mode, only, carrier_id)
            finally:
                # Перечитываем всегда: часть черновиков могла успеть создаться до ошибки
                _step("Перечитываю день")
                refresh_day(day)
        _progress["error"] = None
    except sc.SabyError as exc:
        _progress["error"] = str(exc)
    except Exception as exc:
        logger.error(f"Ошибка СБИС Самоката ({mode}) за {day}: {exc}", exc_info=True)
        _progress["error"] = f"Сбой: {exc}"
    finally:
        _progress["running"] = False
        _progress["finished_at"] = sheet_sync.local_now().isoformat(timespec="seconds")


def start(day: date, user: str, mode: str, only: Optional[List[str]] = None, orders=(),
          carrier_id: Optional[int] = None) -> bool:
    """mode: refresh — прочитать кабинет; check — собрать УПД без записи; create — черновики.
    orders — точки, по которым пойдёт работа: их строки на странице покажут ход."""
    if saby_sync.busy():
        return False
    _progress.update(running=True, day=day.isoformat(), label=LABELS[mode], step="Начинаю", done=0, total=0,
                     error=None, result=None, kind="", rows={})
    if mode in ROW_KINDS:
        saby_sync.track(_progress, ROW_KINDS[mode], orders)
    threading.Thread(target=_worker, args=(day, user, mode, only, carrier_id), name="samokat-saby", daemon=True).start()
    return True
