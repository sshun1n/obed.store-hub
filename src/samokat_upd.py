"""УПД для «Самоката» (ООО «Умный ритейл») по его заказу ORDERS.

Образца отгрузки Самокату в кабинете нет, поэтому УПД собирается так же, как
уведомление Лавки в desadv.py: за основу берётся настоящий УПД нашего ИП,
который сделал сам СБИС (формат 5.03, СЧФДОП), — из него остаются продавец,
грузоотправитель, банк, подписант и оформление. Покупатель, грузополучатель,
строки, основания и информационные поля пишутся заново по заказу и по
требованиям Самоката («Технические требования ЭДО и EDI», договор, прил. 3):

* заголовок, ИнфПолФХЖ1: отправитель (наш GLN), грузополучатель (GLN точки),
  номер_заказа (УР-…), номер_накладной, номер_договора, дата_договора;
* каждая строка, ИнфПолФХЖ2: штрихкод, код_покупателя (УТ-…);
* договор ещё и в основании передачи (СвПер/ОснПер);
* грузополучатель на ЦФЗ — «Умный ритейл» с КПП ЦФЗ из заказа, адрес точки.

Имя файла как у СБИС: ON_NSCHFDOPPR_<ящик получателя>_<наш ящик>_<дата>_<GUID>.
Ящики берутся из СБИС (ИнформацияОКонтрагенте), не выдумываются.
"""
from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple

CENTS = Decimal("0.01")
UNITS = {"796": "шт", "PCE": "шт", "778": "упак", "657": "изд"}
OKEI = {"PCE": "796"}


class UpdError(Exception):
    pass


@dataclass
class Contract:
    number: str
    date: str          # ДД.ММ.ГГГГ


def _num(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", "."))
    except Exception:
        return Decimal("0")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):f}"


def _qty(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rate(value: str) -> str:
    """«5,00» → «5%»: так ставку пишет СБИС в своих УПД."""
    rate = _num(value)
    if rate == 0 and not (value or "").strip():
        return "без НДС"
    return f"{_qty(rate.normalize())}%"


def _org(parent: ET.Element, tag: str, inn: str, kpp: str, name: str, gln: str, address: str,
         short: str) -> ET.Element:
    """Блок юрлица в формате 5.03: ИдСв/СвЮЛУч и Адрес/АдрИнф с GLN места."""
    node = ET.SubElement(parent, tag, {"СокрНаим": short})
    ids = ET.SubElement(node, "ИдСв")
    attrs = {"ИННЮЛ": inn, "НаимОрг": name}
    if kpp:
        attrs["КПП"] = kpp
    ET.SubElement(ids, "СвЮЛУч", attrs)
    addr = ET.SubElement(node, "Адрес", {"ГЛНМеста": gln} if gln else {})
    ET.SubElement(addr, "АдрИнф", {"КодСтр": "643", "НаимСтран": "РОССИЯ", "АдрТекст": address})
    return node


def build(order_xml: str, template_xml: str, *, number: str, day: date, contract: Contract,
          receiver_box: str, codes: dict, sender_box: str = "", now: datetime | None = None) -> Tuple[str, bytes]:
    """УПД по заказу Самоката: имя файла (без .xml) и содержимое в windows-1251.

    codes — код покупателя (УТ-…) → код карточки нашего каталога, к которой строка
    привязана в самом заказе. У блюда две карточки: Лавки (со штрих-кодом) и Самоката.
    По штрих-коду СБИС выбрал бы карточку Лавки, и заказ остался бы «отгружен
    частично». Код карточки пишем так же, как СБИС в своих УПД (КодТов, Ид, Код,
    КодПоставщика) — по нему строка реализации садится на ту же карточку, что в заказе.
    """
    try:
        order = ET.fromstring(order_xml.encode("windows-1251") if order_xml.lstrip().startswith("<?xml") else order_xml)
        root = ET.fromstring(template_xml.encode("windows-1251") if template_xml.lstrip().startswith("<?xml") else template_xml)
    except (ET.ParseError, UnicodeEncodeError) as exc:
        raise UpdError(f"Не удалось разобрать XML: {exc}") from exc

    odoc = order.find("Документ")
    doc = root.find("Документ")
    inv = doc.find("СвСчФакт") if doc is not None else None
    table = doc.find("ТаблСчФакт") if doc is not None else None
    if odoc is None or inv is None or table is None:
        raise UpdError("В заказе или образце УПД нет нужных блоков")
    if (doc.get("КНД"), root.get("ВерсФорм")) != ("1115131", "5.03"):
        raise UpdError("Образец УПД не того формата: нужен 5.03 (КНД 1115131)")

    order_no = odoc.get("Номер") or ""
    if not re.fullmatch(r"УР-\d{8}", order_no):
        raise UpdError(f"Номер заказа «{order_no}» не по маске УР- и 8 цифр")
    order_date = odoc.get("Дата") or ""
    supplier = odoc.find("Поставщик")
    buyer = odoc.find("Покупатель")
    point = odoc.find("Грузополучатель")
    if supplier is None or buyer is None or point is None:
        raise UpdError("В заказе нет поставщика, покупателя или грузополучателя")
    our_gln = supplier.get("GLN") or ""
    point_gln = point.get("GLN") or ""
    buyer_ul = buyer.find("СвЮЛ")
    point_ul = point.find("СвЮЛ")
    if not our_gln or not point_gln or buyer_ul is None:
        raise UpdError("В заказе нет GLN поставщика или точки")
    buyer_inn = buyer_ul.get("ИНН") or ""
    buyer_name = buyer_ul.get("Название") or buyer.get("Название") or ""
    buyer_addr = (buyer.find("Адрес").get("АдрТекст") if buyer.find("Адрес") is not None else "") or ""
    point_kpp = (point_ul.get("КПП") if point_ul is not None else "") or ""
    point_addr = (point.find("Адрес").get("АдрТекст") if point.find("Адрес") is not None else "") or ""
    if not point_addr:
        raise UpdError("В заказе нет адреса точки")

    stamp = day.strftime("%d.%m.%Y")
    now = now or datetime.now()

    # Шапка документа
    if not sender_box:
        # В имени файла образца: ON_NSCHFDOPPR_<получатель>_<мы>_… — наш ящик третьей частью
        parts = (root.get("ИдФайл") or "").split("_")
        sender_box = parts[3] if len(parts) > 3 else ""
    if not receiver_box or not sender_box:
        raise UpdError("Не известен ящик ЭДО получателя или наш")
    file_id = f"ON_NSCHFDOPPR_{receiver_box}_{sender_box}_{day.strftime('%Y%m%d')}_{str(uuid.uuid4()).upper()}_0_0_0_0_0_00"
    root.set("ИдФайл", file_id)
    doc.set("ДатаИнфПр", now.strftime("%d.%m.%Y"))
    doc.set("ВремИнфПр", now.strftime("%H.%M.%S"))
    inv.set("ДатаДок", stamp)
    inv.set("НомерДок", number)
    for tag in ("ИспрСчФ",):
        old = inv.find(tag)
        if old is not None:
            inv.remove(old)

    # Покупатель и грузополучатель — заново. Продавца и грузоотправителя оставляем как в образце
    children = {child.tag: child for child in list(inv)}
    for child in list(inv):
        inv.remove(child)
    seller = children.get("СвПрод")
    shipper = children.get("ГрузОт")
    money = children.get("ДенИзм")
    if seller is None:
        raise UpdError("В образце нет продавца")
    inv.append(seller)
    if shipper is not None:
        inv.append(shipper)
    _org(inv, "ГрузПолуч", buyer_inn, point_kpp, buyer_name, point_gln, point_addr, buyer_name)
    ET.SubElement(inv, "ДокПодтвОтгрНом", {"РеквДатаДок": stamp, "РеквНаимДок": "Универсальный передаточный документ",
                                           "РеквНомерДок": number})
    _org(inv, "СвПокуп", buyer_inn, buyer_ul.get("КПП") or "", buyer_name, buyer.get("GLN") or "", buyer_addr, buyer_name)
    if money is None:
        money = ET.Element("ДенИзм", {"КодОКВ": "643", "НаимОКВ": "Российский рубль"})
    inv.append(money)
    info = ET.SubElement(inv, "ИнфПолФХЖ1")
    for key, value in (
        # Поля Самоката (требования, «Обязательные информационные поля по заголовку»)
        ("отправитель", our_gln),
        ("грузополучатель", point_gln),
        # Проверка Saby для сети Самокат (протокол ФЛК по № 7387 от 17.11) требует ещё
        # дату заказа и GLN покупателя — в требованиях Самоката их нет, но без них не отправить
        ("покупатель", buyer.get("GLN") or ""),
        ("номер_заказа", order_no),
        ("дата_заказа", order_date),
        ("номер_накладной", number),
        ("номер_договора", contract.number),
        ("дата_договора", contract.date),
        # Те же сведения под именами, которые СБИС пишет в свои УПД по заказам EDI
        ("ЗаказНомер", order_no),
        ("ЗаказДата", order_date),
        ("Срок", odoc.get("Срок") or ""),
        ("СрокВремя", odoc.get("СрокВремя") or ""),
        ("НакладнаяНомер", number),
        ("НакладнаяДата", stamp),
        ("ОтгрузкаДата", stamp),
        ("GLNГрузПолуч", point_gln),
        ("GLNПокуп", buyer.get("GLN") or ""),
        ("GLNПост", our_gln),
    ):
        if value:
            ET.SubElement(info, "ТекстИнф", {"Значен": value, "Идентиф": key})

    # Строки — из заказа
    for child in list(table):
        table.remove(child)
    total_net = total_gross = total_vat = total_qty = Decimal("0")
    for position, row in enumerate(odoc.iter("СтрТабл"), start=1):
        qty = _num(row.get("Кол_во"))
        gross = _num(row.get("Сумма"))
        net = _num(row.get("СуммаБезНал"))
        vat_node = row.find("НДС")
        vat = _num(vat_node.get("Сумма")) if vat_node is not None else gross - net
        rate = _rate(vat_node.get("Ставка") if vat_node is not None else "")
        gtin = row.get("GTIN") or ""
        buyer_code = row.get("КодПокупателя") or ""
        if qty <= 0 or not gtin or not buyer_code:
            raise UpdError(f"Строка {position}: нет количества, штрих-кода или кода покупателя")
        item_code = codes.get(buyer_code) or ""
        if not item_code:
            raise UpdError(f"«{row.get('Название')}» ({buyer_code}) в заказе не сопоставлена с каталогом — "
                           "сопоставьте в заказе, иначе реализация сядет не на ту карточку")
        okei = OKEI.get(row.get("ОКЕИ") or "", row.get("ОКЕИ") or "796")
        line = ET.SubElement(table, "СведТов", {
            "КолТов": _qty(qty), "НаимЕдИзм": UNITS.get(okei, "шт"), "НаимТов": row.get("Название") or "",
            "НалСт": rate, "НомСтр": str(position), "ОКЕИ_Тов": okei,
            "СтТовБезНДС": _money(net), "СтТовУчНал": _money(gross), "ЦенаТов": _money(net / qty),
        })
        ET.SubElement(line, "ДопСведТов", {"КодТов": item_code, "ПрТовРаб": "1"})
        excise = ET.SubElement(line, "Акциз")
        ET.SubElement(excise, "БезАкциз").text = "без акциза"
        tax = ET.SubElement(line, "СумНал")
        ET.SubElement(tax, "СумНал").text = _money(vat)
        name = row.get("Название") or ""
        # «КодПокупателя» — под именем, которое ищет проверка Saby (протокол ФЛК: «не указан
        # КодПокупателя»); «код_покупателя» — под именем из требований Самоката
        for key, value in (("штрихкод", gtin), ("код_покупателя", buyer_code), ("КодПокупателя", buyer_code),
                           # Дальше — как в УПД, которые СБИС делает сам по заказам EDI
                           ("НазваниеПокупателя", name), ("GTIN", gtin), ("ЗаказНомер", order_no),
                           ("Ид", item_code), ("ШК", item_code), ("Код", item_code), ("КодПоставщика", item_code),
                           ("НазваниеПоставщика", name), ("ПоляНоменклатуры", '"Type":"True"')):
            ET.SubElement(line, "ИнфПолФХЖ2", {"Значен": value, "Идентиф": key})
        total_net += net
        total_gross += gross
        total_vat += vat
        total_qty += qty
    if total_qty <= 0:
        raise UpdError("В заказе нет ни одной строки")
    totals = ET.SubElement(table, "ВсегоОпл", {"КолНеттоВс": _qty(total_qty), "СтТовБезНДСВсего": _money(total_net),
                                               "СтТовУчНалВсего": _money(total_gross)})
    tax_total = ET.SubElement(totals, "СумНалВсего")
    ET.SubElement(tax_total, "СумНал").text = _money(total_vat)

    # Основания передачи: договор (требование Самоката) и заказ
    transfer = doc.find("СвПродПер")
    if transfer is None:
        raise UpdError("В образце нет сведений о передаче")
    for child in list(transfer):
        transfer.remove(child)
    passed = ET.SubElement(transfer, "СвПер", {"ДатаПер": stamp, "СодОпер": "Товары переданы"})
    ET.SubElement(passed, "ОснПер", {"РеквДатаДок": contract.date, "РеквНаимДок": "Договор",
                                     "РеквНомерДок": contract.number})
    ET.SubElement(passed, "ОснПер", {"РеквДатаДок": order_date, "РеквНаимДок": "Заказ", "РеквНомерДок": order_no})

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="windows-1251", xml_declaration=True)
    body = re.sub(rb"<\?xml version='1.0' encoding='windows-1251'\?>", b'<?xml version="1.0" encoding="WINDOWS-1251" ?>', body, count=1)
    return file_id, body


def totals(order_xml: str) -> Tuple[int, Decimal]:
    """Штук и сумма с НДС по заказу — для сверки с реестром 1С."""
    order = ET.fromstring(order_xml.encode("windows-1251") if order_xml.lstrip().startswith("<?xml") else order_xml)
    qty, gross = Decimal("0"), Decimal("0")
    for row in order.iter("СтрТабл"):
        qty += _num(row.get("Кол_во"))
        gross += _num(row.get("Сумма"))
    return int(qty), gross
