"""Действия в кабинете СБИС по дню поставки: реализации, заказ на перевозку, ЭТрН, подпись.

Перенесено из ka-webapp (edo, saby, routers/yandex) и подогнано под Обед.
Всё запускает только админ, по кнопке, по выбранным точкам. Правила оттуда же:

* реализацию не открываем, пока она не завершена (saby_client.read_card);
* в чужой документ не пишем: проводим только реализацию, где номер и адрес те,
  что ждём, — свою мы так и создаём;
* перед записью состояние перечитываем: отправленное не отзывается;
* ошибка по одной точке не останавливает остальные, но честно показывается.
"""
from __future__ import annotations

import base64
import logging
import secrets
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from src import database, desadv, saby_client as sc, saby_sync, sheet_sync, upd_registry

logger = logging.getLogger("SabyActions")

PROGRAM_VERSION = "Obed Store HUB 1.0"
TRANSPORT_RULE, TRANSPORT_ATTACH, TRANSPORT_KND = "Заказ на перевозку", "ЗаказЗаявка", "1110361"
WAYBILL_RULE, WAYBILL_ATTACH, WAYBILL_KND = "Транспортная накладная", "ЭТрН", "1110339"
FORMAT_VERSION = "5.01"
TARE_CODE = "FT"          # термоупаковка
MASS_METHOD = "01"        # взвешивание, как в накладных из кабинета
ORDER_ACTIONS = ("Отправить", "Отправить черновик")
WAYBILL_ACTIONS = ("Погружен",)   # титул грузоотправителя подписывают отметкой о погрузке
DRAFT = "Документ редактируется"
NOTICE_BUDGET = 20        # сколько завершённых реализаций открыть ради каталога позиций


class ActionError(sc.SabyError):
    pass


# --- Настройки и стороны ---

def get_settings(db) -> database.SabySettings:
    row = db.query(database.SabySettings).first()
    if row is None:
        row = database.SabySettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _parties(db) -> Tuple[database.LegalEntity, database.LegalEntity]:
    own = (db.query(database.LegalEntity).filter_by(role="own", is_active=True)
           .order_by(database.LegalEntity.is_default.desc()).first())
    buyer = (db.query(database.LegalEntity).filter_by(role="buyer", is_active=True)
             .order_by(database.LegalEntity.is_default.desc()).first())
    if own is None:
        raise ActionError("Не заполнено своё юрлицо (раздел «Юрлица»)")
    if buyer is None:
        raise ActionError("Не заполнен покупатель — ООО «Яндекс.Лавка» (раздел «Юрлица»)")
    if not own.phone:
        raise ActionError("У своего юрлица нет телефона — без него ЭТрН не проходит проверку формата")
    return own, buyer


def _fio(entity) -> Dict[str, str]:
    return {k: v for k, v in (("Фамилия", entity.last_name), ("Имя", entity.first_name),
                              ("Отчество", entity.middle_name)) if v}


def _composer(own) -> str:
    return " ".join(p for p in ("Индивидуальный предприниматель", *_fio(own).values()) if p)


def _num(value) -> str:
    text = f"{Decimal(str(value)):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# --- Точки дня ---

def _orders(db, day: date, numbers: List[str]) -> List[database.LavkaOrder]:
    from sqlalchemy.orm import joinedload
    query = (db.query(database.LavkaOrder).options(joinedload(database.LavkaOrder.point))
             .filter(database.LavkaOrder.delivery_date == day))
    orders = query.all()
    if numbers:
        wanted = set(numbers)
        orders = [o for o in orders if o.order_no in wanted]
    if not orders:
        raise ActionError("Не выбрано ни одной точки")
    return sorted(orders, key=lambda o: o.point.name or "")


def _registry_row(db, day: date, order: database.LavkaOrder, used: set) -> Optional[database.UpdRow]:
    """Строка реестра 1С для точки: по адресу, как в ka-webapp. Сумма — только проверка."""
    for row in db.query(database.UpdRow).filter(database.UpdRow.day == day).order_by(database.UpdRow.number):
        if row.id not in used and upd_registry.same_address(order.point.address or "", row.address or ""):
            return row
    return None


# --- Реализации ---

def _find_order(sid: str, number: str) -> Optional[dict]:
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.ORDER_TYPE, mask=number, pages=1):
        if str(item.get("Номер") or "").strip() == number:
            return item
    return None


def _shipment_of(sid: str, order_card: dict) -> Optional[dict]:
    """Реализация, сделанная из заказа, — из списка, без открытия."""
    links = sc.consequences(order_card, sc.SHIPMENT_TYPE)
    if not links:
        return None
    link = links[-1]
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, mask=link["number"], pages=1):
        if item["Идентификатор"] == link["id"]:
            return item
    return {"Идентификатор": link["id"], "Номер": link["number"], "Состояние": {"Название": ""}}


def _approval_stage(order_card: dict) -> str:
    return next((st.get("Идентификатор", "") for st in order_card.get("Этап") or []
                 if st.get("Название") == "Утверждение"), "")


def _notices(db, sid: str, needed: set, day: date, buyer_inn: str) -> Tuple[str, Dict[str, dict]]:
    """Образец уведомления и каталог позиций — из завершённых реализаций Лавки.

    Открываем только завершённые (их ka-webapp читал сотнями без вреда), не больше
    NOTICE_BUDGET после образца. Найденное кладём в edo_items, чтобы не искать снова.
    """
    known = {i.buyer_code: {"name": i.name, "code": i.code} for i in db.query(database.EdoItem)}
    template = ""
    found: Dict[str, dict] = {}
    opened = 0
    for item in sc.list_documents(sid, sc.DOCS_URL, sc.SHIPMENT_TYPE, start=day - timedelta(days=90), end=day, pages=12):
        if template and (needed <= set(known) | set(found) or opened >= NOTICE_BUDGET):
            break
        state = sc.state(item)
        if state not in sc.FINISHED:
            continue
        opened += 1
        saby_sync.step(f"Читаю номенклатуру: завершённая реализация {opened}")
        card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.SHIPMENT_TYPE, state=state)
        text = sc.xml_attachment(sid, card, "УведомлОтгр")
        if not text:
            continue
        notice = ET.fromstring(text)
        node = notice.find("Документ/Стороны/Покупатель/СвЮЛ")
        if buyer_inn and (node.get("ИНН") if node is not None else "") != buyer_inn:
            continue
        template = template or text
        body = notice.find("Документ")
        for row in body.iter("СтрТабл") if body is not None else []:
            code = row.get("КодПокупателя") or ""
            if code and code not in known and code not in found:
                found[code] = {"name": row.get("Название"), "code": row.get("КодПоставщика")}
    if not template:
        raise ActionError("Среди реализаций за три месяца нет завершённой по Лавке — не с чего взять образец документа")
    for code, value in found.items():
        db.add(database.EdoItem(buyer_code=code, name=(value["name"] or "")[:300], code=(value["code"] or "")[:60]))
    db.commit()
    return template, {**known, **found}


def create_shipments(day: date, numbers: List[str], user: str) -> Dict:
    """Реализации черновиком, сразу заполненные: номер УПД из реестра, адрес, позиции."""
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "notes": []}
    try:
        orders = _orders(db, day, numbers)
        saby_sync.step("Вход в СБИС")
        sid = sc.login()
        used, ready, needed = set(), [], set()
        for i, order in enumerate(orders, start=1):
            label = order.point.name
            saby_sync.step(f"Читаю заказ {i} из {len(orders)}", i, len(orders))
            row = _registry_row(db, day, order, used)
            if row is None:
                result["errors"][label] = "нет строки в реестре 1С — загрузите реестр"
                continue
            used.add(row.id)
            item = _find_order(sid, order.order_no)
            if item is None:
                result["errors"][label] = f"заказ {order.order_no} в СБИС не найден"
                continue
            card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.ORDER_TYPE)
            if sc.consequences(card, sc.SHIPMENT_TYPE):
                result["errors"][label] = "реализация уже есть"
                continue
            order_xml = sc.xml_attachment(sid, card, "Заказ")
            if not order_xml:
                result["errors"][label] = "в заказе нет вложения с позициями"
                continue
            card["Идентификатор"] = item["Идентификатор"]
            ready.append((order, row, card, order_xml))
            needed |= {r.get("КодПокупателя") or "" for r in ET.fromstring(order_xml).iter("СтрТабл")} - {""}
        if not ready:
            return result
        inn = ((ready[0][2].get("Контрагент") or {}).get("СвЮЛ") or {}).get("ИНН") or ""
        template, catalogue = _notices(db, sid, needed, day, inn)
        for i, (order, row, card, order_xml) in enumerate(ready, start=1):
            label = order.point.name
            saby_sync.step(f"Создаю реализацию {i} из {len(ready)}", i, len(ready))
            note = row.address or order.point.name
            try:
                name, content = desadv.build(order_xml, template, number=row.number, day=day, note=note,
                                             catalogue=catalogue)
                sc.write_document(sid, sc.DOCS_URL, {
                    "Тип": sc.SHIPMENT_TYPE, "Номер": row.number, "Дата": day.strftime("%d.%m.%Y"),
                    "Примечание": note,
                    "НашаОрганизация": card.get("НашаОрганизация") or {},
                    "Контрагент": card.get("Контрагент") or {},
                    "ДополнительныеПоля": {"НомерТоварнойНакладной": row.number,
                                           "ДатаТоварнойНакладной": day.strftime("%d.%m.%y")},
                    "ДокументОснование": [{"ВидСвязи": "Обычная связь",
                                           "Документ": {"Идентификатор": card["Идентификатор"]}}],
                    "Вложение": [{"Файл": {"Имя": f"{name}.xml",
                                           "ДвоичныеДанные": base64.b64encode(content).decode()}}],
                })
                result["done"].append(label)
                unknown = desadv.unknown_positions(order_xml, catalogue)
                if unknown:
                    result["notes"].append(f"{label}: названия из заказа Лавки, нет в каталоге — {', '.join(unknown)}")
            except (sc.SabyError, desadv.NoticeError) as exc:
                result["errors"][label] = str(exc)
        return result
    finally:
        db.close()


def process_shipments(day: date, numbers: List[str], user: str) -> Dict:
    """Провести: отправить реализацию и утвердить заказ. Подпись не нужна."""
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "notes": []}
    try:
        orders = _orders(db, day, numbers)
        saby_sync.step("Вход в СБИС")
        sid = sc.login()
        used = set()
        for i, order in enumerate(orders, start=1):
            label = order.point.name
            saby_sync.step(f"Провожу точку {i} из {len(orders)}", i, len(orders))
            try:
                row = _registry_row(db, day, order, used)
                if row is not None:
                    used.add(row.id)
                item = _find_order(sid, order.order_no)
                if item is None:
                    raise ActionError(f"заказ {order.order_no} в СБИС не найден")
                card = sc.read_card(sid, sc.DOCS_URL, item["Идентификатор"], sc.ORDER_TYPE)
                shipment = _shipment_of(sid, card)
                if shipment is None:
                    raise ActionError("реализации ещё нет — сначала «Создать»")
                # Состояние — только что из списка: точку могли провести руками минуту назад
                if sc.state(card) in sc.FINISHED or sc.state(shipment) != DRAFT:
                    raise ActionError(f"уже проведена: реализация «{sc.state(shipment) or '—'}», заказ «{sc.state(card)}»")
                number = str(shipment.get("Номер") or "").strip()
                note = str(shipment.get("Примечание") or "")
                # Чужую реализацию не правим и не отправляем: только ту, где всё как мы создаём
                if row is None or number != row.number or note != (row.address or order.point.name):
                    raise ActionError(f"в реализации № {number or '—'} не тот номер или адрес "
                                      f"(ждём № {row.number if row else '—'}). Проверьте в кабинете — сами не правим")
                sc.run_action(sid, sc.DOCS_URL, shipment["Идентификатор"], "Отправка", "Отправить")
                sc.run_action(sid, sc.DOCS_URL, item["Идентификатор"], "Утверждение", "Утвердить", _approval_stage(card))
                result["done"].append(label)
            except sc.SabyError as exc:
                result["errors"][label] = str(exc)
        return result
    finally:
        db.close()


# --- Заказ на перевозку ---

def _carrier_party(carrier) -> dict:
    if carrier.kind == "ЮЛ":
        block = {"ИНН": carrier.inn, "Наименование": carrier.name}
        if carrier.kpp:
            block["КПП"] = carrier.kpp
        return {"ЮЛ": block}
    block = {"ИНН": carrier.inn, **_fio(carrier)}
    return {"ИП" if carrier.kind == "ИП" else "ФЛ": block}


def _contacts(phone: Optional[str]) -> dict:
    return {"Телефон": [{"Значение": phone}]} if phone else {}


def transport_payload(s: database.SabySettings, own, carrier, orders, number: str, day: date) -> dict:
    """Заказ на перевозку на весь день: все точки — одним рейсом, как № 44 из кабинета."""
    stamp = day.strftime("%d.%m.%Y")
    places = len(orders)
    weight = Decimal(str(s.place_weight_kg)) * places
    return {
        "Документ": {"Номер": number, "Дата": stamp, "Тип": "Заказ",
                     "СодержаниеОперации": "Предоставление заказа и заявки на перевозку груза автомобильным транспортом"},
        "Файл": {"Составитель": {"Наименование": _composer(own)}, "ВерсияПрограммы": PROGRAM_VERSION},
        "УсловияПеревозки": {
            "КлиматическийРежим.Температура.Минимальная": s.temp_min,
            "КлиматическийРежим.Температура.Максимальная": s.temp_max,
            "ТребованияПоПеревозкеПищевыхПродуктов":
                f"Перевозка в изотермической таре при температуре от {s.temp_min} до {s.temp_max} °C",
            "УказанияНорм": s.sanitary_norms,
        },
        "Грузоотправитель": {"Реквизиты": {"ИП": {"ИНН": own.inn, **_fio(own)}},
                             "Адрес": {"АдресТекст": own.address, "КодСтраны": "643"},
                             "Контакты": _contacts(own.phone)},
        "Грузоперевозчик": {"Реквизиты": _carrier_party(carrier),
                            "Адрес": {"АдресТекст": carrier.address or own.address, "КодСтраны": "643"},
                            "Контакты": _contacts(carrier.phone)},
        "Маршрут": {
            "Отправление": {"КодСтраны": "643", "АдресТекст": s.pickup_address,
                            "ПодачаТС": {"ДатаВремя": f"{stamp}T{s.pickup_time}:00+03:00", "ДатаВремяНаличиеUTC": "1"}},
            "Пункт": [{"КодСтраны": "643", "АдресТекст": o.point.address or o.point.name,
                       "Операция": {"Тип": "Выгрузка", "ДатаВремя": f"{stamp}T{s.unload_time}:00+03:00",
                                    "ДатаВремяНаличиеUTC": "1"}} for o in orders],
            "КонечныйПункт": {"Название": orders[-1].point.address or orders[-1].point.name},
        },
        "ПараметрыТС": {"Тип": s.vehicle_type, "ТипКузова": s.body_type, "ТипПогрузки": s.loading_type,
                        "Грузоподъемность": float(s.capacity_t or 0), "Вместимость": float(s.volume_m3 or 0)},
        "Груз": {"Позиция": [{
            "Наименование": s.cargo_name, "Состояние": s.cargo_state, "ТараКод": TARE_CODE,
            "МетодОпределенияМассы": MASS_METHOD, "РаспределениеПоДлинеПлатформы": "1", "Делимость": "1",
            "Параметры": {
                "КоличествоМест": str(places), "Масса": {"Брутто": _num(weight)},
                "Длина": _num(Decimal(s.bag_length_cm) / 100), "Ширина": _num(Decimal(s.bag_width_cm) / 100),
                "Высота": _num(Decimal(s.bag_height_cm) / 100),
                "Объем": _num((Decimal(str(s.place_volume_m3)) * places).quantize(Decimal("0.001"))),
            },
        }]},
    }


def our_transport(db, day: date) -> Optional[database.SabyDoc]:
    return (db.query(database.SabyDoc)
            .filter(database.SabyDoc.delivery_date == day, database.SabyDoc.kind == "transport",
                    database.SabyDoc.ours.is_(True), database.SabyDoc.state != "Удалён")
            .order_by(database.SabyDoc.id.desc()).first())


def create_transport(day: date, numbers: List[str], carrier_id: int, user: str) -> Dict:
    db = database.SessionLocal()
    try:
        if our_transport(db, day) is not None:
            raise ActionError("На этот день заказ на перевозку уже есть — второй не создаю")
        carrier = db.query(database.Carrier).get(carrier_id)
        if carrier is None or not carrier.is_active:
            raise ActionError("Перевозчик не найден")
        s = get_settings(db)
        own, _ = _parties(db)
        orders = _orders(db, day, numbers)
        saby_sync.step("Вход в СБИС")
        sid = sc.login()
        number = str(sc.max_number(sid, sc.TRANSPORT_TYPE) + 1)
        saby_sync.step(f"Создаю заказ на перевозку № {number}")
        file = sc.generate_attachment(sid, TRANSPORT_ATTACH, TRANSPORT_KND, FORMAT_VERSION,
                                      transport_payload(s, own, carrier, orders, number, day))
        doc = sc.write_document(sid, sc.TMS_URL, {
            "Тип": sc.TRANSPORT_TYPE, "Регламент": {"Название": TRANSPORT_RULE},
            # Даже для ИП блок называется «СвЮЛ»: форму собственности СБИС определит по ИНН
            "НашаОрганизация": {"СвЮЛ": {"ИНН": own.inn}}, "Вложение": [{"Файл": file}]})
        row = database.SabyDoc(saby_id=doc["Идентификатор"], kind="transport", delivery_date=day,
                               number=str(doc.get("Номер") or "") or number, state=sc.state(doc) or DRAFT,
                               cargo=s.cargo_name, party=carrier.name, ours=True, created_by_app=True,
                               created_by=user, carrier_id=carrier.id, link=doc.get("СсылкаДляНашаОрганизация"))
        db.add(row)
        s.last_carrier_id = carrier.id
        db.commit()
        return {"done": [f"заказ на перевозку № {row.number}"], "errors": {}, "notes": [], "doc_ids": [row.saby_id]}
    finally:
        db.close()


# --- ЭТрН ---

def waybill_payload(s, own, buyer, carrier, order, number: str, transport: database.SabyDoc, day: date) -> dict:
    stamp = day.strftime("%d.%m.%Y")
    weight = _num(Decimal(str(s.place_weight_kg)))
    own_id = {"ИННФЛ": own.inn}
    receiver = {"ИННЮЛ": buyer.inn, "НаимОрг": buyer.full_name or buyer.name}
    if buyer.kpp:
        receiver["КПП"] = buyer.kpp
    vehicle = {"ТипВлад": "1", "ПарТС": {"Тип": carrier.vehicle_type}}
    if carrier.vehicle_plate:
        vehicle["РегНомер"] = carrier.vehicle_plate
    if carrier.vehicle_model:
        vehicle["ПарТС"]["Марка"] = carrier.vehicle_model
    if carrier.capacity_t:
        vehicle["ПарТС"]["Грузопод"] = _num(carrier.capacity_t)
    if carrier.volume_m3:
        vehicle["ПарТС"]["Вместим"] = _num(carrier.volume_m3)
    carrier_block = {"ИдСв": {}, "Адрес": {"АдрИнф": {"АдрТекст": carrier.address or own.address, "КодСтр": "643"}}}
    if carrier.kind == "ЮЛ":
        party = {"ИННЮЛ": carrier.inn, "НаимОрг": carrier.name}
        if carrier.kpp:
            party["КПП"] = carrier.kpp
        carrier_block["ИдСв"] = {"СвЮЛУч": party}
    else:
        party = {"ИННФЛ": carrier.inn, "ФИО": _fio(carrier)}
        if carrier.ogrn:
            party["ОГРНИП"] = carrier.ogrn
        carrier_block["ИдСв"] = {"СвИП" if carrier.kind == "ИП" else "СвФЛУчаст": party}
    if carrier.phone:
        carrier_block["Контакт"] = {"Тлф": [{"value": carrier.phone}]}
    pickup = {"АдресИнф": {"АдрТекст": s.pickup_address, "КодСтр": "643"}}
    return {WAYBILL_KND: {"Файл": {"Документ": {
        "НаимЭкСубСост": _composer(own),
        "СодИнфГО": {
            "НомЗак": transport.number, "ДатаЗак": stamp, "НомерТрН": number, "ДатаТрН": stamp,
            # Без номера ЯЛ Лавка накладную не подписывает
            "ИнфПол": {"ТекстИнф": [{"Идентиф": "ORDERS", "Значение": order.order_no}]},
            "СвГО": {"ГОЭксп": "0", "РекИдентГО": {
                "ИдСв": {"СвИП": {**own_id, "ФИО": _fio(own)}},
                "Адрес": {"АдрИнф": {"АдрТекст": own.address, "КодСтр": "643"}},
                "Контакт": {"Тлф": [{"value": own.phone}]}}},
            "СвГП": {"РекИдентГП": {"ИдСв": {"СвЮЛУч": receiver},
                                    "Адрес": {"АдрИнф": {"АдрТекст": buyer.address, "КодСтр": "643"}},
                                    **({"Контакт": {"Тлф": [{"value": buyer.phone}]}} if buyer.phone else {})},
                     "АдресДостГр": {"АдресИнф": {"АдрТекст": order.point.address or order.point.name, "КодСтр": "643"}}},
            "СвГруз": {"ОпГруз": [{
                "НаимГруз": s.cargo_name, "СостГруз": s.cargo_state, "ВидТар": TARE_CODE, "КолМестГр": "1",
                "Объем": _num(s.place_volume_m3), "СпУпак": "Термосумка",
                "Габар": {"ДлЗнач": _num(Decimal(s.bag_length_cm) / 100), "ШирЗнач": _num(Decimal(s.bag_width_cm) / 100),
                          "ВысЗнач": _num(Decimal(s.bag_height_cm) / 100)},
                "ПлМасГруз": {"МасБрутЗнач": weight}}]},
            "УказГО": {
                "ЗапрПерегруз": "1", "УкНормПрвз": s.sanitary_norms,
                "СвПА": {"ЛицоПА": "Грузоотправитель",
                         "СпосПерУкПА": "Электронное уведомление перевозчика о переадресовке",
                         "КонтПА": {"Тлф": [{"value": own.phone}]}},
                "КлимРеж": {"ТемпНеНиже": str(s.temp_min), "ТемпНеВыше": str(s.temp_max)}},
            "СвПер": carrier_block,
            "СвВодит": {"ИННФЛ": carrier.inn, "ФИО": _fio(carrier),
                        **({"Тлф": [{"value": carrier.phone}]} if carrier.phone else {})},
            "СвТС": {"ТС": vehicle},
            "СвПогруз": {
                "ЗаявПогр": f"{stamp}T{s.pickup_time}:00+03:00", "КолМестПрием": "1", "МасБрутОтгр": weight,
                "МетОпрМасс": MASS_METHOD, "НалКоорТочВрЗаяв": "1", "НалКоорТочВрФПогр": "1", "НалКоорТочВрФУбыт": "1",
                "ФДатВрПриб": f"{stamp}T{s.pickup_time}:00+03:00", "ФДатВрУбыт": f"{stamp}T{s.departure_time}:00+03:00",
                "ФСостКуз": "Хорошее", "ФСостУп": "Целая", "ФСостТар": "Без повреждений",
                "ФАдресПогр": pickup,
                "СвЛицПогрГр": {"СовпГОП": "1", "ИдентРекГО": dict(own_id)},
                "ВладИнфр": {"СовпГОВ": "1", "ИдентРекГО": dict(own_id)},
            },
        },
    }}}}



def create_waybills(day: date, numbers: List[str], user: str) -> Dict:
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "notes": [], "doc_ids": []}
    try:
        transport = our_transport(db, day)
        if transport is None:
            raise ActionError("Сначала нужен заказ на перевозку: накладные ссылаются на его номер")
        if transport.state not in sc.FINISHED:
            raise ActionError("Перевозчик ещё не подписал заказ на перевозку")
        carrier = db.query(database.Carrier).get(transport.carrier_id) if transport.carrier_id else None
        if carrier is None:
            carrier = next((c for c in db.query(database.Carrier).filter_by(is_active=True)
                            if c.name == transport.party), None)
        if carrier is None:
            raise ActionError("Не понял, кто перевозчик: заказ на перевозку создан не приложением. "
                              "Выберите перевозчика в заказе или создайте накладные в кабинете")
        s = get_settings(db)
        own, buyer = _parties(db)
        have = {d.order_no for d in db.query(database.SabyDoc).filter(
            database.SabyDoc.delivery_date == day, database.SabyDoc.kind == "waybill",
            database.SabyDoc.ours.is_(True), database.SabyDoc.state != "Удалён") if d.order_no}
        orders = []
        for o in _orders(db, day, numbers):
            if o.order_no in have:
                result["errors"][o.point.name] = "накладная уже есть"
            else:
                orders.append(o)
        if not orders:
            return result
        saby_sync.step("Вход в СБИС")
        sid = sc.login()
        first = sc.max_number(sid, sc.WAYBILL_TYPE) + 1
        for i, order in enumerate(orders):
            label = order.point.name
            number = str(first + i)
            saby_sync.step(f"Создаю ЭТрН {i + 1} из {len(orders)}", i + 1, len(orders))
            try:
                file = sc.generate_attachment(sid, WAYBILL_ATTACH, WAYBILL_KND, FORMAT_VERSION,
                                              waybill_payload(s, own, buyer, carrier, order, number, transport, day))
                document = {"Тип": sc.WAYBILL_TYPE, "Регламент": {"Название": WAYBILL_RULE},
                            "НашаОрганизация": {"СвЮЛ": {"ИНН": own.inn}}, "Вложение": [{"Файл": file}],
                            "ДокументОснование": [{"ВидСвязи": "Обычная связь",
                                                   "Документ": {"Идентификатор": transport.saby_id}}],
                            # Номер ЯЛ и в карточке: кабинет подставляет его в файл при отправке
                            "ДополнительныеПоля": {"ORDERS": order.order_no}}
                # Ящик Лавки задаём явно: ЭТрН она принимает в Контуре, а не в своём ящике СБИС
                if buyer.edo_id:
                    document["Контрагент"] = {"СвЮЛ": {"ИНН": buyer.inn, "КПП": buyer.kpp}, "Идентификатор": buyer.edo_id}
                doc = sc.write_document(sid, sc.TMS_URL, document)
                db.add(database.SabyDoc(saby_id=doc["Идентификатор"], kind="waybill", delivery_date=day,
                                        number=str(doc.get("Номер") or "") or number, state=sc.state(doc) or DRAFT,
                                        order_no=order.order_no, cargo=s.cargo_name, parent_id=transport.saby_id,
                                        address=order.point.address, ours=True, created_by_app=True, created_by=user,
                                        read_at=datetime.utcnow(), link=doc.get("СсылкаДляНашаОрганизация")))
                db.commit()
                result["done"].append(label)
                result["doc_ids"].append(doc["Идентификатор"])
            except sc.SabyError as exc:
                db.rollback()
                result["errors"][label] = str(exc)
        return result
    finally:
        db.close()


# --- Подпись в браузере ---
#
# Ключ на Рутокене не извлекается, поэтому подписывает браузер через КриптоПро,
# а сервер готовит документ и отправляет готовую подпись. Подготовка и отправка
# должны идти в одной сессии СБИС — её держим здесь под одноразовым ключом.

SIGN_TTL = timedelta(minutes=15)
_SIGNING: Dict[str, dict] = {}
KINDS = {"transport": (sc.TRANSPORT_TYPE, ORDER_ACTIONS), "waybill": (sc.WAYBILL_TYPE, WAYBILL_ACTIONS)}


def sign_targets(db, day: date, kind: str, numbers: List[str]) -> List[database.SabyDoc]:
    """Что подписывать: наши черновики нужного вида за день (по выбранным точкам для ЭТрН)."""
    query = db.query(database.SabyDoc).filter(database.SabyDoc.delivery_date == day, database.SabyDoc.kind == kind,
                                              database.SabyDoc.ours.is_(True), database.SabyDoc.state == DRAFT)
    docs = query.all()
    if kind == "waybill" and numbers:
        docs = [d for d in docs if d.order_no in set(numbers)]
    return docs


def prepare_signing(day: date, kind: str, numbers: List[str], thumbprint: str) -> Dict:
    if kind not in KINDS:
        raise ActionError("Неизвестный вид документа")
    doc_type, preferred = KINDS[kind]
    db = database.SessionLocal()
    try:
        targets = sign_targets(db, day, kind, numbers)
        if not targets:
            raise ActionError("Подписывать нечего: черновиков нет — сначала «Создать»")
        ids = {d.saby_id: (d.number, d.order_no) for d in targets}
    finally:
        db.close()
    sid = sc.login()
    documents, skipped = [], {}
    for saby_id, (number, _) in ids.items():
        try:
            documents.append(sc.prepare_sign(sid, sc.TMS_URL, saby_id, doc_type, thumbprint, preferred))
        except sc.SabyError as exc:
            skipped[f"№ {number}"] = str(exc)
    if not documents:
        raise ActionError(next(iter(skipped.values()), "нечего подписывать"))
    now = datetime.now(timezone.utc)
    for token in [t for t, v in _SIGNING.items() if now - v["at"] > SIGN_TTL]:
        _SIGNING.pop(token, None)
    token = secrets.token_urlsafe(16)
    _SIGNING[token] = {"at": now, "sid": sid, "thumbprint": thumbprint, "day": day, "kind": kind,
                       "docs": {d["id"]: {"stage": d["stage"], "action": d["action"],
                                          "files": {f["id"] for f in d["files"]}} for d in documents}}
    return {"token": token, "documents": [{"id": d["id"], "files": d["files"]} for d in documents], "skipped": skipped}


def send_signing(token: str, signatures: Dict[str, str]) -> Dict:
    item = _SIGNING.pop(token, None)
    if item is None or datetime.now(timezone.utc) - item["at"] > SIGN_TTL:
        raise ActionError("Подготовка устарела — нажмите «Подписать» ещё раз")
    sent, errors = [], {}
    for doc_id, doc in item["docs"].items():
        if not doc["files"] <= set(signatures):
            errors[doc_id] = "подписаны не все файлы документа"
            continue
        try:
            sc.send_signed(item["sid"], sc.TMS_URL, doc_id, doc["stage"], doc["action"], item["thumbprint"],
                           {fid: signatures[fid] for fid in doc["files"]})
            sent.append(doc_id)
        except sc.SabyError as exc:
            errors[doc_id] = str(exc)
    db = database.SessionLocal()
    try:
        names = {d.saby_id: f"№ {d.number}" for d in db.query(database.SabyDoc).filter(database.SabyDoc.saby_id.in_(list(item["docs"])))}
        if item["kind"] == "transport" and sent:
            # Отправили перевозчику — с этой минуты ждём его подпись
            s = get_settings(db)
            for d in db.query(database.SabyDoc).filter(database.SabyDoc.saby_id.in_(sent)):
                d.next_check_at = datetime.utcnow() + timedelta(minutes=s.poll_minutes or 20)
            db.commit()
    finally:
        db.close()
    return {"day": item["day"], "sent": [names.get(i, i) for i in sent],
            "errors": {names.get(i, i): e for i, e in errors.items()}}


# --- Ждём подпись перевозчика ---

def waiting_transports(db) -> List[database.SabyDoc]:
    """Наши заказы на перевозку, ушедшие перевозчику и ещё не подписанные им."""
    return [d for d in db.query(database.SabyDoc).filter(database.SabyDoc.kind == "transport",
                                                         database.SabyDoc.ours.is_(True))
            if d.state not in sc.FINISHED and d.state not in (DRAFT, "Удалён", None, "")
            and "Отклон" not in (d.state or "") and "Ошибк" not in (d.state or "")]


def check_transport(doc_id: str) -> Optional[str]:
    """Перечитать состояние одного заказа на перевозку из списка за его день."""
    db = database.SessionLocal()
    try:
        doc = db.query(database.SabyDoc).filter_by(saby_id=doc_id).first()
        if doc is None:
            return None
        sid = sc.login()
        for item in sc.list_documents(sid, sc.TMS_URL, sc.TRANSPORT_TYPE, start=doc.delivery_date, end=doc.delivery_date):
            if item["Идентификатор"] == doc_id:
                doc.state = sc.state(item) or doc.state
                break
        s = get_settings(db)
        doc.checked_at = datetime.utcnow()
        # Подписал — по этому дню больше не спрашиваем; другие дни ждут своё
        doc.next_check_at = None if doc.state in sc.FINISHED else datetime.utcnow() + timedelta(minutes=s.poll_minutes or 20)
        db.commit()
        return doc.state
    finally:
        db.close()


def poll_due() -> None:
    """Раз в минуту из планировщика: проверить те заказы, чей срок подошёл."""
    if not sc.configured() or saby_sync.progress()["running"]:
        return
    db = database.SessionLocal()
    try:
        now = datetime.utcnow()
        due = [d.saby_id for d in waiting_transports(db) if d.next_check_at is None or d.next_check_at <= now]
    finally:
        db.close()
    for doc_id in due:
        try:
            check_transport(doc_id)
        except sc.SabyError as exc:
            logger.warning(f"Проверка подписи перевозчика не удалась: {exc}")


_poller_started = False


def start_poller() -> None:
    global _poller_started
    if _poller_started:
        return
    _poller_started = True
    import threading
    import time

    def loop():
        while True:
            try:
                poll_due()
            except Exception as exc:
                logger.error(f"Ошибка опроса подписи перевозчика: {exc}", exc_info=True)
            time.sleep(60)

    threading.Thread(target=loop, name="saby-poll", daemon=True).start()
