import random
from sqlalchemy.orm import Session
from database import get_db, Product, Category
import crud

def get_all_categories_flat(db: Session):
    """Возвращает плоский список всех категорий."""
    return db.query(Category).all()

def seed_random_product_categories(db: Session):
    """
    Присваивает каждому продукту от 1 до 3 случайных категорий.
    Дочерние категории имеют более высокий шанс быть выбранными.
    """
    print("Загрузка продуктов и категорий...")
    products = crud.get_products(db)
    all_categories = get_all_categories_flat(db)
    
    if not products:
        print("В базе данных нет продуктов. Запустите синхронизацию.")
        return
        
    if not all_categories:
        print("В базе данных нет категорий. Создайте их в интерфейсе.")
        return

    print(f"Найдено {len(products)} продуктов и {len(all_categories)} категорий.")
    
    # Придаем больший "вес" дочерним категориям
    weighted_categories = []
    for cat in all_categories:
        # Категории без детей (листья дерева) наиболее вероятны
        if not cat.children:
            weighted_categories.extend([cat] * 5)
        # Категории с детьми менее вероятны
        else:
            weighted_categories.append(cat)

    for product in products:
        # Выбираем, сколько категорий присвоить (от 1 до 3)
        num_categories_to_assign = random.randint(1, min(3, len(all_categories)))
        
        # Выбираем случайные категории с учетом веса, но затем убираем дубликаты
        choices = random.choices(weighted_categories, k=num_categories_to_assign)
        assigned_categories = list(set(choices)) # Преобразование в set и обратно в list
        
        # Обновляем связи в базе данных
        product.categories = assigned_categories
        print(f"- Продукту '{product.name_from_sheet[:40]}...' присвоены категории: {[c.name for c in assigned_categories]}")

    print("Сохранение изменений в базе данных...")
    db.commit()
    print("✅ Успешно! Тестовые данные сгенерированы.")


if __name__ == "__main__":
    print("Запуск скрипта для генерации тестовых связей...")
    db_session = next(get_db())
    seed_random_product_categories(db_session)
