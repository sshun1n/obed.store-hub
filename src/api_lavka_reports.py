"""Отчёты Яндекс.Лавки (отзывы, списания) и прогноз заказов: /api/v1/lavka/reports, /writeoffs, /forecast."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src import auth, database, lavka_forecast, lavka_reports, sheet_sync
from src.api_lavka import format_range
from src.api_v1 import product_title
from src.database import get_db

router = APIRouter(prefix="/lavka", tags=["Яндекс.Лавка: отчёты и прогноз"])

FEEDBACK_DAYS = 28   # плитки лайков и дизлайков — за четыре недели до последнего отзыва


def _round1(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 1)


def _catalog(db: Session) -> Dict[str, Dict]:
    """Позиция Лавки (Item ID) → штрих-код и товар справочника. Связь — через строки заказов."""
    line = database.LavkaOrderLine
    by_lavka = {}
    for lavka_id, barcode in (db.query(line.lavka_id, func.max(line.barcode))
                              .filter(line.lavka_id.isnot(None)).group_by(line.lavka_id)):
        by_lavka[lavka_id] = barcode
    titles = {p.barcode: product_title(p) for p in
              db.query(database.Product).filter(database.Product.barcode.in_(set(by_lavka.values())))} if by_lavka else {}
    return {lid: {"barcode": bc, "product": titles.get(bc)} for lid, bc in by_lavka.items()}


def _week_label(monday: date) -> str:
    return format_range(monday, monday + timedelta(days=6))


@router.get("/reports")
def lavka_reports_overview(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Страница отчётов: плитки, отзывы целиком и недели, за которые есть списания."""
    metric = database.LavkaMetric
    weeks = [w for (w,) in db.query(metric.week_start).distinct().order_by(metric.week_start)]
    last_report = db.query(func.max(metric.report_date)).scalar()
    today = sheet_sync.local_now().date()

    writeoff = None
    if weeks:
        def week_pct(week):
            cells = db.query(metric.writeoffs_pct, metric.sales_qty).filter(metric.week_start == week).all()
            return lavka_reports.weighted_writeoff(cells)
        now = week_pct(weeks[-1])
        was = week_pct(weeks[-2]) if len(weeks) > 1 else None
        writeoff = {"week": weeks[-1].isoformat(), "label": _week_label(weeks[-1]),
                    "pct": _round1(now), "was": _round1(was),
                    "was_label": _week_label(weeks[-2]) if len(weeks) > 1 else None}

    catalog = _catalog(db)
    rows = db.query(database.LavkaFeedback).order_by(database.LavkaFeedback.feedback_date.desc(),
                                                     database.LavkaFeedback.id.desc()).all()
    feedback = [{
        "date": r.feedback_date.isoformat(), "order_date": r.order_date.isoformat() if r.order_date else None,
        "name": r.name, "product": (catalog.get(r.lavka_id) or {}).get("product"),
        "liked": bool(r.liked), "comment": r.comment or "",
    } for r in rows]

    counts = None
    if rows:
        newest = rows[0].feedback_date
        since = newest - timedelta(days=FEEDBACK_DAYS - 1)
        recent = [r for r in rows if r.feedback_date >= since]
        counts = {"start": since.isoformat(), "end": newest.isoformat(), "label": format_range(since, newest),
                  "likes": sum(1 for r in recent if r.liked), "dislikes": sum(1 for r in recent if not r.liked)}

    return {
        "last_report": last_report.isoformat() if last_report else None,
        "stale": bool(last_report and (today - last_report).days > lavka_reports.STALE_AFTER_DAYS),
        "stale_days": (today - last_report).days if last_report else None,
        "weeks": [w.isoformat() for w in weeks],
        "writeoff": writeoff,
        "feedback_counts": counts,
        "feedback": feedback,
    }


@router.get("/writeoffs")
def lavka_writeoffs(start: date, end: date, db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    """Средний процент списаний по каждой позиции за недели, попавшие в период.

    Лавка считает списания неделями, поэтому период расширяется до целых недель:
    берём недели, понедельник которых лежит между понедельниками начала и конца.
    """
    if end < start:
        start, end = end, start
    first, last = lavka_reports.monday(start), lavka_reports.monday(end)
    metric = database.LavkaMetric
    rows = (db.query(metric).filter(metric.week_start.between(first, last))
            .order_by(metric.week_start).all())
    catalog = _catalog(db)
    grouped: Dict[str, List] = defaultdict(list)
    for r in rows:
        grouped[r.lavka_id].append(r)

    items = []
    for lavka_id, cells in grouped.items():
        pct = lavka_reports.weighted_writeoff([(c.writeoffs_pct, c.sales_qty) for c in cells])
        info = catalog.get(lavka_id) or {}
        items.append({
            "lavka_id": lavka_id, "name": cells[-1].name, "product": info.get("product"),
            "pct": _round1(pct), "sold": sum(c.sales_qty or 0 for c in cells),
            "weeks": sum(1 for c in cells if c.writeoffs_pct is not None),
            "by_week": [{"week": c.week_start.isoformat(), "pct": _round1(c.writeoffs_pct), "sold": c.sales_qty or 0}
                        for c in cells],
        })
    # Сверху то, что списывается сильнее; позиции без процента — в конце
    items.sort(key=lambda i: (i["pct"] is None, -(i["pct"] or 0), i["name"]))
    total = lavka_reports.weighted_writeoff([(r.writeoffs_pct, r.sales_qty) for r in rows])
    weeks = sorted({r.week_start for r in rows})
    return {
        "start": first.isoformat(), "end": (last + timedelta(days=6)).isoformat(),
        "label": format_range(first, last + timedelta(days=6)),
        "weeks": [w.isoformat() for w in weeks],
        "total_pct": _round1(total), "sold": sum(r.sales_qty or 0 for r in rows),
        "items": items,
    }


def _order_series(db: Session):
    """Штуки по дням поставки: всего и по каждому штрих-коду; последние цены и названия."""
    order, line = database.LavkaOrder, database.LavkaOrderLine
    totals: Dict[date, float] = defaultdict(float)
    items: Dict[str, Dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for day, barcode, qty in (db.query(order.delivery_date, line.barcode, func.sum(line.quantity))
                              .join(line, line.order_id == order.id)
                              .group_by(order.delivery_date, line.barcode)):
        totals[day] += float(qty or 0)
        items[barcode][day] += float(qty or 0)
    # Последняя цена и название позиции — из самого свежего дня, где она была
    meta: Dict[str, Dict] = {}
    for day, barcode, name, price in (db.query(order.delivery_date, line.barcode, line.name, line.price_gross)
                                      .join(line, line.order_id == order.id)
                                      .order_by(order.delivery_date.desc(), line.id.desc())):
        if barcode not in meta:
            meta[barcode] = {"name": name, "price": float(price or 0)}
    return dict(totals), {k: dict(v) for k, v in items.items()}, meta


def _day(d: date) -> Dict:
    return {"date": d.isoformat(), "weekday": lavka_forecast.WEEKDAYS[d.weekday()],
            "weekday_full": lavka_forecast.WEEKDAYS_FULL[d.weekday()], "label": d.strftime("%d.%m")}


@router.get("/forecast")
def lavka_forecast_page(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    totals, items, meta = _order_series(db)
    today = sheet_sync.local_now().date()
    result = lavka_forecast.build(totals, items, today)
    if not result.get("ready"):
        return {"ready": False, "history_days": len(totals)}

    titles = {p.barcode: product_title(p) for p in
              db.query(database.Product).filter(database.Product.barcode.in_(list(meta)))} if meta else {}
    forecast = result["forecast"]
    target_days = [f.day for f in forecast]

    rows = []
    for barcode, by_day in result["items"].items():
        values = [by_day.get(d, 0.0) for d in target_days]
        total = sum(values)
        if total < 0.5:
            continue
        price = meta.get(barcode, {}).get("price", 0.0)
        last_window = result["windows"][0]
        was = sum(v for d, v in items.get(barcode, {}).items() if last_window["start"] <= d <= last_window["end"])
        rows.append({"barcode": barcode, "name": meta.get(barcode, {}).get("name", barcode),
                     "product": titles.get(barcode), "by_day": [round(v, 1) for v in values],
                     "total": round(total, 1), "price": price, "gross": round(total * price, 2), "was": was})
    rows.sort(key=lambda r: -r["total"])

    week_total = sum(f.value for f in forecast)
    windows = result["windows"]
    avg_weeks = sum(w["quantity"] for w in windows) / len(windows) if windows else 0
    gross = sum(r["gross"] for r in rows)
    # Рубли прошлых недель — по тем же последним ценам: иначе тренд смешал бы спрос и подорожание
    gross_was = sum(meta.get(bc, {}).get("price", 0.0) * v for bc, by_day in items.items()
                    for d, v in by_day.items() if windows[0]["start"] <= d <= windows[0]["end"])

    # История для графика: последние восемь недель фактических заказов
    days = sorted(totals)
    since = result["last"] - timedelta(days=8 * 7 - 1)
    history = [{**_day(d), "value": totals[d]} for d in days if d >= since]

    lw = result["last_week"]
    lw_items = []
    for barcode, v in lw["items"].items():
        lw_items.append({"barcode": barcode, "name": meta.get(barcode, {}).get("name", barcode),
                         "product": titles.get(barcode), "actual": v["actual"],
                         "predicted": None if v["predicted"] is None else round(v["predicted"], 1)})
    lw_items.sort(key=lambda r: -r["actual"])
    lw_actual = sum(p["actual"] for p in lw["days"])
    lw_pred = [p["predicted"] for p in lw["days"] if p["predicted"] is not None]

    return {
        "ready": True,
        "first": result["first"].isoformat(), "last": result["last"].isoformat(),
        "history_days": result["history_days"],
        "working": [lavka_forecast.WEEKDAYS[w] for w in result["working"]],
        "horizon": {"start": result["horizon"][0].isoformat(), "end": result["horizon"][1].isoformat(),
                    "label": format_range(*result["horizon"])},
        "days": [{**_day(f.day), "value": round(f.value, 1), "low": round(f.low, 1), "high": round(f.high, 1),
                  "basis": f.basis} for f in forecast],
        "week": {"quantity": round(week_total, 1), "low": round(sum(f.low for f in forecast), 1),
                 "high": round(sum(f.high for f in forecast), 1), "gross": round(gross, 2),
                 "items_quantity": round(sum(r["total"] for r in rows), 1)},
        "trend": {
            "avg": round(avg_weeks, 1), "weeks": len(windows),
            "pct": _round1((week_total - avg_weeks) / avg_weeks * 100) if avg_weeks else None,
            "last_week": windows[0]["quantity"] if windows else None,
            "last_week_label": format_range(windows[0]["start"], windows[0]["end"]) if windows else None,
            "last_week_gross": round(gross_was, 2),
            "windows": [{"label": format_range(w["start"], w["end"]), "quantity": w["quantity"]} for w in windows],
        },
        "accuracy": {
            "mape": _round1(result["mape"]), "week_mape": _round1(result["week_mape"]),
            "checked": len(result["backtest"]),
            "weeks": lavka_forecast.BACKTEST_WEEKS,
            "points": [{**_day(p["day"]), "actual": p["actual"], "predicted": round(p["predicted"], 1)}
                       for p in result["backtest"]],
        },
        "last_week": {
            "start": lw["start"].isoformat(), "end": lw["end"].isoformat(),
            "label": format_range(lw["start"], lw["end"]),
            "days": [{**_day(p["day"]), "actual": p["actual"],
                      "predicted": None if p["predicted"] is None else round(p["predicted"], 1)} for p in lw["days"]],
            "actual": lw_actual,
            "predicted": round(sum(lw_pred), 1) if lw_pred else None,
            "items": lw_items,
        },
        "history": history,
        "items": rows,
    }
