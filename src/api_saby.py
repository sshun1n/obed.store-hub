"""СБИС по дню поставки: что сделано в кабинете по нашим точкам и действия по следующему шагу.

/api/v1/saby/status, /refresh (чтение кабинета), /day (сводка из кэша),
/registry (реестр 1С), /settings, /action (создать и провести, фоном),
/sign/prepare и /sign/send (подпись в браузере), /check-transport.
Читать могут все, менять — только админ.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from src import auth, database, saby_actions, saby_client, saby_sync, sheet_sync, upd_registry
from src.api_lavka import format_range, short_address
from src.database import get_db

router = APIRouter(prefix="/saby", tags=["СБИС"])

MAX_REGISTRY = 5 * 1024 * 1024


def _local(moment: Optional[datetime]) -> Optional[str]:
    """Время из базы (UTC) — в местное, как его видит человек."""
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone(sheet_sync.LOCAL_TZ).replace(tzinfo=None).isoformat(timespec="minutes")


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
    settings = saby_actions.get_settings(db)
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
    ours_transport = next((d for d in sorted(docs, key=lambda x: -x.id)
                           if d.kind == "transport" and d.ours and d.state != "Удалён"), None)
    waiting = ours_transport is not None and ours_transport in saby_actions.waiting_transports(db)
    carriers = [{"id": c.id, "name": c.name} for c in db.query(database.Carrier)
                .filter(database.Carrier.is_active.is_(True)).order_by(database.Carrier.name)]
    draft_waybills = [d.order_no for d in docs if d.kind == "waybill" and d.ours and d.state == saby_actions.DRAFT]
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
        "is_admin": getattr(user, "role", "") == "admin",
        "settings": {"hide_split_shipments": bool(settings.hide_split_shipments),
                     "hide_split_transport": bool(settings.hide_split_transport),
                     "hide_split_waybills": bool(settings.hide_split_waybills),
                     "poll_minutes": settings.poll_minutes or 20},
        "carriers": carriers,
        "last_carrier_id": settings.last_carrier_id,
        "our_transport": None if ours_transport is None else {
            **_doc(ours_transport), "party": ours_transport.party, "carrier_id": ours_transport.carrier_id,
            "created_by_app": bool(ours_transport.created_by_app),
            "draft": ours_transport.state == saby_actions.DRAFT,
            "signed_by_carrier": ours_transport.state in saby_client.FINISHED,
            "waiting": waiting,
            "next_check_at": _local(ours_transport.next_check_at) if waiting else None,
        },
        "draft_waybills": draft_waybills,
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


# --- Настройки ---

class SettingsIn(BaseModel):
    hide_split_shipments: bool = False
    hide_split_transport: bool = False
    hide_split_waybills: bool = False
    poll_minutes: int = Field(20, ge=5, le=240)
    pickup_address: str = Field(..., min_length=5, max_length=300)
    pickup_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    departure_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    unload_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    cargo_name: str = Field(..., min_length=2, max_length=150)
    cargo_state: str = Field(..., min_length=2, max_length=200)
    place_weight_kg: float = Field(..., gt=0, le=100)
    place_volume_m3: float = Field(..., gt=0, le=5)
    bag_length_cm: int = Field(..., gt=0, le=300)
    bag_width_cm: int = Field(..., gt=0, le=300)
    bag_height_cm: int = Field(..., gt=0, le=300)
    temp_min: int = Field(..., ge=-30, le=30)
    temp_max: int = Field(..., ge=-30, le=30)
    sanitary_norms: str = Field(..., max_length=150)
    vehicle_type: str = Field(..., max_length=60)
    body_type: str = Field(..., max_length=60)
    loading_type: str = Field(..., max_length=60)
    capacity_t: float = Field(..., gt=0, le=60)
    volume_m3: float = Field(..., gt=0, le=200)


@router.get("/settings")
def saby_settings_get(db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    row = saby_actions.get_settings(db)
    return {key: getattr(row, key) for key in SettingsIn.model_fields}


@router.put("/settings")
def saby_settings_put(data: SettingsIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    if data.temp_min > data.temp_max:
        raise HTTPException(status_code=400, detail="Нижняя граница температуры выше верхней")
    row = saby_actions.get_settings(db)
    for key, value in data.model_dump().items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    db.commit()
    return {"ok": True}


# --- Действия ---

LABELS = {
    "create_shipments": "Создание реализаций",
    "process_shipments": "Проведение реализаций",
    "create_transport": "Создание заказа на перевозку",
    "create_waybills": "Создание ЭТрН",
}


class ActionIn(BaseModel):
    day: date
    action: str
    orders: List[str] = []            # пусто — все точки дня
    carrier_id: Optional[int] = None


@router.post("/action")
def saby_action(data: ActionIn, admin=Depends(auth.admin_only)):
    """Действие в кабинете — фоном, с ходом выполнения в /status. Только админ, только по кнопке."""
    if data.action not in LABELS:
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    if not saby_client.configured():
        raise HTTPException(status_code=400, detail="Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    who = admin.login
    if data.action == "create_transport":
        if not data.carrier_id:
            raise HTTPException(status_code=400, detail="Выберите перевозчика")
        task = lambda: saby_actions.create_transport(data.day, data.orders, data.carrier_id, who)
    elif data.action == "create_shipments":
        task = lambda: saby_actions.create_shipments(data.day, data.orders, who)
    elif data.action == "process_shipments":
        task = lambda: saby_actions.process_shipments(data.day, data.orders, who)
    else:
        task = lambda: saby_actions.create_waybills(data.day, data.orders, who)
    if not saby_sync.start(data.day, LABELS[data.action], task):
        raise HTTPException(status_code=409, detail="Другая операция с СБИС ещё идёт, дождитесь её окончания")
    return {"started": True}


class SignPrepareIn(BaseModel):
    day: date
    kind: str                          # transport | waybill
    orders: List[str] = []
    thumbprint: str = Field(..., min_length=20, max_length=80)


class Signature(BaseModel):
    id: str
    signature: str


class SignSendIn(BaseModel):
    token: str
    signatures: List[Signature]


@router.post("/sign/prepare")
def saby_sign_prepare(data: SignPrepareIn, admin=Depends(auth.admin_only)):
    if saby_sync.progress()["running"]:
        raise HTTPException(status_code=409, detail="Идёт другая операция с СБИС, дождитесь её окончания")
    try:
        return saby_actions.prepare_signing(data.day, data.kind, data.orders, data.thumbprint)
    except saby_client.SabyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sign/send")
def saby_sign_send(data: SignSendIn, admin=Depends(auth.admin_only)):
    try:
        result = saby_actions.send_signing(data.token, {s.id: s.signature for s in data.signatures})
    except saby_client.SabyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Перечитываем день фоном: после отправки состояния меняются
    saby_sync.start(result["day"], "Чтение СБИС")
    return {"sent": result["sent"], "errors": result["errors"]}


@router.post("/check-transport")
def saby_check_transport(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Кнопка «Проверить»: подписал ли перевозчик заказ на перевозку этого дня."""
    doc = saby_actions.our_transport(db, day)
    if doc is None:
        raise HTTPException(status_code=404, detail="На этот день нашего заказа на перевозку нет")
    try:
        state = saby_actions.check_transport(doc.saby_id)
    except saby_client.SabyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"state": state, "signed": state in saby_client.FINISHED}
