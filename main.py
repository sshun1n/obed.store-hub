import os
import uvicorn
from src.api import app

# Точка входа для запуска приложения.
# На сервере используется порт 8282 (прописан в Dockerfile и compose).

if __name__ == "__main__":
    # Считываем настройки из окружения, чтобы быть гибкими
    port = int(os.getenv("PORT", 8282))
    # На сервере (в Docker) reload обычно выключают для стабильности
    # Но для удобства оставим управление через переменную или просто дефолт
    is_debug = os.getenv("ENV", "prod") == "dev"
    
    print(f"--- Запуск OBED.STORE Hub (Порт: {port}, Debug: {is_debug}) ---")
    
    uvicorn.run(
        "src.api:app", 
        host="0.0.0.0", 
        port=port, 
        reload=is_debug
    )
