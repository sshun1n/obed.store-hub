# Используем официальный легкий образ Python
FROM python:3.10-slim

# Установка системных зависимостей для psycopg2 и шрифтов
RUN apt-get update && apt-get install -y 
    libpq-dev 
    gcc 
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Установка зависимостей Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код и статику
COPY src/ ./src/
COPY static/ ./static/
COPY main.py .

# Создаем папку для отчетов
RUN mkdir -p reports

# Открываем порт
EXPOSE 8282

# Запуск приложения
CMD ["python", "main.py"]
