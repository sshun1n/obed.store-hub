"""Заказы Яндекс.Лавки: список по дате поставки, проверка почты, ручная загрузка файла."""
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from src import auth, barcodes, crud, database, lavka_mail
from src.service import parse_weight
from src.api_v1 import product_title
from src.database import get_db

router = APIRouter(prefix="/lavka", tags=["Яндекс.Лавка"])


def _money(value) -> float:
    return float(Decimal(value or 0).quantize(Decimal("0.01")))


@router.get("/status")
def lavka_status(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    status = lavka_mail.get_status(db)
    last = db.query(func.max(database.LavkaOrder.delivery_date)).scalar()
    status["latest_delivery_date"] = last.isoformat() if last else None
    return status


@router.post("/check")
def lavka_check(user=Depends(auth.get_current_user)):
    """Кнопка «Проверить почту». Менеджеру тоже нужна: заказы приходят утром."""
    if lavka_mail._history["running"]:
        raise HTTPException(status_code=409, detail="Идёт загрузка истории почты, дождитесь её окончания")
    return lavka_mail.check_mail()


@router.post("/history")
def lavka_history(admin=Depends(auth.admin_only)):
    if not lavka_mail.start_history():
        raise HTTPException(status_code=409, detail="Загрузка истории уже идёт")
    return {"started": True}


class ScheduleIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=5, le=720)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekdays: List[int] = Field(min_length=1)
    senders: Optional[str] = Field(default=None, max_length=1000)


@router.put("/schedule")
def lavka_schedule(data: ScheduleIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    if data.start_time >= data.end_time:
        raise HTTPException(status_code=400, detail="Начало должно быть раньше конца")
    if any(day < 0 or day > 6 for day in data.weekdays):
        raise HTTPException(status_code=400, detail="Неверный день недели")
    settings = lavka_mail.get_settings(db)
    settings.enabled = data.enabled
    settings.interval_minutes = data.interval_minutes
    settings.start_time = data.start_time
    settings.end_time = data.end_time
    settings.weekdays = ",".join(str(day) for day in sorted(set(data.weekdays)))
    if data.senders is not None:
        senders = [s.strip() for s in data.senders.replace(";", ",").split(",") if s.strip()]
        if not senders:
            raise HTTPException(status_code=400, detail="Укажите хотя бы одного отправителя")
        if any("@" not in s or '"' in s for s in senders):
            raise HTTPException(status_code=400, detail="Отправители — адреса почты через запятую")
        settings.senders = ", ".join(senders)
    db.commit()
    return lavka_mail.get_status(db)


@router.get("/dates")
def lavka_dates(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Даты поставки с заказами, новые сверху: для выбора дня на странице."""
    rows = (db.query(database.LavkaOrder.delivery_date, func.count(database.LavkaOrder.id),
                     func.sum(database.LavkaOrder.total_quantity), func.sum(database.LavkaOrder.total_gross))
            .group_by(database.LavkaOrder.delivery_date)
            .order_by(database.LavkaOrder.delivery_date.desc())
            .limit(120).all())
    return [{"date": d.isoformat(), "orders": n, "quantity": int(q or 0), "gross": _money(g)} for d, n, q, g in rows]


@router.get("/orders")
def lavka_orders(day: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Заказы на дату поставки: по точкам и сводка по товарам со сверкой по штрих-коду."""
    orders = (db.query(database.LavkaOrder)
              .options(joinedload(database.LavkaOrder.point), selectinload(database.LavkaOrder.lines))
              .filter(database.LavkaOrder.delivery_date == day)
              .all())
    orders.sort(key=lambda o: (o.point.name or "").lower())

    barcodes = {line.barcode for order in orders for line in order.lines if line.barcode}
    products = {}
    if barcodes:
        for product in db.query(database.Product).filter(database.Product.barcode.in_(barcodes)):
            products[product.barcode] = {"id": product.id, "title": product_title(product)}

    items = defaultdict(lambda: {"quantity": 0, "gross": Decimal(0), "points": 0})
    out_orders = []
    for order in orders:
        lines = []
        for line in order.lines:
            item = items[line.barcode]
            item.update(barcode=line.barcode, name=line.name, lavka_id=line.lavka_id, product=products.get(line.barcode))
            item["quantity"] += line.quantity
            item["gross"] += Decimal(line.sum_gross or 0)
            item["points"] += 1
            lines.append({
                "barcode": line.barcode, "name": line.name, "lavka_id": line.lavka_id, "quantity": line.quantity,
                "price_gross": _money(line.price_gross), "sum_gross": _money(line.sum_gross),
                "product": products.get(line.barcode),
            })
        out_orders.append({
            "id": order.id, "order_no": order.order_no,
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "point": {"id": order.point.id, "external_id": order.point.external_id,
                      "name": order.point.name, "address": order.point.address},
            "total_quantity": order.total_quantity, "total_net": _money(order.total_net),
            "total_gross": _money(order.total_gross),
            "revised_at": order.revised_at.isoformat(timespec="seconds") if order.revised_at else None,
            "imported_at": order.imported_at.isoformat(timespec="seconds") if order.imported_at else None,
            "source_filename": order.source_filename,
            "lines": lines,
        })

    summary = sorted(
        ({**v, "gross": _money(v["gross"])} for v in items.values()),
        key=lambda v: v["name"].lower(),
    )
    return {
        "date": day.isoformat(),
        "orders": out_orders,
        "items": summary,
        "totals": {
            "orders": len(out_orders),
            "quantity": sum(o["total_quantity"] for o in out_orders),
            "gross": _money(sum((Decimal(str(o["total_gross"])) for o in out_orders), Decimal(0))),
            "unmatched": sum(1 for v in summary if not v["product"]),
        },
    }


@router.get("/journal")
def lavka_journal(limit: int = 50, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    rows = (db.query(database.LavkaMailFile)
            .order_by(database.LavkaMailFile.id.desc()).limit(max(1, min(limit, 200))).all())
    return [{
        "processed_at": r.processed_at.isoformat(timespec="seconds") if r.processed_at else None,
        "received_at": r.received_at.isoformat(timespec="seconds") if r.received_at else None,
        "sender": r.sender, "subject": r.subject, "filename": r.filename,
        "kind": r.kind, "orders_found": r.orders_found, "note": r.note,
    } for r in rows]


@router.post("/upload")
async def lavka_upload(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Файл заказа вручную: если письмо не дошло или для проверки."""
    content = await file.read(lavka_mail.MAX_ATTACHMENT + 1)
    if len(content) > lavka_mail.MAX_ATTACHMENT:
        raise HTTPException(status_code=413, detail="Файл больше 8 МБ")
    result = lavka_mail.import_attachment(db, content, filename=file.filename or "")
    if result.reports:
        raise HTTPException(status_code=400, detail="Это отчёт Лавки, а не заказ: такие файлы пока не разбираются")
    if result.errors:
        raise HTTPException(status_code=400, detail="; ".join(result.errors))
    if result.skipped:
        raise HTTPException(status_code=400, detail="Это не файл заказа Лавки или такой файл уже загружали")
    return {"created": result.created, "revised": result.revised, "unchanged": result.unchanged}


# --- Привязка штрих-кода Лавки к товару справочника ---

_WORD_RE = re.compile(r"[а-яa-z]+")
_STOP = {"с", "со", "и", "в", "во", "на", "по", "из", "под", "для", "без", "гр", "г"}


def _stems(text: str) -> set:
    """Основы слов: «Цыплёнок гриль с рисом по-мексикански» и «Цыпленок Гриль/Рис пл-мексикански» близки."""
    words = _WORD_RE.findall((text or "").lower().replace("ё", "е"))
    return {w[:5] for w in words if w not in _STOP and len(w) > 1}


def _score(lavka_name: str, product_name: str) -> float:
    a, b = _stems(lavka_name), _stems(product_name)
    if not a or not b:
        return 0.0
    common = len(a & b)
    # Первое слово — название блюда, совпадение по нему важнее
    first_a = next(iter(_WORD_RE.findall(lavka_name.lower().replace("ё", "е"))), "")[:5]
    first_b = next(iter(_WORD_RE.findall(product_name.lower().replace("ё", "е"))), "")[:5]
    return common / len(a | b) + (0.25 if first_a and first_a == first_b else 0)


@router.get("/suggest")
def lavka_suggest(name: str, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Товары справочника, похожие на название из заказа Лавки, лучшие сверху."""
    scored = []
    for product in db.query(database.Product).all():
        title = product_title(product)
        # Только название блюда: в name_from_sheet после веса идёт весь состав, он размывает оценку
        score = _score(name, title)
        if score > 0.15:
            scored.append((score, product, title))
    scored.sort(key=lambda x: -x[0])
    return [{"id": p.id, "title": t, "barcode": p.barcode, "is_hidden": bool(p.is_hidden), "score": round(s, 2)}
            for s, p, t in scored[:8]]


class LinkIn(BaseModel):
    barcode: str = Field(min_length=8, max_length=14)
    product_id: Optional[int] = None
    create_name: Optional[str] = Field(default=None, max_length=200)
    replace: bool = False  # перезаписать прежний код товара или забрать код у другого товара


@router.put("/link")
def lavka_link(data: LinkIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    """Записывает штрих-код Лавки в карточку товара или создаёт товар с ним."""
    barcode = barcodes.normalize(data.barcode)
    if barcode is None:
        raise HTTPException(status_code=400, detail="Штрих-код Лавки не похож на EAN-13")
    holder = db.query(database.Product).filter(database.Product.barcode == barcode).first()

    if data.create_name:
        name = data.create_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название товара")
        if holder:
            raise HTTPException(status_code=409, detail=f"Этот код уже у товара «{product_title(holder)}»")
        if crud.get_product_by_name(db, name):
            raise HTTPException(status_code=409, detail="Товар с таким названием уже есть: выберите его из списка")
        # «Крылышки по-восточному…, 275 г»: вес уходит в своё поле, в названии не нужен
        match = re.search(r",?\s*(\d+)\s*г\.?$", name)
        weight = int(match.group(1)) if match else parse_weight(name)
        if match:
            name = name[:match.start()].strip(" ,")
        if crud.get_product_by_name(db, name):
            raise HTTPException(status_code=409, detail="Товар с таким названием уже есть: выберите его из списка")
        product = crud.create_product(db, name, created_by=admin.full_name or admin.login, category_ids=[],
                                      barcode=barcode, weight_grams=weight)
        return {"product": {"id": product.id, "title": product_title(product)}, "created": True}

    product = crud.get_product(db, data.product_id) if data.product_id else None
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if holder and holder.id == product.id:
        return {"product": {"id": product.id, "title": product_title(product)}, "created": False}
    conflicts = []
    if holder:
        conflicts.append(f"код уже у товара «{product_title(holder)}»")
    if product.barcode:
        conflicts.append(f"у «{product_title(product)}» уже есть код {product.barcode}")
    if conflicts and not data.replace:
        text = "; ".join(conflicts)
        raise HTTPException(status_code=409, detail=text[:1].upper() + text[1:])
    if holder:
        holder.barcode = None
        db.flush()
    product.barcode = barcode
    db.commit()
    return {"product": {"id": product.id, "title": product_title(product)}, "created": False}
