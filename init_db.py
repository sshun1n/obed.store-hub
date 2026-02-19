# Скрипт полной инициализации базы данных проекта OBED.STORE
# Создает структуру таблиц и добавляет базового администратора.

from database import init_db, SessionLocal
import crud
import schemas

def setup_initial_data():
    """Создает начальные данные в базе (администратора)."""
    db = SessionLocal()
    try:
        # Проверяем, существует ли уже пользователь admin
        admin_user = crud.get_user_by_login(db, login="admin")
        if not admin_user:
            print("Создание администратора по умолчанию (admin:admin)...")
            admin_in = schemas.UserCreate(
                login="admin",
                password="admin",
                full_name="Системный администратор",
                role="admin"
            )
            crud.create_user(db, admin_in)
            print("Администратор успешно создан.")
        else:
            print("Администратор уже существует.")
    finally:
        db.close()

if __name__ == "__main__":
    print("--- Инициализация базы данных PostgreSQL ---")
    init_db()  # Вызывает SQLAlchemy для создания таблиц
    setup_initial_data() 
    print("--- Процесс завершен успешно ---")
