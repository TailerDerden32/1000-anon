FROM python:3.9-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements первым для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание директории для данных
RUN mkdir -p /app/data

# Установка переменных окружения
ENV PYTHONUNBUFFERED=1

# Запуск бота
CMD ["python", "bot.py"]
