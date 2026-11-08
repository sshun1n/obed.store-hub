# Используем официальный легкий образ Python
FROM python:3.10-slim

# Системные зависимости: psycopg2, шрифты и Pango для WeasyPrint (лист номенклатуры Лавки
# в PDF, как в ka-webapp). fonts-dejavu-core — ради кириллицы: в slim-образе шрифтов нет
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    fonts-liberation \
    fonts-dejavu-core \
    fontconfig \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# fontconfig держит кэш шрифтов в домашнем каталоге; без явного пути сыплет ошибками при сборке PDF
ENV XDG_CACHE_HOME=/tmp/.cache

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
COPY init_db.py .

# Создаем папку для отчетов
RUN mkdir -p reports

# Открываем порт
EXPOSE 8282

# Запуск приложения
CMD ["python", "main.py"]
