"""Шифрование паролей внешних систем, которые хранятся в базе.

Ключ выводится из SECRET_KEY. Сменится SECRET_KEY — сохранённые пароли
перестанут расшифровываться, их нужно будет ввести заново. Копия базы на
другом стенде со своим SECRET_KEY тоже не прочитает чужие пароли.
"""
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from src.auth import SECRET_KEY


def _fernet() -> Fernet:
    digest = hashlib.sha256(("obed-hub:" + SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    """Расшифрованное значение или None, если его нет или ключ не подходит."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
