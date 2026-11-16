"""Самокат: заказ на перевозку и ЭТрН (титул 1, грузоотправитель) по техническим требованиям.

Требования ООО «Умный ритейл» к ЭТрН («Технические требования ЭДО и EDI», раздел ЭТрН,
п. 1 — прямые поставщики) и как они выполнены здесь:

* 1.1 Номер заказа — маска «УР-» + 8 цифр, кириллица, верхний регистр, без пробелов
  и лишнего текста. Берётся из ORDERS (номер заказа в СБИС) и проверяется по маске.
  Способ — рекомендованный 1.1.1: ``СодИнфГО/СвГруз/ОпГруз/@ДрОГруз``, одна строка
  ОпГруз = один заказ. Одна ЭТрН — одна точка (один заказ). Других способов
  (ИнфПол «ORDERS», ДокПерЦенн) не добавляем: номер в документе ровно один.
* 1.2 Грузополучатель — всегда головное подразделение: ИНН 7811657720, КПП 997750001,
  ``СвЮЛУч НаимОрг="ООО "УМНЫЙ РИТЕЙЛ""``, юридический адрес, телефон.
  ``АдресДостГр`` — GLN точки из ORDERS и адрес точки из ORDERS.
* Груз — ``УчГосСист="0"`` и ``<Марк>Отсутствует</Марк>``, как в примере требований.

Реквизиты получателя — из «Юрлиц» (покупатель с ИНН 7811657720): так их видно и можно
поправить. Ящик ЭДО — оттуда же, иначе из СБИС (ИнформацияОКонтрагенте).

Заказ на перевозку — свой на день Самоката, отдельно от Лавки: другой маршрут и
другие точки. В saby_docs — вид sm_transport, накладные — sm_waybill; страница
«СБИС» Лавки их не трогает (saby_sync._transport).

Документы создаются черновиками только по кнопке админа; подпись и отправка —
отдельным шагом, Рутокеном в браузере (saby_actions.prepare_signing).
"""
from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import joinedload

from src import database, saby_actions as sa, saby_client as sc, samokat_saby as ss

ORDER_RE = re.compile(r"^УР-\d{8}$")
TRANSPORT_KIND, WAYBILL_KIND = "sm_transport", "sm_waybill"
RECEIVER_NAME = 'ООО "УМНЫЙ РИТЕЙЛ"'   # как в примере требований, п. 1.2


class TransportError(sc.SabyError):
    pass


# --- Стороны и точки ---

def receiver(db) -> database.LegalEntity:
    """Грузополучатель — головное «Умного ритейла» из «Юрлиц». Без телефона не делаем:
    СБИС тогда пишет пустой <Контакт/>, а в требованиях телефон есть."""
    entity = (db.query(database.LegalEntity)
              .filter(database.LegalEntity.inn == ss.SAMOKAT_INN, database.LegalEntity.is_active.is_(True))
              .order_by(database.LegalEntity.id).first())
    if entity is None:
        raise TransportError("В «Юрлицах» нет ООО «Умный ритейл» (ИНН 7811657720) — добавьте покупателем")
    problems = [what for what, ok in (("КПП 997750001", entity.kpp == ss.SAMOKAT_KPP), ("адреса", bool(entity.address)),
                                      ("телефона", bool(entity.phone))) if not ok]
    if problems:
        raise TransportError(f"У «Умного ритейла» в «Юрлицах» нет {', '.join(problems)} — заполните карточку")
    return entity


def _orders(db, day: date, numbers: List[str]) -> List[database.SamokatOrder]:
    rows = (db.query(database.SamokatOrder)
            .options(joinedload(database.SamokatOrder.point), joinedload(database.SamokatOrder.lines))
            .filter(database.SamokatOrder.delivery_date == day).all())
    if numbers:
        rows = [o for o in rows if o.order_no in set(numbers)]
    rows = [o for o in rows if o.order_no]
    if not rows:
        raise TransportError("Нет точек с номером заказа УР- — сначала «Обновить из СБИС»")
    return sorted(rows, key=lambda o: o.point.address if o.point else "")


def _check_order(order: database.SamokatOrder) -> Optional[str]:
    """Что не так с точкой для ЭТрН по требованиям; None — всё есть."""
    if not ORDER_RE.match(order.order_no or ""):
        return f"номер заказа «{order.order_no}» не по маске УР- и 8 цифр"
    if not order.point or not order.point.gln:
        return "нет GLN точки — «Обновить из СБИС», он берётся из заказа"
    if not order.point.order_address:
        return "нет адреса точки из заказа — «Обновить из СБИС»"
    return None


def weights(db, orders: List[database.SamokatOrder], bag_kg: float) -> Tuple[Dict[str, Decimal], List[str]]:
    """Вес точки в кг с тарой — как у Лавки: штуки × вес штуки из карточки товара или названия."""
    codes = {line.barcode for o in orders for line in o.lines}
    products = {p.barcode: p for p in db.query(database.Product).filter(database.Product.barcode.in_(codes))} if codes else {}
    out, unknown = {}, set()
    for o in orders:
        grams = 0
        for line in o.lines:
            one = sa.item_grams(line.name, products.get(line.barcode))
            if one is None:
                unknown.add(line.name)
                continue
            grams += one * int(line.quantity or 0)
        out[o.order_no] = (Decimal(grams) / 1000 + Decimal(str(bag_kg or 0))).quantize(Decimal("0.001"))
    return out, sorted(unknown)


def _stop(order: database.SamokatOrder) -> SimpleNamespace:
    """Точка в том виде, который ждут общие сборщики документов Лавки."""
    return SimpleNamespace(order_no=order.order_no,
                           point=SimpleNamespace(address=order.point.address, name=order.point.address))


def our_transport(db, day: date) -> Optional[database.SabyDoc]:
    return (db.query(database.SabyDoc)
            .filter(database.SabyDoc.delivery_date == day, database.SabyDoc.kind == TRANSPORT_KIND,
                    database.SabyDoc.state != "Удалён")
            .order_by(database.SabyDoc.id.desc()).first())


# --- Заказ на перевозку ---

def create_transport(day: date, numbers: List[str], carrier_id: int, user: str) -> Dict:
    db = database.SessionLocal()
    try:
        if our_transport(db, day) is not None:
            raise TransportError("На этот день заказ на перевозку Самоката уже есть — второй не создаю")
        carrier = db.get(database.Carrier, carrier_id)
        if carrier is None or not carrier.is_active:
            raise TransportError("Перевозчик не найден")
        s = sa.get_settings(db, "samokat")
        own, _ = sa._parties(db)
        orders = _orders(db, day, numbers)
        w, unknown = weights(db, orders, s.bag_weight_kg)
        ss._step("Вход в СБИС")
        sid = sc.login()
        number = str(sc.max_number(sid, sc.TRANSPORT_TYPE) + 1)
        ss._step(f"Создаю заказ на перевозку № {number}")
        file = sc.generate_attachment(sid, sa.TRANSPORT_ATTACH, sa.TRANSPORT_KND, sa.FORMAT_VERSION,
                                      sa.transport_payload(s, own, carrier, [_stop(o) for o in orders], number, day, w))
        doc = sc.write_document(sid, sc.TMS_URL, {
            "Тип": sc.TRANSPORT_TYPE, "Регламент": {"Название": sa.TRANSPORT_RULE},
            "НашаОрганизация": {"СвЮЛ": {"ИНН": own.inn}}, "Вложение": [{"Файл": file}]})
        db.add(database.SabyDoc(saby_id=doc["Идентификатор"], kind=TRANSPORT_KIND, delivery_date=day,
                                number=str(doc.get("Номер") or "") or number, state=sc.state(doc) or sa.DRAFT,
                                cargo=s.cargo_name, party=carrier.name, ours=True, created_by_app=True,
                                created_by=user, carrier_id=carrier.id, link=doc.get("СсылкаДляНашаОрганизация")))
        s.last_carrier_id = carrier.id
        db.commit()
        total = sum(w.values(), Decimal("0"))
        notes = [f"Вес не определился, посчитан нулём: {', '.join(unknown)}"] if unknown else []
        return {"done": [f"заказ на перевозку № {number}, точек {len(orders)}, {sa._num(total)} кг"],
                "errors": {}, "skipped": [], "notes": notes}
    finally:
        db.close()


# --- ЭТрН ---

def waybill_payload(s, own, buyer: database.LegalEntity, carrier, order: database.SamokatOrder, number: str,
                    transport: database.SabyDoc, day: date, weight_kg: Decimal) -> dict:
    """Титул грузоотправителя: общий сборщик Лавки и замены по требованиям Самоката."""
    party = SimpleNamespace(inn=buyer.inn, kpp=buyer.kpp, full_name=RECEIVER_NAME, name=RECEIVER_NAME,
                            address=buyer.address, phone=buyer.phone)
    payload = sa.waybill_payload(s, own, party, carrier, _stop(order), number, transport, day, weight_kg)
    info = payload[sa.WAYBILL_KND]["Файл"]["Документ"]["СодИнфГО"]
    # 1.1: номер заказа только одним способом — ДрОГруз в строке груза
    info.pop("ИнфПол", None)
    info["СвГруз"]["ОпГруз"][0].update({"ДрОГруз": order.order_no, "УчГосСист": "0", "Марк": "Отсутствует"})
    # 1.2: место доставки — GLN и адрес точки из ORDERS
    info["СвГП"]["АдресДостГр"] = {"ГЛН": order.point.gln,
                                    "АдресИнф": {"АдрТекст": order.point.order_address, "КодСтр": "643"}}
    return payload


def check_file(xml: str, order: database.SamokatOrder) -> List[str]:
    """Сверка готового файла с требованиями: что СБИС собрал, то и проверяем."""
    root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", xml))
    info = root.find("Документ/СодИнфГО")
    problems = []
    rows = info.findall("СвГруз/ОпГруз") if info is not None else []
    if len(rows) != 1 or rows[0].get("ДрОГруз") != order.order_no:
        problems.append("номер заказа не в ОпГруз/@ДрОГруз")
    if info is not None and info.find("ИнфПол/ТекстИнф[@Идентиф='ORDERS']") is not None:
        problems.append("лишний номер заказа в ИнфПол")
    gp = info.find("СвГП/РекИдентГП/ИдСв/СвЮЛУч") if info is not None else None
    if gp is None or gp.get("ИННЮЛ") != ss.SAMOKAT_INN or gp.get("КПП") != ss.SAMOKAT_KPP:
        problems.append("грузополучатель не головное (ИНН 7811657720, КПП 997750001)")
    if info is None or (info.findtext("СвГП/АдресДостГр/ГЛН") or "") != order.point.gln:
        problems.append("в АдресДостГр не GLN точки из заказа")
    if info is None or not (info.findtext("СвГП/РекИдентГП/Контакт/Тлф") or "").strip():
        problems.append("нет телефона грузополучателя")
    return problems


def create_waybills(day: date, numbers: List[str], user: str, dry_run: bool = False) -> Dict:
    """ЭТрН на каждую точку черновиком. dry_run — собрать и сверить файлы, ничего не создавая."""
    db = database.SessionLocal()
    result = {"done": [], "errors": {}, "notes": [], "skipped": [], "dry_run": dry_run}
    try:
        transport = our_transport(db, day)
        s = sa.get_settings(db, "samokat")
        if transport is None and dry_run:
            # Проверка до заказа на перевозку: номер заказа условный, перевозчик — последний выбранный
            transport = SimpleNamespace(number="0", saby_id=None, state=sa.DRAFT, carrier_id=s.last_carrier_id)
            result["notes"].append("Заказа на перевозку ещё нет: в проверочных ЭТрН его номер условный (0)")
        if transport is None:
            raise TransportError("Сначала нужен заказ на перевозку Самоката: накладные ссылаются на его номер")
        if transport.state == sa.DRAFT and not dry_run:
            raise TransportError("Сначала подпишите заказ на перевозку")
        if "Отклон" in (transport.state or ""):
            raise TransportError(f"Заказ на перевозку в состоянии «{transport.state}» — накладные к нему не делаю")
        carrier = db.get(database.Carrier, transport.carrier_id) if transport.carrier_id else None
        if carrier is None:
            raise TransportError("Не понял, кто перевозчик заказа на перевозку")
        own, _ = sa._parties(db)
        buyer = receiver(db)
        have = {d.order_no for d in db.query(database.SabyDoc).filter(
            database.SabyDoc.delivery_date == day, database.SabyDoc.kind == WAYBILL_KIND,
            database.SabyDoc.state != "Удалён") if d.order_no}
        orders = []
        for o in _orders(db, day, numbers):
            label = f"{o.order_no} · {ss.short_address(o.point.address)}"
            if o.order_no in have:
                result["skipped"].append(label)
                ss._row(o.order_no, "skip", "ЭТрН уже есть")
                continue
            problem = _check_order(o)
            if problem:
                result["errors"][label] = problem
                ss._row(o.order_no, "error", problem)
                continue
            orders.append(o)
        if not orders:
            return result
        w, unknown = weights(db, orders, s.bag_weight_kg)
        if unknown:
            result["notes"].append(f"Вес не определился, посчитан нулём: {', '.join(unknown)}")
        ss._step("Вход в СБИС")
        sid = sc.login()
        box = buyer.edo_id or ss._box(sid, {"СвЮЛ": {"ИНН": ss.SAMOKAT_INN, "КПП": ss.SAMOKAT_KPP}})
        if not box:
            raise TransportError("Нет ящика ЭДО Самоката: ни в «Юрлицах», ни в СБИС")
        first = sc.max_number(sid, sc.WAYBILL_TYPE) + 1
        for i, order in enumerate(orders):
            label = f"{order.order_no} · {ss.short_address(order.point.address)}"
            number = str(first + i)
            ss._step(f"{'Проверяю' if dry_run else 'Создаю'} ЭТрН {i + 1} из {len(orders)}", i + 1, len(orders))
            ss._row(order.order_no, "work", "собираю ЭТрН" if dry_run else "создаю ЭТрН")
            try:
                file = sc.generate_attachment(sid, sa.WAYBILL_ATTACH, sa.WAYBILL_KND, sa.FORMAT_VERSION,
                                              waybill_payload(s, own, buyer, carrier, order, number, transport, day,
                                                              w[order.order_no]))
                xml = (base64.b64decode(file["ДвоичныеДанные"]).decode("cp1251") if file.get("ДвоичныеДанные")
                       else sc.download(sid, file["Ссылка"]))
                problems = check_file(xml, order)
                if problems:
                    raise TransportError("файл не по требованиям: " + "; ".join(problems))
                if dry_run:
                    result["done"].append(label)
                    ss._row(order.order_no, "ok", "ЭТрН собрана")
                    continue
                doc = sc.write_document(sid, sc.TMS_URL, {
                    "Тип": sc.WAYBILL_TYPE, "Регламент": {"Название": sa.WAYBILL_RULE},
                    "НашаОрганизация": {"СвЮЛ": {"ИНН": own.inn}}, "Вложение": [{"Файл": file}],
                    "ДокументОснование": [{"ВидСвязи": "Обычная связь",
                                           "Документ": {"Идентификатор": transport.saby_id}}],
                    # Получатель — головное «Умного ритейла», ящик задаём явно
                    "Контрагент": {"СвЮЛ": {"ИНН": ss.SAMOKAT_INN, "КПП": ss.SAMOKAT_KPP}, "Идентификатор": box},
                })
                db.add(database.SabyDoc(saby_id=doc["Идентификатор"], kind=WAYBILL_KIND, delivery_date=day,
                                        number=str(doc.get("Номер") or "") or number, state=sc.state(doc) or sa.DRAFT,
                                        order_no=order.order_no, cargo=s.cargo_name, parent_id=transport.saby_id,
                                        address=order.point.address, ours=True, created_by_app=True, created_by=user,
                                        read_at=datetime.utcnow(), link=doc.get("СсылкаДляНашаОрганизация")))
                db.commit()
                result["done"].append(label)
                ss._row(order.order_no, "ok", "создана")
            except sc.SabyError as exc:
                db.rollback()
                result["errors"][label] = str(exc)
                ss._row(order.order_no, "error", str(exc))
        return result
    finally:
        db.close()


# --- Чтение кабинета: состояния наших документов ---

def refresh(sid: str, db, day: date, store) -> None:
    """Номер и состояние наших заказа на перевозку и ЭТрН — из списков за день, без открытия."""
    mine = {d.saby_id: d for d in store.rows.values() if d.kind in (TRANSPORT_KIND, WAYBILL_KIND)}
    if not mine:
        return
    for doc_type, kind in ((sc.TRANSPORT_TYPE, TRANSPORT_KIND), (sc.WAYBILL_TYPE, WAYBILL_KIND)):
        if not any(d.kind == kind for d in mine.values()):
            continue
        seen = set()
        for item in sc.list_documents(sid, sc.TMS_URL, doc_type, start=day, end=day):
            if item["Идентификатор"] in mine:
                seen.add(item["Идентификатор"])
                store.put(item["Идентификатор"], kind, item)
        for doc in mine.values():
            if doc.kind == kind and doc.saby_id not in seen:
                doc.state = "Удалён"
