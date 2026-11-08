"""Заказы Яндекс.Лавки: список по дате поставки, проверка почты, ручная загрузка файла."""
import re
from pathlib import Path
from urllib.parse import quote
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

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
    """Даты поставки с заказами: точки в календаре выбора периода."""
    rows = (db.query(database.LavkaOrder.delivery_date, func.count(database.LavkaOrder.id),
                     func.sum(database.LavkaOrder.total_quantity))
            .group_by(database.LavkaOrder.delivery_date)
            .order_by(database.LavkaOrder.delivery_date).all())
    return [{"date": d.isoformat(), "orders": n, "quantity": int(q or 0)} for d, n, q in rows]


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]


def format_range(start: date, end: date) -> str:
    if start == end:
        return f"{start.day} {MONTHS_GEN[start.month - 1]} {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    if start.year == end.year:
        return f"{start.day} {MONTHS_GEN[start.month - 1]} – {end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    return f"{format_range(start, start)} – {format_range(end, end)}"


def compare_range(start: date, end: date, last: Optional[date]) -> Tuple[date, date, str]:
    """С чем сравнивать. Перенесено из ka-webapp.

    День — с тем же днём недели неделей раньше: у заказов недельный ритм, и
    вторник против понедельника показывал бы рост на пустом месте.
    Период — с таким же по длине отрезком прямо перед ним. Текущий период
    обычно не дожит до конца, поэтому от прошлого берём столько же дней,
    сколько уже есть данных в текущем, иначе всегда было бы «падение».
    """
    if start == end:
        day = start - timedelta(days=7)
        return day, day, f"тот же день недели неделей раньше, {format_range(day, day)}"
    span = (end - start).days + 1
    cmp_from, cmp_to = start - timedelta(days=span), start - timedelta(days=1)
    # Целый календарный месяц сравниваем с прошлым календарным месяцем
    if start.day == 1 and (end + timedelta(days=1)).day == 1 and end.month == start.month:
        cmp_to = start - timedelta(days=1)
        cmp_from = cmp_to.replace(day=1)
    label = format_range(cmp_from, cmp_to)
    if last is not None and start <= last < end:
        cut = min(cmp_to, cmp_from + (last - start))
        if cut < cmp_to:
            cmp_to = cut
            label = format_range(cmp_from, cmp_to) + ": столько же дней, сколько прошло в текущем"
    return cmp_from, cmp_to, label


def _totals(db: Session, start: date, end: date) -> Dict:
    order, line = database.LavkaOrder, database.LavkaOrderLine
    row = (db.query(func.count(func.distinct(order.id)), func.count(func.distinct(order.point_id)),
                    func.count(func.distinct(line.barcode)), func.coalesce(func.sum(line.quantity), 0),
                    func.coalesce(func.sum(line.sum_gross), 0))
           .join(line, line.order_id == order.id)
           .filter(order.delivery_date.between(start, end)).one())
    return {"orders": row[0], "points": row[1], "items": row[2], "quantity": int(row[3] or 0), "gross": _money(row[4])}


def _items(db: Session, start: date, end: date) -> List[Dict]:
    """Сводная номенклатура за период. Группировка по штрих-коду: Лавка может
    слегка поменять название, и позиция разъехалась бы на две строки."""
    order, line = database.LavkaOrder, database.LavkaOrderLine
    rows = (db.query(line.barcode, func.max(line.name), func.sum(line.quantity), func.sum(line.sum_gross),
                     func.count(func.distinct(order.point_id)), func.max(line.lavka_id))
            .join(order, line.order_id == order.id)
            .filter(order.delivery_date.between(start, end))
            .group_by(line.barcode).all())
    return [{"barcode": b, "name": n, "quantity": int(q or 0), "gross": _money(g), "points": p, "lavka_id": l}
            for b, n, q, g, p, l in rows]


def sort_key(name: str, quantity: int) -> tuple:
    """«По виду изделия», как в ka-webapp: вид — первое слово, внутри вида больше сверху.
    «ё» как «е», иначе она уезжает в конец алфавита."""
    cleaned = (name or "").strip().lower().replace("ё", "е")
    head = cleaned.split()[0].strip(".,;:«»\"'") if cleaned.split() else ""
    return head, -quantity, cleaned


@router.get("/summary")
def lavka_summary(start: date, end: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Заказы за день или период: итоги, сравнение и номенклатура со сверкой по штрих-коду."""
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        raise HTTPException(status_code=400, detail="Период длиннее года")
    last = db.query(func.max(database.LavkaOrder.delivery_date)).scalar()
    cmp_from, cmp_to, cmp_label = compare_range(start, end, last)

    items = _items(db, start, end)
    previous = {i["barcode"]: i["quantity"] for i in _items(db, cmp_from, cmp_to)}
    barcodes_now = {i["barcode"] for i in items if i["barcode"]}
    products = {}
    if barcodes_now:
        for product in db.query(database.Product).filter(database.Product.barcode.in_(barcodes_now)):
            products[product.barcode] = {"id": product.id, "title": product_title(product)}
    for item in items:
        was = previous.get(item["barcode"])
        item["was"] = was
        item["delta"] = round((item["quantity"] - was) / was * 100, 1) if was else None
        item["product"] = products.get(item["barcode"])
    items.sort(key=lambda i: sort_key(i["name"], i["quantity"]))

    revised = (db.query(func.count(database.LavkaOrder.id))
               .filter(database.LavkaOrder.delivery_date.between(start, end),
                       database.LavkaOrder.revised_at.isnot(None)).scalar())
    return {
        "start": start.isoformat(), "end": end.isoformat(), "label": format_range(start, end),
        "compare": {"start": cmp_from.isoformat(), "end": cmp_to.isoformat(), "label": cmp_label,
                    "totals": _totals(db, cmp_from, cmp_to)},
        "totals": _totals(db, start, end),
        "unmatched": sum(1 for i in items if not i["product"]),
        "revised": revised,
        "items": items,
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


# --- Лист номенклатуры (как в ka-webapp: HTML для предпросмотра, PDF через WeasyPrint) ---

SHEET_SORTS = [
    ("kind", "По виду изделия"),
    ("qty_desc", "По количеству, больше сверху"),
    ("qty_asc", "По количеству, меньше сверху"),
    ("alpha", "По алфавиту"),
    ("sum_desc", "По стоимости, дороже сверху"),
    ("sum_asc", "По стоимости, дешевле сверху"),
]
DEFAULT_SORT = "kind"

# Геометрия половины листа в мм, из ka-webapp: A4 альбомная — 210 мм, поля по 7 мм,
# шапка с логотипом и датой — 17 мм. Строка при кегле 1 pt замерена по готовой вёрстке
SHEET_ROWS_MM = 210 - 7 * 2 - 17 - 1
HEADER_ROWS = 1
ROW_MM_PER_PT = 0.656
ROW_MM_CONST = 0.18
MIN_FONT_PT, MAX_FONT_PT = 5.5, 13.0

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_sheet_env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
                         autoescape=select_autoescape(["html"]))


# Ширина колонки названия в мм: половина листа (297 − 2×8 полей − 2×7 у линии реза) / 2
# минус клетка под галочку (2,2 em) и колонка количества (13 мм)
HALF_WIDTH_MM = (297 - 16 - 14) / 2
NUM_COL_MM = 13
AVG_CHAR_EM = 0.5        # средняя ширина буквы Arial в кириллице, в долях кегля
PT_MM = 0.3528
LINE_MM_PER_PT = 1.2 * PT_MM   # каждая лишняя строка переноса


def _lines(name: str, pt: float) -> int:
    width = HALF_WIDTH_MM - NUM_COL_MM - 2.9 * pt * PT_MM
    per_line = max(10, int(width / (AVG_CHAR_EM * pt * PT_MM)))
    return max(1, -(-len(name or "") // per_line))


def fit_font_pt(names: List[str]) -> float:
    """Кегль, при котором список занимает половину листа почти целиком.

    В ka-webapp строка считалась одной: названия там короче 46 знаков. У Обеда
    названия до 80 знаков и переносятся, поэтому учитываем и строки переноса,
    иначе большой заказ уезжал бы на вторую страницу.
    """
    if not names:
        return MAX_FONT_PT
    pt = MAX_FONT_PT
    while pt > MIN_FONT_PT:
        height = (len(names) + HEADER_ROWS) * (pt * ROW_MM_PER_PT + ROW_MM_CONST)
        height += sum(_lines(n, pt) - 1 for n in names) * pt * LINE_MM_PER_PT
        if height <= SHEET_ROWS_MM:
            break
        pt = round(pt - 0.1, 1)
    return max(pt, MIN_FONT_PT)


def _alpha(name: str) -> str:
    return (name or "").strip().lower().replace("ё", "е")


def sort_items(items: List[Dict], mode: str) -> List[Dict]:
    if mode == "qty_desc":
        return sorted(items, key=lambda i: (-i["quantity"], _alpha(i["name"])))
    if mode == "qty_asc":
        return sorted(items, key=lambda i: (i["quantity"], _alpha(i["name"])))
    if mode == "alpha":
        return sorted(items, key=lambda i: _alpha(i["name"]))
    if mode == "sum_desc":
        return sorted(items, key=lambda i: (-i["gross"], _alpha(i["name"])))
    if mode == "sum_asc":
        return sorted(items, key=lambda i: (i["gross"], _alpha(i["name"])))
    return sorted(items, key=lambda i: sort_key(i["name"], i["quantity"]))


def _sheet_label(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d.%m.%y")
    if start.year == end.year:
        return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%y')}"
    return f"{start.strftime('%d.%m.%y')}–{end.strftime('%d.%m.%y')}"


def _render_sheet(db: Session, start: date, end: date, left: str, right: str, mode: str, logo: str) -> Tuple[str, str]:
    if end < start:
        start, end = end, start
    modes = dict(SHEET_SORTS)
    left = left if left in modes else DEFAULT_SORT
    right = right if right in modes else left
    items = _items(db, start, end)
    label = _sheet_label(start, end)
    html = _sheet_env.get_template("lavka_sheet.html").render(
        mode=mode, logo_src=logo, day_label=label,
        items_left=sort_items(items, left), items_right=sort_items(items, right),
        font_pt=fit_font_pt([i["name"] for i in items]), total_quantity=sum(i["quantity"] for i in items),
    )
    return html, label


@router.get("/sheet/sorts")
def lavka_sheet_sorts(user=Depends(auth.get_current_user)):
    return [{"value": v, "label": l} for v, l in SHEET_SORTS]


@router.get("/sheet.html", response_class=HTMLResponse)
def lavka_sheet_html(start: date, end: date, left: str = "", right: str = "",
                     db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Предпросмотр листа: та же вёрстка, что уходит в PDF."""
    html, _ = _render_sheet(db, start, end, left, right, "preview", "/static/img/obed-logo.svg")
    return HTMLResponse(html)


@router.get("/sheet.pdf")
def lavka_sheet_pdf(start: date, end: date, left: str = "", right: str = "",
                    db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    # Логотип путём к файлу: по http WeasyPrint постучался бы в это же приложение
    html, label = _render_sheet(db, start, end, left, right, "pdf", (STATIC_DIR / "img" / "obed-logo.svg").as_uri())
    try:
        from weasyprint import HTML  # тяжёлая библиотека, без Pango падает с OSError
    except (ImportError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"Не удалось собрать PDF: на сервере нет библиотек вёрстки ({exc.__class__.__name__})")
    pdf = HTML(string=html, base_url=str(STATIC_DIR)).write_pdf()
    name = f"Яндекс {label} номенклатура.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})
