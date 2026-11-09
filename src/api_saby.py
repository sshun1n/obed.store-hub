"""СБИС по дню поставки: что уже сделано в кабинете по нашим точкам. Только чтение.

/api/v1/saby/status, /refresh (фоновое чтение кабинета), /day (сводка из кэша),
/registry (загрузка реестра 1С, админ).
"""
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from src import auth, database, saby_client, saby_sync, upd_registry
from src.api_lavka import format_range, short_address
from src.database import get_db

router = APIRouter(prefix="/saby", tags=["СБИС"])

MAX_REGISTRY = 5 * 1024 * 1024


def _money(value) -> Optional[float]:
    return None if value is None else float(Decimal(value).quantize(Decimal("0.01")))


@router.get("/status")
def saby_status(user=Depends(auth.get_current_user)):
    return {"configured": saby_client.configured(), "progress": saby_sync.progress()}


@router.post("/refresh")
def saby_refresh(day: date, user=Depends(auth.get_current_user)):
    """Прочитать кабинет за день. Только чтение, поэтому кнопка доступна всем."""
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    if not saby_sync.start(day):
        raise HTTPException(status_code=409, detail="Чтение СБИС уже идёт, дождитесь его окончания")
    return {"started": True}


def _doc(row: Optional[database.SabyDoc]) -> Optional[Dict]:
    if row is None:
        return None
    kinds = set((row.attachments or "").split(","))
    return {"id": row.saby_id, "number": row.number, "date": row.doc_date.isoformat() if row.doc_date else None,
            "state": row.state, "link": row.link, "amount": _money(row.amount),
            "accepted": "АктПриемки" in kinds, "upd_sent": "УпдСчфДоп" in kinds,
            "checked_at": row.checked_at.isoformat(timespec="seconds") if row.checked_at else None}


@router.get("/day")
def saby_day(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    orders = (db.query(database.LavkaOrder).options(joinedload(database.LavkaOrder.point))
              .filter(database.LavkaOrder.delivery_date == day).all())
    docs = db.query(database.SabyDoc).filter(database.SabyDoc.delivery_date == day).all()
    by_kind: Dict[str, Dict[str, database.SabyDoc]] = {"order": {}, "shipment": {}, "waybill": {}}
    for d in docs:
        if d.kind in by_kind and d.order_no and d.ours:
            by_kind[d.kind][d.order_no] = d
    registry = db.query(database.UpdRow).filter(database.UpdRow.day == day).order_by(database.UpdRow.number).all()
    free = list(registry)

    points = []
    for o in sorted(orders, key=lambda x: (x.point.address or x.point.name or "")):
        shipment = by_kind["shipment"].get(o.order_no)
        # Строку реестра ищем сначала по номеру реализации — он и есть номер из 1С,
        # затем по адресу, как в ka-webapp. Сумма — только проверка
        row = None
        if shipment and shipment.number:
            row = next((r for r in free if r.number == shipment.number), None)
        if row is None:
            row = next((r for r in free if upd_registry.same_address(o.point.address or "", r.address or "")), None)
        if row is not None:
            free.remove(row)
        gross = Decimal(o.total_gross or 0)
        reg = None
        if row is not None:
            reg = {"number": row.number, "amount": _money(row.amount),
                   "sum_ok": abs(Decimal(row.amount) - gross) < Decimal("0.05"),
                   "number_ok": (shipment.number == row.number) if shipment and shipment.number else None}
        points.append({
            "order_no": o.order_no, "point": o.point.name, "address": o.point.address,
            "address_short": short_address(o.point.address or ""), "gross": _money(gross),
            "quantity": o.total_quantity,
            "order": _doc(by_kind["order"].get(o.order_no)),
            "shipment": _doc(shipment),
            "waybill": _doc(by_kind["waybill"].get(o.order_no)),
            "registry": reg,
        })

    ours = {o.order_no for o in orders}
    loose = [d for d in docs if d.kind == "waybill" and d.ours and (d.order_no or "") not in ours]
    transport = [{**_doc(d), "party": d.party, "cargo": d.cargo,
                  "waybills": sum(1 for w in docs if w.kind == "waybill" and w.parent_id == d.saby_id)}
                 for d in docs if d.kind == "transport" and d.ours]
    checked = max((d.checked_at for d in docs), default=None)
    n = len(points)
    return {
        "day": day.isoformat(), "label": format_range(day, day),
        "configured": saby_client.configured(),
        "checked_at": checked.isoformat(timespec="seconds") if checked else None,
        "points": points,
        "transport": transport,
        "loose_waybills": [{**_doc(d), "address": d.address, "order_no": d.order_no} for d in loose],
        "registry_free": [{"number": r.number, "amount": _money(r.amount), "address": r.address} for r in free],
        "registry_rows": len(registry),
        "stats": {
            "points": n,
            "orders": sum(1 for p in points if p["order"]),
            "shipments": sum(1 for p in points if p["shipment"]),
            "waybills": sum(1 for p in points if p["waybill"]),
            "accepted": sum(1 for p in points if p["shipment"] and p["shipment"]["accepted"]),
            "upd_sent": sum(1 for p in points if p["shipment"] and p["shipment"]["upd_sent"]),
            "registry": sum(1 for p in points if p["registry"]),
        },
    }


@router.post("/registry")
async def saby_registry(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Реестр реализаций из 1С. Заменяет строки целиком по датам, которые есть в файле."""
    content = await file.read(MAX_REGISTRY + 1)
    if len(content) > MAX_REGISTRY:
        raise HTTPException(status_code=400, detail="Файл больше 5 МБ — это не реестр")
    try:
        rows = upd_registry.parse_registry(content)
    except upd_registry.RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    days = sorted({r.day for r in rows})
    db.query(database.UpdRow).filter(database.UpdRow.day.in_(days)).delete(synchronize_session=False)
    for r in rows:
        db.add(database.UpdRow(day=r.day, number=r.number, amount=r.amount, address=r.address[:300] or None))
    db.commit()
    return {"rows": len(rows), "days": [d.isoformat() for d in days],
            "label": format_range(days[0], days[-1])}
