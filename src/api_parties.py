"""Юрлица (мы и покупатель) и перевозчики — стороны документов для Лавки и СБИС.

Смотреть могут все вошедшие: менеджеру нужны реквизиты при отгрузке.
Менять — только админ.
"""
from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src import auth
from src.database import Carrier, LegalEntity, get_db

router = APIRouter()

# Значения из формы заказа на перевозку в Saby — API принимает их как есть
VEHICLE_TYPES = ["легковая", "грузовая", "фургон", "тягач"]
BODY_TYPES = ["закрытый", "тент", "изотермический", "рефрижератор", "открытый"]
LOADING_TYPES = ["ручная", "задняя", "боковая", "верхняя", "навалом"]


# --- Проверка реквизитов ---

def _digits(value: Optional[str]) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def inn_is_valid(inn: str) -> bool:
    """Контрольные цифры ИНН: 10 цифр у организации, 12 у ИП и физлица."""
    if not inn.isdigit():
        return False
    def check(digits: str, weights: List[int]) -> int:
        return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10
    if len(inn) == 10:
        return check(inn, [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(inn[9])
    if len(inn) == 12:
        return (check(inn, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(inn[10])
                and check(inn, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(inn[11]))
    return False


def ogrn_is_valid(ogrn: str) -> bool:
    """ОГРН — 13 цифр, ОГРНИП — 15; последняя цифра контрольная."""
    if not ogrn.isdigit():
        return False
    if len(ogrn) == 13:
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    if len(ogrn) == 15:
        return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])
    return False


def check_requisites(kind: str, inn: str, kpp: Optional[str], ogrn: Optional[str]) -> None:
    if not inn:
        raise HTTPException(status_code=400, detail="Укажите ИНН: по нему СБИС узнаёт сторону документа")
    if not inn_is_valid(inn):
        raise HTTPException(status_code=400, detail="ИНН с ошибкой: проверьте цифры")
    if kind == "ЮЛ" and len(inn) != 10:
        raise HTTPException(status_code=400, detail="У организации ИНН из 10 цифр")
    if kind in ("ИП", "ФЛ") and len(inn) != 12:
        raise HTTPException(status_code=400, detail="У ИП и физлица ИНН из 12 цифр")
    if kpp and not (len(kpp) == 9 and kpp[:4].isdigit()):
        raise HTTPException(status_code=400, detail="КПП — 9 знаков")
    if ogrn and not ogrn_is_valid(ogrn):
        raise HTTPException(status_code=400, detail="ОГРН с ошибкой: 13 цифр у организации, 15 у ИП")
    if ogrn and kind == "ИП" and len(ogrn) != 15:
        raise HTTPException(status_code=400, detail="У ИП ОГРНИП из 15 цифр")


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def person_name(last: Optional[str], first: Optional[str], middle: Optional[str]) -> str:
    return " ".join(p for p in (last, first, middle) if p)


# --- Юрлица ---

class EntityIn(BaseModel):
    role: Literal["own", "buyer"] = "own"
    kind: Literal["ИП", "ЮЛ"] = "ИП"
    name: Optional[str] = Field(default=None, max_length=200)
    full_name: Optional[str] = Field(default=None, max_length=300)
    inn: str = Field(max_length=20)
    kpp: Optional[str] = Field(default=None, max_length=20)
    ogrn: Optional[str] = Field(default=None, max_length=20)
    last_name: Optional[str] = Field(default=None, max_length=80)
    first_name: Optional[str] = Field(default=None, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    address: Optional[str] = Field(default=None, max_length=400)
    phone: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=150)
    edo_id: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=2000)


class EntityOut(EntityIn):
    id: int
    name: str
    is_default: bool
    is_active: bool

    class Config:
        from_attributes = True


class ActiveIn(BaseModel):
    active: bool


def _apply_entity(entity: LegalEntity, data: EntityIn) -> None:
    entity.role = data.role
    entity.kind = data.kind
    entity.inn = _digits(data.inn)
    entity.kpp = _clean(data.kpp) if data.kind == "ЮЛ" else None
    entity.ogrn = _digits(data.ogrn) or None
    is_person = data.kind == "ИП"
    entity.last_name = _clean(data.last_name) if is_person else None
    entity.first_name = _clean(data.first_name) if is_person else None
    entity.middle_name = _clean(data.middle_name) if is_person else None
    entity.full_name = None if is_person else _clean(data.full_name)
    for field in ("address", "phone", "email", "edo_id", "notes"):
        setattr(entity, field, _clean(getattr(data, field)))
    name = _clean(data.name)
    if not name:
        if is_person:
            fio = person_name(entity.last_name, entity.first_name, entity.middle_name)
            name = f"ИП {fio}" if fio else None
        else:
            name = entity.full_name
    if not name:
        raise HTTPException(status_code=400, detail="Укажите ФИО предпринимателя" if is_person else "Укажите наименование организации")
    if is_person and not (entity.last_name and entity.first_name):
        raise HTTPException(status_code=400, detail="Для ИП нужны фамилия и имя: они идут в документы")
    entity.name = name
    check_requisites(entity.kind, entity.inn, entity.kpp, entity.ogrn)


def _get_entity(db: Session, entity_id: int) -> LegalEntity:
    entity = db.get(LegalEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Юрлицо не найдено")
    return entity


def _ensure_default(db: Session, role: str) -> None:
    """У каждой роли ровно одно юрлицо по умолчанию, если есть активные."""
    active = db.query(LegalEntity).filter(LegalEntity.role == role, LegalEntity.is_active.is_(True)).order_by(LegalEntity.id).all()
    defaults = [e for e in active if e.is_default]
    if active and not defaults:
        active[0].is_default = True
    for extra in defaults[1:]:
        extra.is_default = False
    for entity in db.query(LegalEntity).filter(LegalEntity.role == role, LegalEntity.is_active.is_(False)):
        entity.is_default = False


@router.get("/legal-entities", response_model=List[EntityOut], tags=["Юрлица"])
def list_entities(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    return db.query(LegalEntity).order_by(LegalEntity.role.desc(), LegalEntity.is_active.desc(), LegalEntity.name).all()


@router.post("/legal-entities", response_model=EntityOut, tags=["Юрлица"])
def create_entity(data: EntityIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    entity = LegalEntity()
    _apply_entity(entity, data)
    duplicate = db.query(LegalEntity).filter(LegalEntity.role == entity.role, LegalEntity.inn == entity.inn).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Юрлицо с этим ИНН уже есть: {duplicate.name}")
    db.add(entity)
    db.flush()
    _ensure_default(db, entity.role)
    db.commit()
    db.refresh(entity)
    return entity


@router.put("/legal-entities/{entity_id}", response_model=EntityOut, tags=["Юрлица"])
def update_entity(entity_id: int, data: EntityIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    entity = _get_entity(db, entity_id)
    old_role = entity.role
    _apply_entity(entity, data)
    duplicate = db.query(LegalEntity).filter(
        LegalEntity.role == entity.role, LegalEntity.inn == entity.inn, LegalEntity.id != entity.id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Юрлицо с этим ИНН уже есть: {duplicate.name}")
    if old_role != entity.role:
        entity.is_default = False
        _ensure_default(db, old_role)
    db.flush()
    _ensure_default(db, entity.role)
    db.commit()
    db.refresh(entity)
    return entity


@router.put("/legal-entities/{entity_id}/active", response_model=EntityOut, tags=["Юрлица"])
def set_entity_active(entity_id: int, data: ActiveIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    entity = _get_entity(db, entity_id)
    entity.is_active = data.active
    db.flush()
    _ensure_default(db, entity.role)
    db.commit()
    db.refresh(entity)
    return entity


@router.put("/legal-entities/{entity_id}/default", response_model=EntityOut, tags=["Юрлица"])
def set_entity_default(entity_id: int, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    entity = _get_entity(db, entity_id)
    if not entity.is_active:
        raise HTTPException(status_code=400, detail="Сначала верните юрлицо из скрытых")
    for other in db.query(LegalEntity).filter(LegalEntity.role == entity.role):
        other.is_default = other.id == entity.id
    db.commit()
    db.refresh(entity)
    return entity


# --- Перевозчики ---

class CarrierIn(BaseModel):
    kind: Literal["ИП", "ЮЛ", "ФЛ"] = "ИП"
    name: Optional[str] = Field(default=None, max_length=200)
    inn: str = Field(max_length=20)
    ogrn: Optional[str] = Field(default=None, max_length=20)
    kpp: Optional[str] = Field(default=None, max_length=20)
    last_name: Optional[str] = Field(default=None, max_length=80)
    first_name: Optional[str] = Field(default=None, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    birth_date: Optional[date] = None
    birth_place: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=150)
    address: Optional[str] = Field(default=None, max_length=300)
    vehicle_model: Optional[str] = Field(default=None, max_length=120)
    vehicle_plate: Optional[str] = Field(default=None, max_length=20)
    vehicle_type: str = "легковая"
    body_type: str = "закрытый"
    loading_type: str = "ручная"
    capacity_t: Optional[float] = Field(default=None, gt=0, le=100)
    volume_m3: Optional[float] = Field(default=None, gt=0, le=200)
    licence_number: Optional[str] = Field(default=None, max_length=30)
    licence_from: Optional[date] = None
    licence_to: Optional[date] = None
    licence_region: Optional[str] = Field(default=None, max_length=120)
    licence_categories: Optional[str] = Field(default=None, max_length=60)
    licence_gibdd: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("vehicle_type")
    @classmethod
    def _vehicle(cls, v):
        if v not in VEHICLE_TYPES:
            raise ValueError("Тип машины не из списка")
        return v

    @field_validator("body_type")
    @classmethod
    def _body(cls, v):
        if v not in BODY_TYPES:
            raise ValueError("Тип кузова не из списка")
        return v

    @field_validator("loading_type")
    @classmethod
    def _loading(cls, v):
        if v not in LOADING_TYPES:
            raise ValueError("Тип погрузки не из списка")
        return v


class CarrierOut(CarrierIn):
    id: int
    name: str
    is_active: bool

    class Config:
        from_attributes = True


def _apply_carrier(carrier: Carrier, data: CarrierIn) -> None:
    text_fields = (
        "last_name", "first_name", "middle_name", "birth_place", "phone", "email", "address",
        "vehicle_model", "licence_number", "licence_region", "licence_categories", "licence_gibdd", "notes",
    )
    for field in text_fields:
        setattr(carrier, field, _clean(getattr(data, field)))
    carrier.kind = data.kind
    carrier.inn = _digits(data.inn)
    carrier.ogrn = _digits(data.ogrn) or None
    carrier.kpp = _clean(data.kpp) if data.kind == "ЮЛ" else None
    carrier.vehicle_plate = (_clean(data.vehicle_plate) or "").upper() or None
    for field in ("birth_date", "vehicle_type", "body_type", "loading_type", "capacity_t", "volume_m3",
                  "licence_from", "licence_to"):
        setattr(carrier, field, getattr(data, field))
    # Имя для списков собираем сами, если его не ввели отдельно
    carrier.name = _clean(data.name) or person_name(carrier.last_name, carrier.first_name, carrier.middle_name)
    if not carrier.name:
        raise HTTPException(status_code=400, detail="Укажите фамилию и имя или название")
    if carrier.licence_from and carrier.licence_to and carrier.licence_from > carrier.licence_to:
        raise HTTPException(status_code=400, detail="Права выданы позже, чем истекают")
    check_requisites(carrier.kind, carrier.inn, carrier.kpp, carrier.ogrn)


def _get_carrier(db: Session, carrier_id: int) -> Carrier:
    carrier = db.get(Carrier, carrier_id)
    if not carrier:
        raise HTTPException(status_code=404, detail="Перевозчик не найден")
    return carrier


@router.get("/carriers/options", tags=["Перевозчики"])
def carrier_options(user=Depends(auth.get_current_user)):
    return {"vehicle_types": VEHICLE_TYPES, "body_types": BODY_TYPES, "loading_types": LOADING_TYPES}


@router.get("/carriers", response_model=List[CarrierOut], tags=["Перевозчики"])
def list_carriers(db: Session = Depends(get_db), user=Depends(auth.get_current_user)):
    return db.query(Carrier).order_by(Carrier.is_active.desc(), Carrier.name).all()


@router.post("/carriers", response_model=CarrierOut, tags=["Перевозчики"])
def create_carrier(data: CarrierIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    carrier = Carrier()
    _apply_carrier(carrier, data)
    db.add(carrier)
    db.commit()
    db.refresh(carrier)
    return carrier


@router.put("/carriers/{carrier_id}", response_model=CarrierOut, tags=["Перевозчики"])
def update_carrier(carrier_id: int, data: CarrierIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    carrier = _get_carrier(db, carrier_id)
    _apply_carrier(carrier, data)
    db.commit()
    db.refresh(carrier)
    return carrier


@router.put("/carriers/{carrier_id}/active", response_model=CarrierOut, tags=["Перевозчики"])
def set_carrier_active(carrier_id: int, data: ActiveIn, db: Session = Depends(get_db), admin=Depends(auth.admin_only)):
    carrier = _get_carrier(db, carrier_id)
    carrier.is_active = data.active
    db.commit()
    db.refresh(carrier)
    return carrier
