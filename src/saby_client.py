"""Клиент Saby (СБИС): вход, чтение, запись документов, переходы и подпись.

Перенесено из ka-webapp (saby.py, edo.py). Здесь только вызовы API; что и когда
создавать, решает saby_actions. Документы создаются только по кнопке админа.

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

import http.client
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from typing import Callable, Dict, Iterator, List, Optional

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
# Справка Saby: «следите, чтобы количество параллельных потоков было не больше 5», иначе
# СБИС ограничит потоки или заблокирует сессию. Предел — на всё приложение, а не на операцию:
# кабинет и логин общие с ka-webapp, поэтому оба приложения одновременно не запускают
SABY_PARALLEL = 5
WRITE_CHOICES = (1, 2, 3, 5)

_local = threading.local()

ORDERS_RE = re.compile(r'<ТекстИнф[^>]*Значение="([^"]*)"[^>]*Идентиф="ORDERS"|<ТекстИнф[^>]*Идентиф="ORDERS"[^>]*Значение="([^"]*)"')
CARGO_RE = re.compile(r'НаимГруз="([^"]*)"')
DELIVERY_RE = re.compile(r'АдресДостГр.*?АдрТекст="([^"]*)"', re.DOTALL)


class SabyError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("SABY_LOGIN") and os.getenv("SABY_PASSWORD"))


class SabyBusy(SabyError):
    """Идёт другая запись в кабинет — вторую не начинаем."""


# --- Общий предел запросов ---

class _Gate:
    """Семафор с изменяемым пределом: все запросы к СБИС приложения идут через него."""

    def __init__(self, limit: int):
        self._cond = threading.Condition()
        self._limit = limit
        self._busy = 0

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, min(int(limit), SABY_PARALLEL))
            self._cond.notify_all()

    def __enter__(self):
        with self._cond:
            while self._busy >= self._limit:
                self._cond.wait()
            self._busy += 1
        return self

    def __exit__(self, *exc):
        with self._cond:
            self._busy -= 1
            self._cond.notify_all()


_GATE = _Gate(SABY_PARALLEL)
_WRITE_PARALLEL = [1]


def set_limits(read: Optional[int] = None, write: Optional[int] = None) -> None:
    """Потоки из админ-панели: действуют сразу, без перезапуска."""
    if read:
        _GATE.set_limit(read)
    if write:
        _WRITE_PARALLEL[0] = write if write in WRITE_CHOICES else 1


def read_parallel() -> int:
    return _GATE.limit


def write_parallel() -> int:
    return min(_WRITE_PARALLEL[0], _GATE.limit)


# --- Одна запись в кабинет за раз ---
#
# Операции и так идут по одной (saby_sync._lock), но подпись идёт мимо них прямо из
# запроса. Замок записи держат все, кто пишет: действия, подготовка и отправка подписи.
# serial растёт с каждой записью — фоновое чтение по нему видит, что, пока оно читало,
# кабинет поменялся, и прочитанное не сохраняет (saby_sync.Stale).

_WRITE_LOCK = threading.Lock()
_writing: Dict[str, object] = {"title": None, "serial": 0}


@contextmanager
def writing(title: str):
    if not _WRITE_LOCK.acquire(blocking=False):
        raise SabyBusy(f"Не запущено: идёт другая запись в СБИС — «{_writing['title'] or 'операция'}»")
    _writing["title"] = title
    _writing["serial"] = int(_writing["serial"]) + 1
    try:
        yield
    finally:
        _writing["title"] = None
        _WRITE_LOCK.release()


def writing_now() -> bool:
    return _WRITE_LOCK.locked()


def write_serial() -> int:
    return int(_writing["serial"])


# --- Транспорт ---
#
# Чтения идут через соединение, открытое у потока: без нового TLS-рукопожатия вызов
# вдвое быстрее (90 мс против 170). Оборвалось устаревшее соединение — один повтор.
# Записи — всегда по свежему соединению и без повтора: ответ мог потеряться после
# того, как СБИС запись выполнил, и повтор отправил бы документ второй раз.

READ_METHODS = frozenset({
    "СБИС.Аутентифицировать", "СБИС.СписокДокументов", "СБИС.ПрочитатьДокумент",
    "СБИС.СгенерироватьВложение",       # только собирает файл, документа не создаёт
    "СБИС.ИнформацияОКонтрагенте",
})
_STALE = (http.client.RemoteDisconnected, http.client.CannotSendRequest, http.client.BadStatusLine,
          ConnectionResetError, BrokenPipeError, ConnectionAbortedError)
TIMEOUT = 40


def _connections() -> Dict[str, http.client.HTTPSConnection]:
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    return conns


def _drop(netloc: str) -> None:
    conn = _connections().pop(netloc, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _send(url: str, body: bytes, headers: dict, *, reuse: bool) -> tuple:
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    if not reuse:
        conn = http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT)
        try:
            conn.request("POST", path, body, headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()
    for attempt in (1, 2):
        conns = _connections()
        conn = conns.get(parts.netloc)
        if conn is None:
            conn = conns[parts.netloc] = http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT)
        reused = conn.sock is not None
        try:
            conn.request("POST", path, body, headers)
            response = conn.getresponse()
            data = response.read()
        except _STALE:
            _drop(parts.netloc)
            if not reused or attempt == 2:      # оборвалось свежее — это сбой, а не «устарело»
                raise
            continue
        except Exception:
            _drop(parts.netloc)
            raise
        if response.will_close:
            _drop(parts.netloc)
        return response.status, data
    raise http.client.HTTPException("нет ответа")


def _post(url: str, method: str, params: dict, sid: str = "") -> dict:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 0},
                      ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if sid:
        headers[SESSION_HEADER] = sid
    try:
        with _GATE:
            status, raw = _send(url, body, headers, reuse=method in READ_METHODS)
    except Exception as exc:
        raise SabyError(f"Не удалось связаться с Saby: {exc}") from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SabyError(f"HTTP {status}: {text[:300]}") from exc
    if "error" in payload:
        error = payload["error"]
        details = (error.get("data") or {}).get("details") or ""
        raise SabyError(f"{error.get('message', '')} {details}".strip() or "Saby вернула ошибку")
    if status >= 400:
        raise SabyError(f"HTTP {status}: {text[:300]}")
    return payload.get("result")


def fetch(sid: str, link: str, timeout: int = 60) -> bytes:
    """Файл документа по ссылке. Ссылка может вести на другой хост с переадресацией,
    поэтому urllib, но тоже под общим пределом."""
    request = urllib.request.Request(link, headers={SESSION_HEADER: sid})
    try:
        with _GATE, urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise SabyError(f"Не скачался файл документа: {exc}") from exc


# --- Несколько потоков ---

def pmap(fn: Callable, items: list, on_done: Optional[Callable[[int, int], None]] = None,
         workers: Optional[int] = None) -> list:
    """fn по каждому элементу в несколько потоков, результаты в том же порядке.

    Для чтений. Исключение элемента возвращается на его месте, а не прерывает
    остальные — решает вызывающий. В базу из fn не пишем: пишет вызывающий поток.
    """
    items = list(items)
    if not items:
        return []
    out: list = [None] * len(items)
    done = [0]
    lock = threading.Lock()

    def run(index):
        try:
            out[index] = fn(items[index])
        except Exception as exc:     # noqa: BLE001 — отдаём вызывающему как есть
            out[index] = exc
        with lock:
            done[0] += 1
            count = done[0]
        if on_done:
            on_done(count, len(items))

    size = max(1, min(workers or _GATE.limit, len(items)))
    with ThreadPoolExecutor(max_workers=size, thread_name_prefix="saby") as pool:
        list(pool.map(run, range(len(items))))
    return out


LIMIT_HINTS = ("сесси", "лимит", "превыш", "ограничен", "блокир", "HTTP 429", "HTTP 503", "HTTP 502", "HTTP 401")


def is_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(h.lower() in text for h in LIMIT_HINTS)


def is_link_error(exc: BaseException) -> bool:
    """Обрыв связи: запись могла и пройти — повторять нельзя, только перечитать."""
    return "Не удалось связаться с Saby" in str(exc)


def pmap_writes(fn: Callable, items: list, on_brake: Optional[Callable[[BaseException], None]] = None) -> list:
    """Записи по точкам. При 1 потоке (по умолчанию) — простой цикл в прежнем порядке.
    При N — N точек одновременно, каждую ведёт один поток, поэтому шаги внутри точки
    идут по порядку. После ошибки лимита или сессии новые точки идут строго по одной."""
    items = list(items)
    workers = write_parallel()
    if workers <= 1 or len(items) <= 1:
        out = []
        for item in items:
            try:
                out.append(fn(item))
            except Exception as exc:     # noqa: BLE001
                out.append(exc)
        return out
    brake, serial = threading.Event(), threading.Lock()

    def call(item):
        try:
            return fn(item)
        except Exception as exc:     # noqa: BLE001
            if is_limit_error(exc) and not brake.is_set():
                brake.set()
                if on_brake:
                    on_brake(exc)
            return exc

    def run(item):
        if brake.is_set():
            with serial:
                return call(item)
        return call(item)

    return pmap(run, items, workers=workers)


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


def read_card(sid: str, url: str, ident: str, kind: str, state: str = "") -> dict:
    """Карточка документа. Реализацию — только завершённую, см. начало модуля.

    Завершённые реализации нужны одному месту: образцу уведомления об отгрузке и
    каталогу позиций. Их ka-webapp читал сотнями без единой новой редакции.
    Состояние передаёт вызывающий, взяв его из списка: узнавать его чтением
    карточки уже поздно.
    """
    if kind == SHIPMENT_TYPE and state not in FINISHED:
        raise SabyError(f"Реализацию в состоянии «{state or 'неизвестно'}» открывать нельзя: у незавершённой "
                        "появляется вторая редакция уведомления и перестаёт уходить УПД. "
                        "Берите данные из СписокДокументов")
    if kind == SHIPMENT_TYPE:
        url = DOCS_URL
    if kind not in (ORDER_TYPE, SHIPMENT_TYPE, TRANSPORT_TYPE, WAYBILL_TYPE):
        raise SabyError(f"Чтение документов типа «{kind}» не проверялось")
    card = _post(url, "СБИС.ПрочитатьДокумент", {"Документ": {"Идентификатор": ident}}, sid) or {}
    if card.get("Тип") and card["Тип"] != kind:
        raise SabyError(f"Ожидали {kind}, пришёл {card['Тип']}")
    return card


def download(sid: str, link: str) -> str:
    return fetch(sid, link, timeout=30).decode("cp1251", errors="replace")


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


# --- Запись, переходы, подпись ---

def write_document(sid: str, url: str, document: dict) -> dict:
    return _post(url, "СБИС.ЗаписатьДокумент", {"Документ": document}, sid) or {}


def generate_attachment(sid: str, kind: str, subtype: str, version: str, payload: dict) -> dict:
    """Файл документа по подстановке: формат ФНС собирает сам СБИС."""
    generated = _post(TMS_URL, "СБИС.СгенерироватьВложение", {"Документ": {"Вложение": [
        {"Тип": kind, "Подтип": subtype, "ВерсияФормата": version, "Подстановка": payload}]}}, sid)
    return generated["Вложение"][0]["Файл"]


def run_action(sid: str, url: str, doc_id: str, stage: str, action: str, stage_id: str = "") -> dict:
    """Переход без подписи: подготовка и выполнение, как требует справка."""
    step: dict = {"Название": stage, "Действие": {"Название": action}}
    if stage_id:
        step["Идентификатор"] = stage_id
    _post(url, "СБИС.ПодготовитьДействие", {"Документ": {"Идентификатор": doc_id, "Этап": step}}, sid)
    return _post(url, "СБИС.ВыполнитьДействие", {"Документ": {"Идентификатор": doc_id, "Этап": step}}, sid)


def pending_action(card: dict, preferred: tuple) -> tuple:
    """Этап и переход, которые ждут подписи. Не угадываем: если вариантов несколько
    и ни один не из preferred — останавливаемся и перечисляем."""
    options = []
    for stage in card.get("Этап") or []:
        if stage.get("Служебный") == "Да":
            continue
        options += [(stage.get("Название") or "", a.get("Название") or "")
                    for a in stage.get("Действие") or [] if a.get("ТребуетПодписания") == "Да"]
    if not options:
        raise SabyError("документу сейчас нечего подписывать — вероятно, он уже отправлен")
    for wanted in preferred:
        for option in options:
            if option[1] == wanted:
                return option
    if len(options) == 1:
        return options[0]
    raise SabyError("не понял, какой шаг подписывать: " + ", ".join(f"«{a}: {b}»" for a, b in options))


def sign_step(sid: str, url: str, doc_id: str, kind: str, preferred: tuple, fixed: Optional[tuple] = None) -> tuple:
    """Этап и переход, которые подписывать. Только чтение — можно в несколько потоков.

    fixed — этап и действие, известные заранее (УПД по реализации). Тогда карточку
    не читаем вовсе: открывать незавершённую реализацию нельзя (см. read_card).
    """
    if fixed:
        return fixed
    return pending_action(read_card(sid, url, doc_id, kind), preferred)


def prepare_action(sid: str, url: str, doc_id: str, stage: str, action: str, thumbprint: str) -> List[dict]:
    """ПодготовитьДействие под подпись — это запись, по одному документу. Отдаёт файлы
    к подписи: {id, name, link}; скачивает их вызывающий (fetch, можно параллельно)."""
    prepared = _post(url, "СБИС.ПодготовитьДействие", {"Документ": {"Идентификатор": doc_id, "Этап": {
        "Название": stage, "Действие": {"Название": action, "Сертификат": {"Отпечаток": thumbprint}}}}}, sid) or {}
    files = []
    for prepared_stage in prepared.get("Этап") or []:
        for attachment in prepared_stage.get("Вложение") or []:
            file = attachment.get("Файл") or {}
            if attachment.get("Идентификатор") and file.get("Ссылка"):
                files.append({"id": attachment["Идентификатор"], "name": file.get("Имя") or "", "link": file["Ссылка"]})
    if not files:
        raise SabyError("СБИС не вернул файл для подписи")
    return files


def send_signed(sid: str, url: str, doc_id: str, stage: str, action: str, thumbprint: str,
                signatures: Dict[str, str]) -> dict:
    """Подпись сделана в браузере (Ключ.Тип = «Клиентский») — отправляем её в СБИС."""
    return _post(url, "СБИС.ВыполнитьДействие", {"Документ": {"Идентификатор": doc_id, "Этап": {
        "Название": stage,
        "Действие": [{"Название": action, "Сертификат": {"Отпечаток": thumbprint, "Ключ": {"Тип": "Клиентский"}}}],
        "Вложение": [{"Идентификатор": fid, "Подпись": [{"Файл": {"ДвоичныеДанные": sig}}]}
                     for fid, sig in sorted(signatures.items())],
    }}}, sid)


def max_number(sid: str, doc_type: str) -> int:
    """Наибольший номер документа этого типа в кабинете. Нумерация общая с Ателье
    и раздельная у заказов и накладных, поэтому считаем по кабинету, а не по себе."""
    numbers = []
    for item in list_documents(sid, TMS_URL, doc_type, pages=2):
        value = str(item.get("Номер") or "").strip()
        if value.isdigit():
            numbers.append(int(value))
    return max(numbers, default=0)


def xml_attachment(sid: str, card: dict, fmt: str) -> str:
    """Вложение ЭДО по атрибуту «Формат» внутри файла (Заказ, УведомлОтгр), а не по имени."""
    import xml.etree.ElementTree as ET
    for item in card.get("Вложение") or []:
        file = item.get("Файл") or {}
        link, name = file.get("Ссылка"), file.get("Имя") or ""
        if not link or not name.lower().endswith(".xml"):
            continue
        try:
            text = download(sid, link)
            if ET.fromstring(text).get("Формат") == fmt:
                return text
        except Exception:
            continue
    return ""
