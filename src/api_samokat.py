"""Заказы «Самоката» (ООО «Умный ритейл»): сводка и заказы по точкам за период.

Письма читает тот же опрос ящика, что и у Лавки (`lavka_mail`): один ящик,
один курсор, один журнал. Поэтому здесь нет своей проверки почты — кнопка
на странице зовёт общую. Ответы устроены как у `/lavka/summary` и `/lavka/orders`,
чтобы страница работала тем же скриптом.
"""
from datetime import date
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from src import (api_lavka, api_saby, auth, database, lavka_mail, saby_actions, saby_client, samokat_saby,
                 samokat_transport, upd_registry)
from src.api_lavka import _money, compare_range, format_range, short_address, sort_key
from src.api_v1 import product_title
from src.database import get_db

router = APIRouter(prefix="/samokat", tags=["Самокат"])

Order, Line = database.SamokatOrder, database.SamokatOrderLine


def _check_range(start: date, end: date):
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        raise HTTPException(status_code=400, detail="Период длиннее года")
    return start, end


@router.get("/status")
def samokat_status(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    status = lavka_mail.get_status(db)
    status["samokat_senders"] = list(database.SAMOKAT_SENDERS)
    last = db.query(func.max(Order.delivery_date)).scalar()
    status["latest_delivery_date"] = last.isoformat() if last else None
    return status


@router.get("/dates")
def samokat_dates(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    rows = (db.query(Order.delivery_date, func.count(Order.id), func.sum(Order.total_quantity))
            .group_by(Order.delivery_date).order_by(Order.delivery_date).all())
    return [{"date": d.isoformat(), "orders": n, "quantity": int(q or 0)} for d, n, q in rows]


def _totals(db: Session, start: date, end: date) -> Dict:
    row = (db.query(func.count(func.distinct(Order.id)), func.count(func.distinct(Order.point_id)),
                    func.count(func.distinct(Line.barcode)), func.coalesce(func.sum(Line.quantity), 0),
                    func.coalesce(func.sum(Line.sum_gross), 0))
           .join(Line, Line.order_id == Order.id)
           .filter(Order.delivery_date.between(start, end)).one())
    return {"orders": row[0], "points": row[1], "items": row[2], "quantity": int(row[3] or 0), "gross": _money(row[4])}


def _items(db: Session, start: date, end: date) -> List[Dict]:
    rows = (db.query(Line.barcode, func.max(Line.name), func.sum(Line.quantity), func.sum(Line.sum_gross),
                     func.count(func.distinct(Order.point_id)))
            .join(Order, Line.order_id == Order.id)
            .filter(Order.delivery_date.between(start, end))
            .group_by(Line.barcode).all())
    return [{"barcode": b, "name": n, "quantity": int(q or 0), "gross": _money(g), "points": p}
            for b, n, q, g, p in rows]


@router.get("/summary")
def samokat_summary(start: date, end: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    start, end = _check_range(start, end)
    last = db.query(func.max(Order.delivery_date)).scalar()
    cmp_from, cmp_to, cmp_label = compare_range(start, end, last)

    items = _items(db, start, end)
    previous = {i["barcode"]: i["quantity"] for i in _items(db, cmp_from, cmp_to)}
    codes = {i["barcode"] for i in items if i["barcode"]}
    products = {}
    if codes:
        for product in db.query(database.Product).filter(database.Product.barcode.in_(codes)):
            products[product.barcode] = {"id": product.id, "title": product_title(product)}
    for item in items:
        was = previous.get(item["barcode"])
        item["was"] = was
        item["delta"] = round((item["quantity"] - was) / was * 100, 1) if was else None
        item["product"] = products.get(item["barcode"])
    items.sort(key=lambda i: sort_key(i["name"], i["quantity"]))

    revised = (db.query(func.count(Order.id))
               .filter(Order.delivery_date.between(start, end), Order.revised_at.isnot(None)).scalar())
    return {
        "start": start.isoformat(), "end": end.isoformat(), "label": format_range(start, end),
        "compare": {"start": cmp_from.isoformat(), "end": cmp_to.isoformat(), "label": cmp_label,
                    "totals": _totals(db, cmp_from, cmp_to)},
        "totals": _totals(db, start, end),
        "unmatched": sum(1 for i in items if not i["product"]),
        "revised": revised,
        "items": items,
    }


def _shipment_text(order) -> str:
    if not order.shipment_number:
        return ""
    draft = order.shipment_state in (None, "", "Черновик", "Документ редактируется")
    return f"реализация № {order.shipment_number}" + (" (черновик)" if draft else f", {order.shipment_state}")


@router.get("/orders")
def samokat_orders(start: date, end: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    start, end = _check_range(start, end)
    rows = (db.query(Order).options(joinedload(Order.point), selectinload(Order.lines))
            .filter(Order.delivery_date.between(start, end)).all())
    result = []
    for o in rows:
        lines = sorted(o.lines, key=lambda l: sort_key(l.name, l.quantity))
        address = o.point.address if o.point else ""
        result.append({
            "id": o.id, "order_no": " · ".join(x for x in (o.order_no, _shipment_text(o)) if x),
            "delivery_date": o.delivery_date.isoformat(),
            "point": "", "address": address, "address_short": short_address(address),
            "positions": len(lines), "quantity": int(o.total_quantity or 0), "gross": _money(o.total_gross),
            "revised": o.revised_at is not None,
            "lines": [{"barcode": l.barcode, "name": l.name, "quantity": l.quantity,
                       "price_gross": _money(l.price_gross), "gross": _money(l.sum_gross)} for l in lines],
        })
    result.sort(key=lambda r: (r["delivery_date"], r["address_short"].lower()))
    return result


# --- Лист номенклатуры на производство — тот же, что у Лавки ---

@router.get("/sheet/sorts")
def samokat_sheet_sorts(user=Depends(auth.get_current_user)):
    return [{"value": v, "label": l} for v, l in api_lavka.SHEET_SORTS]


@router.get("/sheet.html", response_class=HTMLResponse)
def samokat_sheet_html(start: date, end: date, left: str = "", right: str = "",
                       db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    html, _ = api_lavka.render_sheet(db, start, end, left, right, "preview", "/static/img/obed-logo.svg",
                                     items=_items, brand="Самокат")
    return HTMLResponse(html)


@router.get("/sheet.pdf")
def samokat_sheet_pdf(start: date, end: date, left: str = "", right: str = "",
                      db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    html, label = api_lavka.render_sheet(db, start, end, left, right, "pdf",
                                         (api_lavka.STATIC_DIR / "img" / "obed-logo.svg").as_uri(),
                                         items=_items, brand="Самокат")
    return api_lavka.sheet_pdf(html, f"Самокат {label} номенклатура.pdf")


# --- СБИС Самоката: день поставки по точкам ---

def _doc(row) -> Optional[Dict]:
    if row is None:
        return None
    kinds = set((row.attachments or "").split(","))
    return {"id": row.saby_id, "number": row.number, "state": row.state, "link": row.link,
            "amount": _money(row.amount) if row.amount is not None else None,
            "approved": "ПодтверждениеЗаказа" in kinds, "upd": "УпдСчфДоп" in kinds}


def _draft(state: Optional[str]) -> bool:
    return not state or state in ("Черновик", "Документ редактируется")


FAILED = ("проблем", "ошибк", "отклон", "аннулир", "удал")


def _failed(state: Optional[str]) -> bool:
    """Не ушла: проверка СБИС остановила отправку («Проблемы при доставке»), отклонена, удалена."""
    return any(word in (state or "").lower() for word in FAILED)


def _sent(state: Optional[str]) -> bool:
    return not _draft(state) and not _failed(state)


@router.get("/day")
def samokat_day(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Точки дня из базы, без похода в СБИС: заказ УР-, ответ на него, строка реестра 1С по адресу,
    реализация и ЭТрН — как их видело последнее чтение кабинета."""
    rows = (db.query(Order).options(joinedload(Order.point))
            .filter(Order.delivery_date == day).all())
    rows.sort(key=lambda o: short_address(o.point.address if o.point else "").lower())
    registry = upd_registry.rows_for(db, day, samokat_saby.SAMOKAT_INN)
    docs = db.query(database.SabyDoc).filter(database.SabyDoc.delivery_date == day).all()
    mine = [d for d in docs if d.kind.startswith("sm_")]
    by_kind: Dict[str, Dict[str, database.SabyDoc]] = {"sm_order": {}, "sm_shipment": {}, "waybill": {},
                                                       "sm_waybill": {}}
    for d in sorted(docs, key=lambda x: x.id):
        if d.kind in by_kind and d.order_no and d.state != "Удалён":
            by_kind[d.kind][d.order_no] = d
    settings = saby_actions.get_settings(db, "samokat")
    weights, unknown_weight = samokat_transport.weights(db, [o for o in rows if o.order_no], settings.bag_weight_kg)
    transport = samokat_transport.our_transport(db, day)
    used, points = set(), []
    for o in rows:
        address = o.point.address if o.point else ""
        shipment = by_kind["sm_shipment"].get(o.order_no or "")
        ship = _doc(shipment)
        if ship is None and o.shipment_number:     # кабинет ещё не перечитан после создания
            ship = {"id": o.shipment_id, "number": o.shipment_number, "state": o.shipment_state or "Черновик",
                    "link": None, "amount": None, "approved": False, "upd": False}
        # Строку реестра — сначала по номеру реализации (он и есть номер из 1С), потом по адресу
        row = None
        if ship is not None and ship["number"]:
            row = next((r for r in registry if r.id not in used and r.number == ship["number"]), None)
        if row is None:
            row = next((r for r in registry if r.id not in used and upd_registry.same_address(address, r.address or "")), None)
        if row is not None:
            used.add(row.id)
        gross = _money(o.total_gross)
        points.append({
            "id": o.id, "order_no": o.order_no or "", "address": address, "address_short": short_address(address),
            "gln": o.point.gln if o.point else None, "quantity": int(o.total_quantity or 0), "gross": gross,
            "registry": {"number": row.number, "amount": _money(row.amount), "ok": abs(_money(row.amount) - gross) < 1,
                         "number_ok": (ship["number"] == row.number) if ship else None}
            if row is not None else None,
            "order": _doc(by_kind["sm_order"].get(o.order_no or "")),
            "shipment": ship,
            # Своя ЭТрН (создана приложением); иначе — сделанная в кабинете руками
            "waybill": _doc(by_kind["sm_waybill"].get(o.order_no or "") or by_kind["waybill"].get(o.order_no or "")),
            "waybill_ours": (o.order_no or "") in by_kind["sm_waybill"],
            "weight_kg": float(weights.get(o.order_no, 0)) if o.order_no else 0,
        })
    checked = max((d.checked_at for d in mine), default=None)
    n = len(points)
    waiting = transport is not None and transport in saby_actions.waiting_transports(db)
    try:
        samokat_transport.receiver(db)
        receiver_problem = None
    except saby_client.SabyError as exc:
        receiver_problem = str(exc)
    return {
        "day": day.isoformat(), "label": format_range(day, day),
        "is_admin": user.role == "admin", "configured": saby_client.configured(),
        "checked_at": api_saby._local(checked),
        "registry_rows": len(registry),
        "registry_free": [{"number": r.number, "address": r.address} for r in registry if r.id not in used],
        "stats": {"points": n, "registry": sum(1 for p in points if p["registry"]),
                  "orders": sum(1 for p in points if p["order"]),
                  "linked": sum(1 for p in points if p["order_no"]),
                  "approved": sum(1 for p in points if p["order"] and p["order"]["approved"]),
                  "shipments": sum(1 for p in points if p["shipment"]),
                  "drafts": sum(1 for p in points if p["shipment"] and _draft(p["shipment"]["state"])),
                  "sent": sum(1 for p in points if p["shipment"] and _sent(p["shipment"]["state"])),
                  "failed": sum(1 for p in points if p["shipment"] and _failed(p["shipment"]["state"])),
                  "waybills": sum(1 for p in points if p["waybill"]),
                  "our_waybills": sum(1 for p in points if p["waybill_ours"])},
        "points": points,
        "settings": {"hide_split_shipments": bool(settings.hide_split_shipments),
                     "hide_check": bool(settings.hide_check),
                     "hide_split_transport": bool(settings.hide_split_transport),
                     "hide_split_waybills": bool(settings.hide_split_waybills)},
        "carriers": [{"id": c.id, "name": c.name} for c in db.query(database.Carrier)
                     .filter(database.Carrier.is_active.is_(True)).order_by(database.Carrier.name)],
        "last_carrier_id": settings.last_carrier_id,
        "our_transport": None if transport is None else {
            **_doc(transport), "party": transport.party, "draft": transport.state == saby_actions.DRAFT,
            "signed_by_carrier": transport.state in saby_client.FINISHED, "waiting": waiting,
            "next_check_at": api_saby._local(transport.next_check_at) if waiting else None},
        "draft_waybills": [d.order_no for d in docs if d.kind == "sm_waybill" and d.state == saby_actions.DRAFT],
        # Черновики реализаций, созданные приложением: их подписывает «Подписать»
        "draft_shipments": [d.order_no for d in saby_actions.samokat_drafts(db, day)],
        "unknown_weight": unknown_weight,
        "receiver_problem": receiver_problem,
    }


@router.post("/refresh")
def samokat_refresh(day: date, user=Depends(auth.get_current_user)):
    """Прочитать кабинет за день. Только чтение, поэтому кнопка доступна всем."""
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    if not samokat_saby.start(day, user.login, "refresh"):
        raise HTTPException(status_code=409, detail="Другая операция с СБИС ещё идёт, дождитесь её окончания")
    return {"started": True}


# --- СБИС: черновики реализаций с УПД ---

class DraftsIn(BaseModel):
    day: date
    dry_run: bool = True
    orders: List[str] = []      # номера УР-; пусто — все точки дня


@router.post("/shipments")
def samokat_shipments(data: DraftsIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Проверка (dry_run) или создание черновиков реализаций. Ничего не отправляет. Только админ."""
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    targets = data.orders or [o.order_no for o in db.query(Order)
                              .filter(Order.delivery_date == data.day, Order.order_no.isnot(None))]
    if not samokat_saby.start(data.day, admin.login, "check" if data.dry_run else "create",
                              data.orders or None, orders=targets):
        raise HTTPException(status_code=409, detail="Другая операция с СБИС ещё идёт, дождитесь её окончания")
    return {"started": True}


class ActionIn(BaseModel):
    day: date
    action: str                  # transport | waybills | waybills_check
    orders: List[str] = []       # пусто — все точки дня
    carrier_id: Optional[int] = None


@router.post("/action")
def samokat_action(data: ActionIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Заказ на перевозку и ЭТрН Самоката — фоном, черновиками. Только админ, только по кнопке."""
    if data.action not in ("transport", "waybills", "waybills_check", "approve"):
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    if data.action == "transport" and not data.carrier_id:
        raise HTTPException(status_code=400, detail="Выберите перевозчика")
    targets = data.orders or [o.order_no for o in db.query(Order)
                              .filter(Order.delivery_date == data.day, Order.order_no.isnot(None))]
    if not samokat_saby.start(data.day, admin.login, data.action, data.orders or None, orders=targets,
                              carrier_id=data.carrier_id):
        raise HTTPException(status_code=409, detail="Другая операция с СБИС ещё идёт, дождитесь её окончания")
    return {"started": True}


@router.post("/check-transport")
def samokat_check_transport(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Подписал ли перевозчик заказ на перевозку Самоката этого дня."""
    doc = samokat_transport.our_transport(db, day)
    if doc is None:
        raise HTTPException(status_code=404, detail="На этот день заказа на перевозку Самоката нет")
    try:
        state = saby_actions.check_transport(doc.saby_id)
    except saby_client.SabyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"state": state, "signed": state in saby_client.FINISHED}


@router.get("/shipments/status")
def samokat_shipments_status(user=Depends(auth.get_current_user)):
    return samokat_saby.progress()


@router.get("/shipments/preview")
def samokat_preview(order_no: str, admin=Depends(auth.admin_only)):
    """УПД последней проверки — файлом, как он ушёл бы в СБИС."""
    found = samokat_saby.preview(order_no)
    if found is None:
        raise HTTPException(status_code=404, detail="Нет файла: сначала «Проверить»")
    name, content = found
    return Response(content=content, media_type="application/xml",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name) + ".xml"})
