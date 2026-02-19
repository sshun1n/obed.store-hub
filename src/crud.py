from sqlalchemy.orm import Session
from typing import List, Optional
from src import database, schemas
import bcrypt

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БЕЗОПАСНОСТИ ---

def get_password_hash(password: str) -> str:
    # Генерирует безопасный хеш пароля
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Проверяет, совпадает ли введенный пароль с хешем из базы
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- CRUD ДЛЯ КАТЕГОРИЙ ---

def get_category(db: Session, category_id: int) -> Optional[database.Category]:
    return db.query(database.Category).filter(database.Category.id == category_id).first()

def get_categories(db: Session, skip: int = 0, limit: int = 100) -> List[database.Category]:
    # Получаем корневые категории
    return db.query(database.Category).filter(database.Category.parent_id == None).offset(skip).limit(limit).all()

def create_category(db: Session, category: schemas.CategoryCreate) -> database.Category:
    db_category = database.Category(name=category.name, parent_id=category.parent_id)
    db.add(db_category)
    db.commit()
    db.refresh(db_category)
    return db_category

def update_category(db: Session, category_id: int, category_data: schemas.CategoryCreate) -> Optional[database.Category]:
    db_category = get_category(db, category_id)
    if not db_category:
        return None
    db_category.name = category_data.name
    db_category.parent_id = category_data.parent_id
    db.commit()
    db.refresh(db_category)
    return db_category

def delete_category(db: Session, category_id: int) -> bool:
    db_category = get_category(db, category_id)
    if not db_category or db_category.children:
        return False
    db.delete(db_category)
    db.commit()
    return True

# --- CRUD ДЛЯ ПРОДУКТОВ ---

def get_products(db: Session, skip: int = 0, limit: int = 500) -> List[database.Product]:
    return db.query(database.Product).offset(skip).limit(limit).all()

def sync_products(db: Session, names: List[str]) -> int:
    added_count = 0
    existing_names = {p.name_from_sheet for p in db.query(database.Product.name_from_sheet).all()}
    for name in names:
        if name not in existing_names:
            db_product = database.Product(name_from_sheet=name)
            db.add(db_product)
            existing_names.add(name)
            added_count += 1
    db.commit()
    return added_count

def update_product_categories(db: Session, product_id: int, category_ids: List[int]) -> Optional[database.Product]:
    db_product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not db_product:
        return None
    categories = db.query(database.Category).filter(database.Category.id.in_(category_ids)).all()
    db_product.categories = categories
    db.commit()
    db.refresh(db_product)
    return db_product

# --- CRUD ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---

def get_user(db: Session, user_id: int) -> Optional[database.User]:
    return db.query(database.User).filter(database.User.id == user_id).first()

def get_user_by_login(db: Session, login: str) -> Optional[database.User]:
    return db.query(database.User).filter(database.User.login == login).first()

def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[database.User]:
    return db.query(database.User).offset(skip).limit(limit).all()

def create_user(db: Session, user: schemas.UserCreate) -> database.User:
    hashed_pwd = get_password_hash(user.password)
    db_user = database.User(
        login=user.login,
        hashed_password=hashed_pwd,
        full_name=user.full_name,
        role=user.role,
        phone=user.phone
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(db: Session, user_id: int, user_data: schemas.UserUpdate) -> Optional[database.User]:
    db_user = get_user(db, user_id)
    if not db_user: return None
    update_dict = user_data.dict(exclude_unset=True)
    if "password" in update_dict:
        update_dict["hashed_password"] = get_password_hash(update_dict.pop("password"))
    for key, value in update_dict.items():
        setattr(db_user, key, value)
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, user_id: int) -> bool:
    db_user = get_user(db, user_id)
    if not db_user: return False
    db.delete(db_user)
    db.commit()
    return True

# --- CRUD ДЛЯ GOOGLE CONFIGS ---

def get_google_configs(db: Session) -> List[database.GoogleConfig]:
    return db.query(database.GoogleConfig).order_by(database.GoogleConfig.created_at.desc()).all()

def get_active_google_config(db: Session) -> Optional[database.GoogleConfig]:
    return db.query(database.GoogleConfig).filter(database.GoogleConfig.is_active == True).first()

def create_google_config(db: Session, config: schemas.GoogleConfigCreate) -> database.GoogleConfig:
    db_config = database.GoogleConfig(**config.dict())
    db.add(db_config)
    db.commit()
    db.refresh(db_config)
    return db_config

def activate_google_config(db: Session, config_id: int) -> Optional[database.GoogleConfig]:
    db.query(database.GoogleConfig).update({database.GoogleConfig.is_active: False})
    db_config = db.query(database.GoogleConfig).filter(database.GoogleConfig.id == config_id).first()
    if db_config:
        db_config.is_active = True
        db.commit()
        db.refresh(db_config)
    return db_config

def delete_google_config(db: Session, config_id: int) -> bool:
    db_config = db.query(database.GoogleConfig).filter(database.GoogleConfig.id == config_id).first()
    if not db_config: return False
    db.delete(db_config)
    db.commit()
    return True
