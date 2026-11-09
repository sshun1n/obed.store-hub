"""Сборка «Уведомления об отгрузке» — вложения реализации. Перенесено из ka-webapp без изменений.

Документ, который в кабинете появляется по кнопке «Не отгружено». Через API
такого действия нет, а созданный по основанию документ приходит пустым:
позиции СБИС сама не подставляет. Поэтому собираем вложение сами.

Собираем не с нуля, а переписыванием настоящего уведомления: берём последнее
отправленное, меняем в нём данные точки и строки, всё остальное оставляем
как есть. Так реквизиты, банковские счета и структура остаются теми, что
СБИС уже принимала, — выдумывать формат с чистого листа для документа,
который уедет в Лавку, слишком рискованно.

Строки берём из вложения самого заказа: там та же таблица с GTIN, кодами
Лавки, количеством и НДС — данные приходят от получателя и потому совпадают
с тем, что он ждёт обратно.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal

# Единица измерения по коду ОКЕИ. В заказе код, в уведомлении — слово
UNITS = {"796": "шт", "778": "упак", "657": "изд"}



class NoticeError(Exception):
    pass


def _num(value: str | None) -> Decimal:
    try:
        return Decimal((value or "0").replace(",", "."))
    except Exception:
        return Decimal("0")


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def _line(source: ET.Element, position: int, catalogue: dict[str, dict]) -> ET.Element:
    """Строка уведомления из строки заказа.

    В заказе «Цена» — с налогом, а в уведомлении цена без налога и отдельно
    «ЦенаСНДС». Перепутать их значит разойтись с получателем в сумме.

    Название и код позиции берём не из заказа, а из каталога — из того, что
    кабинет писал в прошлых уведомлениях по этому же товару. У Лавки свои
    написания («Котлета рубленная» против нашей «Котлеты рубленные»), а код
    поставщика у части позиций вообще внутренний, вида «X886525825», и из
    заказа его не вывести. Сопоставляем по коду покупателя: он общий с Лавкой
    и не меняется. Чего в каталоге нет — пишем как пришло в заказе.
    """
    quantity = _num(source.get("Кол_во")) or Decimal("1")
    net = _num(source.get("СуммаБезНал"))
    gross = _num(source.get("Сумма"))
    buyer_code = source.get("КодПокупателя") or ""
    incoming = source.get("Название") or ""
    gtin = source.get("GTIN") or ""
    known = catalogue.get(buyer_code) or {}
    name = known.get("name") or incoming
    code = known.get("code") or gtin or source.get("Код") or ""
    okei = source.get("ОКЕИ") or "796"

    row = ET.Element(
        "СтрТабл",
        {
            "GTIN": gtin,
            "ЕдИзм": UNITS.get(okei, "шт"),
            "КодПокупателя": source.get("КодПокупателя") or "",
            "КодПоставщика": code,
            "Кол_во": source.get("Кол_во") or "0",
            "Название": name,
            "ОКЕИ": okei,
            "ПорНомер": str(position),
            "Сумма": _money(gross),
            "СуммаБезНал": _money(net),
            "Цена": _money(net / quantity),
            "ЦенаСНДС": _money(gross / quantity),
        },
    )
    vat = source.find("НДС")
    ET.SubElement(
        row,
        "НДС",
        {
            "Ставка": (vat.get("Ставка") if vat is not None else "") or "",
            "Сумма": _money(_num(vat.get("Сумма")) if vat is not None else Decimal("0")),
        },
    )

    # Те же параметры, что кабинет кладёт в свои документы: по ним получатель
    # сопоставляет позицию со своей номенклатурой
    params = {
        "НазваниеПокупателя": incoming,
        "Ид": code,
        "ШК": code,
        "Код": code,
        "КодПоставщика": code,
        "НазваниеПоставщика": name,
        "ПоляНоменклатуры": '"Type":"True"',
    }
    holder = ET.SubElement(row, "СписокПараметр")
    for key, value in params.items():
        ET.SubElement(holder, "Параметр", {"Значение": value, "Имя": key})
    extra = ET.SubElement(row, "ДопСведения")
    copy = ET.SubElement(extra, "СписокПараметр")
    for key, value in params.items():
        ET.SubElement(copy, "Параметр", {"Значение": value, "Имя": key})
    return row


def unknown_positions(order_xml: str, catalogue: dict[str, dict]) -> list[str]:
    """Позиции заказа, которых нет в каталоге, — их названия.

    По ним уведомление соберётся из данных Лавки, а не из наших. Документ от
    этого не сломается, но показать это глазами перед отправкой стоит.
    """
    try:
        order = ET.fromstring(order_xml)
    except ET.ParseError:
        return []
    return [
        row.get("Название") or "без названия"
        for row in order.iter("СтрТабл")
        if (row.get("КодПокупателя") or "") not in catalogue
    ]


def build(
    order_xml: str,
    template_xml: str,
    *,
    number: str,
    day: date,
    note: str,
    catalogue: dict[str, dict] | None = None,
) -> tuple[str, bytes]:
    """Уведомление об отгрузке по заказу: имя файла и содержимое.

    За основу берётся настоящее уведомление — образец, — в нём меняются точка,
    номер, даты и строки. Имя файла возвращаем отдельно: вложение в СБИС
    называется так же, как документ внутри.
    """
    try:
        order = ET.fromstring(order_xml)
        notice = ET.fromstring(template_xml)
    except ET.ParseError as exc:
        raise NoticeError(f"Не удалось разобрать XML: {exc}") from exc

    order_doc = order if order.tag == "Документ" else order.find("Документ")
    doc = notice if notice.tag == "Документ" else notice.find("Документ")
    if order_doc is None or doc is None:
        raise NoticeError("В XML нет блока «Документ»")

    stamp = day.strftime("%d.%m.%Y")
    doc.set("Дата", stamp)
    doc.set("ДатаОтгрузки", stamp)
    doc.set("Номер", number)
    doc.set("Примечание", note)
    doc.set("Срок", order_doc.get("Срок") or stamp)

    # Точку опознают по GLN, адрес в блоке получателя остаётся юридическим
    source = order_doc.find(".//Грузополучатель")
    target = doc.find(".//Грузополучатель")
    if source is not None and target is not None:
        if source.get("GLN"):
            target.set("GLN", source.get("GLN"))
        if source.get("Название"):
            target.set("Название", source.get("Название"))

    basis = doc.find("СписокОснование")
    if basis is not None:
        for child in list(basis):
            basis.remove(child)
        ET.SubElement(
            basis,
            "Основание",
            {
                "Дата": order_doc.get("Дата") or stamp,
                "Номер": order_doc.get("Номер") or "",
                "Тип": "Заказ",
            },
        )

    rows = doc.find("СписокСтрТабл")
    if rows is None:
        raise NoticeError("В образце нет списка строк")
    for child in list(rows):
        rows.remove(child)

    total_gross = Decimal("0")
    total_net = Decimal("0")
    for position, source_row in enumerate(order_doc.iter("СтрТабл"), start=1):
        rows.append(_line(source_row, position, catalogue or {}))
        total_gross += _num(source_row.get("Сумма"))
        total_net += _num(source_row.get("СуммаБезНал"))
    if not len(rows):
        raise NoticeError("В заказе нет ни одной строки")
    ET.SubElement(rows, "ИтогТабл", {"Сумма": _money(total_gross), "СуммаБезНДС": _money(total_net)})

    # Хвостовые параметры дублируют номер и дату накладной — их читает кабинет
    tail = doc.find("СписокПараметр")
    if tail is not None:
        values = {
            "ДатаТоварнойНакладной": stamp,
            "НомерТоварнойНакладной": number,
            "Примечание": note,
            "ИнфПередТабл": note,
            "НакладнаяНомер": number,
            "НакладнаяДата": stamp,
            "ОтгрузкаДата": stamp,
            "номер_накладной": number,
            "дата_накладной": day.strftime("%Y-%m-%d 00:00:00"),
        }
        for parameter in tail.iter("Параметр"):
            name = parameter.get("Имя")
            if name in values:
                parameter.set("Значение", values[name])

    root = notice
    root.set("Имя", _file_name(root.get("Имя") or "", order_doc.get("Дата") or stamp))
    body = ET.tostring(root, encoding="windows-1251", xml_declaration=True)
    # Кабинет пишет кодировку заглавными — держим файл в том же виде
    body = re.sub(rb"encoding='windows-1251'", b'encoding="WINDOWS-1251"', body, count=1)
    return root.get("Имя"), body


def _file_name(template: str, order_date: str) -> str:
    """Имя файла по образцу: меняем только дату и идентификатор.

    Имя не произвольное: «DP_TTN__<код Лавки>_<наш ИНН и КПП>_<дата заказа>_
    <идентификатор>». Первые части — это стороны обмена, они те же самые, и
    придумывать их заново незачем. Дата в имени — заказа, а не отгрузки: так в
    обоих документах, которые кабинет сделал сам.
    """
    stamp = ""
    try:
        stamp = datetime.strptime(order_date, "%d.%m.%Y").strftime("%Y%m%d")
    except ValueError:
        pass
    parts = template.split("_")
    fresh = str(uuid.uuid4()).upper()
    if len(parts) < 4 or not stamp:
        return f"DP_TTN__{stamp}_{fresh}"
    parts[-2:] = [stamp, fresh]
    return "_".join(parts)
