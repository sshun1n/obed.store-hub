"""Генерация и проверка штрих-кодов EAN-13, картинка для этикетки.

Параметры картинки те же, что в KA web app: код ставят на этикетку, поэтому
широкие белые поля срезаны, а DPI записан в файл явно.
"""
import io
import secrets
from typing import Optional

from barcode import EAN13
from barcode.writer import ImageWriter
from PIL import Image, ImageOps

INTERNAL_PREFIX = "200"  # диапазон 20–29 зарезервирован для внутреннего использования

DPI = 300
# Поле по бокам и под цифрами: крайняя полоса и низ цифр не должны срезаться при
# печати. Тихую зону, которая нужна сканеру, обеспечивает раскладка этикетки
EDGE_MARGIN_MM = 0.5


def checksum(digits12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits12))
    return str((10 - total % 10) % 10)


def normalize(code: str) -> Optional[str]:
    """Приводит код к EAN-13: 12 цифр дополняет контрольной. None, если код некорректен."""
    code = code.strip().replace(" ", "")
    if not (code.isascii() and code.isdigit()):
        return None
    if len(code) == 12:
        return code + checksum(code)
    if len(code) == 13 and code[12] == checksum(code[:12]):
        return code
    return None


def generate() -> str:
    body = INTERNAL_PREFIX + "".join(secrets.choice("0123456789") for _ in range(9))
    return body + checksum(body)


def render_png(code: str) -> bytes:
    buffer = io.BytesIO()
    EAN13(code, writer=ImageWriter()).write(
        buffer,
        options={
            "module_height": 12.0,
            "font_size": 8,
            "text_distance": 4.0,
            "quiet_zone": 4.0,
            "dpi": DPI,
        },
    )
    return _trim(buffer.getvalue())


def _trim(png: bytes) -> bytes:
    """Срезает белые поля слева, справа и снизу, оставляя EDGE_MARGIN_MM.

    Верх не трогаем: над полосами и так меньше миллиметра. DPI сохраняем явно:
    без него редактор этикеток возьмёт 72 точки на дюйм и растянет код вчетверо.
    """
    image = Image.open(io.BytesIO(png))
    dark = ImageOps.invert(image.convert("L")).point(lambda v: 255 if v > 127 else 0)
    box = dark.getbbox()
    if box is None:
        return png
    margin = round(EDGE_MARGIN_MM / 25.4 * DPI)
    left = max(0, box[0] - margin)
    right = min(image.width, box[2] + margin)
    bottom = min(image.height, box[3] + margin)
    out = io.BytesIO()
    image.crop((left, 0, right, bottom)).save(out, format="PNG", dpi=(DPI, DPI))
    return out.getvalue()
