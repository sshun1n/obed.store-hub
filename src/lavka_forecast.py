"""Прогноз заказов Яндекс.Лавки на неделю вперёд — в целом и по каждой позиции.

Модель та же, что в ka-webapp (yandex_stats, решение D-39): сезонно-наивная.
Чтобы предсказать четверг, берём прошлые четверги и усредняем с затуханием веса:
свежая неделя весит больше старой. Чистый Python: данных мало, а у ритейла
главная сезонность недельная, тяжёлые модели выигрывают считанные проценты.

Дни отгрузки модель находит сама по последним неделям: у Обеда сейчас это
вторник, четверг и суббота. Поменяется график — через пару недель поменяется
и прогноз, без правки кода.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Dict, List, Optional, Set

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

SEASON_DEPTH = 8        # сколько одноимённых дней недели в расчёте: два месяца
DECAY = 0.35            # вес недели назад — 0.65 от свежей, на третьей неделе около половины
CORRIDOR = 1.5          # ширина коридора в медианных отклонениях
WORKDAY_WEEKS = 8       # по скольким последним неделям искать дни отгрузки
WORKDAY_SHARE = 0.5     # день недели рабочий, если отгрузки в нём были хотя бы в половине недель
ACTIVE_DAYS = 3         # позиция жива, если её заказывали за последние 3 дня поставки
BACKTEST_WEEKS = 4      # на скольких неделях меряем среднюю ошибку
TREND_WEEKS = 4         # с чем сравниваем прогноз: средняя неделя за 4 последние


@dataclass
class DayForecast:
    day: date
    value: float
    low: float
    high: float
    basis: int


def _weighted(history: List[float]) -> tuple:
    """Взвешенное среднее и медианный разброс по ряду «тот же день недели, от новых к старым»."""
    values = history[:SEASON_DEPTH]
    if not values:
        return 0.0, 0.0
    weight, total, weights = 1.0, 0.0, 0.0
    for value in values:
        total += value * weight
        weights += weight
        weight *= 1 - DECAY
    forecast = total / weights
    spread = median([abs(v - forecast) for v in values])
    return forecast, spread


def working_weekdays(days: List[date]) -> Set[int]:
    """Дни недели, в которые действительно отгружаемся, — по последним неделям.

    Смотрим только свежие недели: если график поменялся, старые месяцы не должны
    держать прогноз на дне, в который уже не возим. Разовая поставка в другой
    день (перенос из-за праздника) порог в половину недель не пройдёт.
    """
    if not days:
        return set()
    last = max(days)
    window_start = last - timedelta(days=WORKDAY_WEEKS * 7 - 1)
    recent = [d for d in days if d >= window_start]
    weeks = max(1, len({d - timedelta(days=d.weekday()) for d in recent}))
    counts: Dict[int, int] = defaultdict(int)
    for d in recent:
        counts[d.weekday()] += 1
    return {wd for wd, count in counts.items() if count >= max(1.0, weeks * WORKDAY_SHARE)}


def _history(series: Dict[date, float], target: date, since: Optional[date] = None) -> List[float]:
    """Прошлые значения того же дня недели до target, от новых к старым."""
    return [series[d] for d in sorted(series, reverse=True)
            if d < target and d.weekday() == target.weekday() and (since is None or d >= since)]


def forecast_series(series: Dict[date, float], days: List[date]) -> List[DayForecast]:
    out = []
    for target in days:
        history = _history(series, target)
        if not history:
            continue
        value, spread = _weighted(history)
        out.append(DayForecast(target, value, max(value - CORRIDOR * spread, 0.0),
                               value + CORRIDOR * spread, min(len(history), SEASON_DEPTH)))
    return out


def item_series(item_days: Dict[date, float], all_days: List[date]) -> Dict[date, float]:
    """Ряд позиции по всем дням поставки с нулями там, где её не заказали.

    Без нулей прогноз считал бы только удачные дни и завышал бы редкие позиции.
    Нули ставим лишь с первого появления позиции: до него её просто не было.
    """
    if not item_days:
        return {}
    first = min(item_days)
    return {d: item_days.get(d, 0.0) for d in all_days if d >= first}


def predict_one(series: Dict[date, float], target: date) -> Optional[float]:
    """Что модель сказала бы про target, зная только то, что было до него."""
    history = _history(series, target)
    if len(history) < 3:
        return None
    return _weighted(history)[0]


def build(totals: Dict[date, float], items: Dict[str, Dict[date, float]], today: date) -> Dict:
    """Всё для страницы прогноза: дни, позиции, проверка на прошлом.

    totals — штуки по дням поставки, items — то же по каждому штрих-коду.
    Возвращает сырые числа, оформление — в api_lavka_reports.
    """
    days = sorted(totals)
    if len(days) < 6:
        return {"ready": False}
    last = days[-1]
    working = working_weekdays(days)

    # Неделя вперёд — после последнего известного заказа: он уже пришёл, гадать о нём незачем
    horizon = [last + timedelta(days=i) for i in range(1, 8)]
    target_days = [d for d in horizon if d.weekday() in working]
    forecast = forecast_series(totals, target_days)

    recent = set(days[-ACTIVE_DAYS:])
    item_rows = {}
    for key, by_day in items.items():
        if not any(by_day.get(d, 0) > 0 for d in recent):
            continue   # снята с продажи: её нули только раздували бы таблицу
        series = item_series(by_day, days)
        by_target = {}
        for f in forecast_series(series, target_days):
            by_target[f.day] = f.value
        item_rows[key] = by_target

    # Проверка на прошлом: каждый день последних недель предсказываем вслепую
    backtest = []
    check_from = last - timedelta(days=BACKTEST_WEEKS * 7 - 1)
    for d in days:
        if d < check_from or d.weekday() not in working:
            continue
        predicted = predict_one(totals, d)
        if predicted is None:
            continue
        backtest.append({"day": d, "actual": totals[d], "predicted": predicted})
    errors = [abs(p["predicted"] - p["actual"]) / p["actual"] * 100 for p in backtest if p["actual"]]
    mape = sum(errors) / len(errors) if errors else None
    # Та же проверка по неделям целиком: прогноз недельный, и промахи отдельных дней
    # внутри недели частью гасят друг друга
    week_errors = []
    for k in range(BACKTEST_WEEKS):
        end = last - timedelta(days=7 * k)
        points = [p for p in backtest if end - timedelta(days=6) <= p["day"] <= end]
        actual = sum(p["actual"] for p in points)
        if points and actual:
            week_errors.append(abs(sum(p["predicted"] for p in points) - actual) / actual * 100)
    week_mape = sum(week_errors) / len(week_errors) if week_errors else None

    # Прошлая неделя — последняя целиком прошедшая календарная, с понедельника по воскресенье
    this_monday = today - timedelta(days=today.weekday())
    prev_monday = this_monday - timedelta(days=7)
    prev_days = [d for d in days if prev_monday <= d < this_monday]
    if not prev_days:
        # Сегодняшняя неделя без истории позади — берём последнюю неделю, где поставки были
        prev_monday = last - timedelta(days=last.weekday())
        if prev_monday + timedelta(days=6) >= today:
            prev_monday -= timedelta(days=7)
        prev_days = [d for d in days if prev_monday <= d < prev_monday + timedelta(days=7)]
    last_week = []
    for d in prev_days:
        predicted = predict_one(totals, d)
        last_week.append({"day": d, "actual": totals[d], "predicted": predicted})
    last_week_items = {}
    for key, by_day in items.items():
        if not any(by_day.get(d, 0) > 0 for d in prev_days):
            continue
        series = item_series(by_day, days)
        actual = sum(series.get(d, 0.0) for d in prev_days)
        predicted = [predict_one(series, d) for d in prev_days if d in series]
        known = [p for p in predicted if p is not None]
        last_week_items[key] = {"actual": actual, "predicted": sum(known) if known else None}

    # С чем сравнивать неделю прогноза: те же семидневные окна назад от последнего заказа
    windows = []
    for k in range(TREND_WEEKS):
        end = last - timedelta(days=7 * k)
        start = end - timedelta(days=6)
        windows.append({"start": start, "end": end,
                        "quantity": sum(v for d, v in totals.items() if start <= d <= end)})

    return {
        "ready": bool(forecast),
        "first": days[0], "last": last, "history_days": len(days),
        "working": sorted(working),
        "horizon": (horizon[0], horizon[-1]),
        "forecast": forecast,
        "items": item_rows,
        "backtest": backtest, "mape": mape, "week_mape": week_mape,
        "last_week": {"start": prev_monday, "end": prev_monday + timedelta(days=6),
                      "days": last_week, "items": last_week_items},
        "windows": windows,
    }
