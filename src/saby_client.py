"""Клиент Saby (СБИС): вход и чтение. Ничего не создаёт и не меняет.

Перенесено из ka-webapp (saby.py, edo.py) только то, что нужно для чтения.
Создание документов (этап 6) будет добавляться сюда же, по одному, вместе с
владельцем.

Главное правило из ka-webapp: **реализацию (ДокОтгрИсх) не открывать.** Чтение
карточки незавершённой реализации порождает вторую редакцию уведомления об
отгрузке, и СБИС перестаёт отправлять УПД: 4, 5 и 9 сентября так сломали 32
документа, бухгалтер чинил руками. Поэтому read_card отказывает для реализаций
всегда, а всё, что нужно о них знать (номер, состояние, вложения), берётся из
СписокДокументов: список безвреден, им перебирались тысячи документов.

Заказы Лавки (ЗаказВх) и документы TMS (заказ на перевозку, ЭТрН) ka-webapp
открывает в работе каждый день, лишних редакций это не давало. Проверено и
здесь 2026-11-09 на завершённых документах: редакция до и после чтения одна.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from typing import Dict, Iterator, List, Optional

AUTH_URL = "https://online.sbis.ru/auth/service/"
TMS_URL = "https://tms.saby.ru/service/"
DOCS_URL = "https://online.sbis.ru/service/?srv=1"
SESSION_HEADER = "X-SBISSessionID"

ORDER_TYPE = "ЗаказВх"            # заказ от Лавки, номер ЯЛ…
SHIPMENT_TYPE = "ДокОтгрИсх"      # реализация — НЕ ОТКРЫВАТЬ
TRANSPORT_TYPE = "TransportOrder"  # заказ на перевозку
WAYBILL_TYPE = "ConsignmentNote"   # ЭТрН

FINISHED = {"Выполнение завершено успешно", "Выполнение завершено с проблемами"}
PAGE = 50

ORDERS_RE = re.compile(r'<ТекстИнф[^>]*Значение="([^"]*)"[^>]*Идентиф="ORDERS"|<ТекстИнф[^>]*Идентиф="ORDERS"[^>]*Значение="([^"]*)"')
CARGO_RE = re.compile(r'НаимГруз="([^"]*)"')
DELIVERY_RE = re.compile(r'АдресДостГр.*?АдрТекст="([^"]*)"', re.DOTALL)


class SabyError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("SABY_LOGIN") and os.getenv("SABY_PASSWORD"))


def _post(url: str, method: str, params: dict, sid: str = "") -> dict:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 0},
                      ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if sid:
        headers[SESSION_HEADER] = sid
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise SabyError(f"HTTP {exc.code}: {raw[:300]}") from exc
    except Exception as exc:
        raise SabyError(f"Не удалось связаться с Saby: {exc}") from exc
    if "error" in payload:
        error = payload["error"]
        details = (error.get("data") or {}).get("details") or ""
        raise SabyError(f"{error.get('message', '')} {details}".strip() or "Saby вернула ошибку")
    return payload.get("result")


def login() -> str:
    if not configured():
        raise SabyError("Не заданы SABY_LOGIN и SABY_PASSWORD в .env сервера")
    return str(_post(AUTH_URL, "СБИС.Аутентифицировать",
                     {"Логин": os.getenv("SABY_LOGIN"), "Пароль": os.getenv("SABY_PASSWORD")}))


def _stamp(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def list_documents(sid: str, url: str, doc_type: str, *, start: Optional[date] = None, end: Optional[date] = None,
                   mask: str = "", pages: int = 20) -> Iterator[dict]:
    """СписокДокументов постранично. Даты задаём всегда, когда можем: без них
    список обрезается сотней записей, и документы за день можно недосчитать."""
    for page in range(pages):
        flt = {"Тип": doc_type, "Навигация": {"РазмерСтраницы": str(PAGE), "Страница": str(page)}}
        if start:
            flt["ДатаС"] = _stamp(start)
        if end:
            flt["ДатаПо"] = _stamp(end)
        if mask:
            flt["Маска"] = mask
        result = _post(url, "СБИС.СписокДокументов", {"Фильтр": flt}, sid) or {}
        items = result.get("Документ") or []
        for item in items:
            if item.get("Тип") and item.get("Тип") != doc_type:
                continue
            yield item
        if len(items) < PAGE:
            return


def read_card(sid: str, url: str, ident: str, kind: str) -> dict:
    """Карточка документа. Реализацию — никогда, см. начало модуля."""
    if kind == SHIPMENT_TYPE:
        raise SabyError("Реализацию открывать нельзя: у незавершённой появляется вторая редакция "
                        "уведомления и перестаёт уходить УПД. Берите данные из СписокДокументов")
    if kind not in (ORDER_TYPE, TRANSPORT_TYPE, WAYBILL_TYPE):
        raise SabyError(f"Чтение документов типа «{kind}» не проверялось")
    card = _post(url, "СБИС.ПрочитатьДокумент", {"Документ": {"Идентификатор": ident}}, sid) or {}
    if card.get("Тип") and card["Тип"] != kind:
        raise SabyError(f"Ожидали {kind}, пришёл {card['Тип']}")
    return card


def download(sid: str, link: str) -> str:
    request = urllib.request.Request(link, headers={SESSION_HEADER: sid})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("cp1251", errors="replace")
    except Exception as exc:
        raise SabyError(f"Не скачался файл документа: {exc}") from exc


def attachment_xml(sid: str, card: dict, kind: str) -> str:
    """Файл основного вложения нужного типа (ЭТрН, ЗаказЗаявка). Пусто, если нет."""
    for item in card.get("Вложение") or []:
        link = (item.get("Файл") or {}).get("Ссылка")
        if item.get("Тип") == kind and link:
            return download(sid, link)
    return ""


def state(document: dict) -> str:
    return (document.get("Состояние") or {}).get("Название") or ""


def attachments(document: dict) -> List[str]:
    return [a.get("Тип") for a in document.get("Вложение") or [] if a.get("Служебный") != "Да" and a.get("Тип")]


def consequences(card: dict, doc_type: str) -> List[Dict]:
    """Документы, сделанные из этого: реализация из заказа, ЭТрН из заказа на перевозку."""
    out = []
    for link in card.get("ДокументСледствие") or []:
        doc = link.get("Документ") or {}
        if doc.get("Тип") == doc_type and doc.get("Идентификатор"):
            out.append({"id": doc["Идентификатор"], "number": str(doc.get("Номер") or "").strip(),
                        "date": doc.get("Дата") or ""})
    return out


def waybill_facts(xml: str) -> Dict[str, str]:
    """Из титула ЭТрН: номер заказа Лавки (ORDERS), груз и адрес доставки."""
    found = ORDERS_RE.search(xml)
    order_no = (found.group(1) or found.group(2)) if found else ""
    cargo = CARGO_RE.search(xml)
    delivery = DELIVERY_RE.search(xml)
    return {"order_no": order_no.strip(), "cargo": cargo.group(1) if cargo else "",
            "address": delivery.group(1) if delivery else ""}


def party_name(card: dict) -> str:
    party = card.get("Контрагент") or {}
    person = party.get("СвФЛ") or {}
    if person:
        return person.get("НазваниеПолное") or " ".join(
            x for x in (person.get("Фамилия"), person.get("Имя"), person.get("Отчество")) if x)
    return (party.get("СвЮЛ") or {}).get("Название") or ""
