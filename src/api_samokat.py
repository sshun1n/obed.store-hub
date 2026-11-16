"""Заказы «Самоката» (ООО «Умный ритейл»): сводка и заказы по точкам за период.

Письма читает тот же опрос ящика, что и у Лавки (`lavka_mail`): один ящик,
один курсор, один журнал. Поэтому здесь нет своей проверки почты — кнопка
на странице зовёт общую. Ответы устроены как у `/lavka/summary` и `/lavka/orders`,
чтобы страница работала тем же скриптом.
"""
from datetime import date
from typing import Dict, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from src import auth, database, lavka_mail, saby_client, samokat_saby, upd_registry
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


# --- СБИС Самоката: день поставки по точкам ---

@router.get("/day")
def samokat_day(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Точки дня из базы (без похода в СБИС): заказ УР-, строка реестра 1С по адресу, наша реализация."""
    rows = (db.query(Order).options(joinedload(Order.point))
            .filter(Order.delivery_date == day).all())
    rows.sort(key=lambda o: short_address(o.point.address if o.point else "").lower())
    registry = db.query(database.UpdRow).filter(database.UpdRow.day == day).order_by(database.UpdRow.number).all()
    used, points = set(), []
    for o in rows:
        address = o.point.address if o.point else ""
        row = next((r for r in registry if r.id not in used and upd_registry.same_address(address, r.address or "")), None)
        if row is not None:
            used.add(row.id)
        gross = _money(o.total_gross)
        points.append({
            "id": o.id, "order_no": o.order_no or "", "address": address, "address_short": short_address(address),
            "gln": o.point.gln if o.point else None, "quantity": int(o.total_quantity or 0), "gross": gross,
            "registry": {"number": row.number, "amount": _money(row.amount), "ok": abs(_money(row.amount) - gross) < 1}
            if row is not None else None,
            "shipment": {"number": o.shipment_number, "state": o.shipment_state or "Черновик"} if o.shipment_number else None,
        })
    return {
        "day": day.isoformat(), "is_admin": user.role == "admin", "registry_rows": len(registry),
        "registry_free": [{"number": r.number, "address": r.address} for r in registry if r.id not in used],
        "stats": {"points": len(points), "registry": sum(1 for p in points if p["registry"]),
                  "orders": sum(1 for p in points if p["order_no"]),
                  "shipments": sum(1 for p in points if p["shipment"])},
        "points": points,
    }


# --- СБИС: черновики реализаций с УПД ---

class DraftsIn(BaseModel):
    day: date
    dry_run: bool = True
    orders: List[str] = []      # номера УР-; пусто — все точки дня


@router.post("/shipments")
def samokat_shipments(data: DraftsIn, admin=Depends(auth.admin_only)):
    """Проверка (dry_run) или создание черновиков реализаций. Ничего не отправляет. Только админ."""
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    if not samokat_saby.start(data.day, admin.login, data.dry_run, data.orders or None):
        raise HTTPException(status_code=409, detail="Другая операция с СБИС ещё идёт, дождитесь её окончания")
    return {"started": True}


@router.get("/shipments/status")
def samokat_shipments_status(admin=Depends(auth.admin_only)):
    return samokat_saby.progress()


@router.get("/shipments/preview")
def samokat_preview(order_no: str, admin=Depends(auth.admin_only)):
    """УПД последней проверки — файлом, как он ушёл бы в СБИС."""
    found = samokat_saby.preview(order_no)
    if found is None:
        raise HTTPException(status_code=404, detail="Нет файла: сначала «Проверить без создания»")
    name, content = found
    return Response(content=content, media_type="application/xml",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.xml"})
