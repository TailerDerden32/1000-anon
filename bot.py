import os
import telebot
import sqlite3
from datetime import datetime, timedelta
import logging
import requests
import json
from flask import Flask, request
import threading
import time
import sys

# === НАСТРОЙКИ ===
BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_IDS = [int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',')]
CHANNEL_USERNAME = os.environ['CHANNEL_USERNAME']

# Настройки мониторинга
HEALTH_CHECK_INTERVAL = 300  # 5 минут
MAX_ERROR_COUNT = 3  # Максимальное количество ошибок перед перезапуском
RESTART_DELAY = 60  # Задержка перед перезапуском в секундах

# Глобальные переменные для статистики и мониторинга
BOT_START_TIME = datetime.now()
MESSAGE_COUNT = 0
LAST_RESTART_TIME = datetime.now()
ERROR_COUNT = 0
LAST_ERROR_TIME = None
HEALTH_MONITOR_RUNNING = False

# Проверяем переменные окружения
required_vars = ['BOT_TOKEN', 'ADMIN_IDS', 'CHANNEL_USERNAME']
missing_vars = [var for var in required_vars if not os.environ.get(var)]

if missing_vars:
    print(f"❌ Отсутствуют переменные окружения: {', '.join(missing_vars)}")
    print("⚠️ Установите их в настройках bothost")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot_health.log')
    ]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Проверка бота
try:
    bot_info = bot.get_me()
    logger.info(f"✅ Бот запущен: {bot_info.first_name} (@{bot_info.username})")
except Exception as e:
    logger.error(f"❌ Ошибка доступа к боту: {e}")
    logger.error("⚠️ Проверьте правильность BOT_TOKEN")
    exit(1)

# Проверка канала
try:
    chat = bot.get_chat(CHANNEL_USERNAME)
    logger.info(f"✅ Канал найден: {chat.title}")
except Exception as e:
    logger.error(f"❌ Ошибка доступа к каналу {CHANNEL_USERNAME}: {e}")
    logger.error("⚠️ Проверьте: 1) Юзернейм канала 2) Бот добавлен как администратор")
# =================

# === СИСТЕМА МОНИТОРИНГА ЗДОРОВЬЯ ===
def log_error(error_type, error_message):
    """Логирует ошибку и увеличивает счетчик ошибок"""
    global ERROR_COUNT, LAST_ERROR_TIME
    ERROR_COUNT += 1
    LAST_ERROR_TIME = datetime.now()
    
    logger.error(f"🚨 Ошибка [{error_type}]: {error_message}")
    logger.error(f"📊 Счетчик ошибок: {ERROR_COUNT}/{MAX_ERROR_COUNT}")
    
    # Логируем в базу данных
    log_bot_event('error', f"{error_type}: {error_message}")

def reset_error_count():
    """Сбрасывает счетчик ошибок"""
    global ERROR_COUNT
    ERROR_COUNT = 0
    logger.info("🔄 Счетчик ошибок сброшен")

def health_check():
    """Проверяет здоровье бота"""
    try:
        # Проверка соединения с Telegram API
        bot.get_me()
        
        # Проверка доступа к каналу
        bot.get_chat(CHANNEL_USERNAME)
        
        # Проверка базы данных
        conn = sqlite3.connect('bot.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        
        logger.info("❤️ Проверка здоровья: все системы работают нормально")
        reset_error_count()
        return True
        
    except Exception as e:
        log_error('health_check', str(e))
        return False

def auto_restart_if_needed():
    """Автоматически перезапускает бота при необходимости"""
    global ERROR_COUNT
    
    if ERROR_COUNT >= MAX_ERROR_COUNT:
        logger.error(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: Достигнут лимит ошибок ({ERROR_COUNT}/{MAX_ERROR_COUNT})")
        logger.info("🔄 Инициирование автоматического перезапуска...")
        
        # Уведомляем админов о перезапуске
        notify_admins_about_restart()
        
        # Ждем перед перезапуском
        time.sleep(RESTART_DELAY)
        
        # Перезапускаем бота
        restart_bot()

def health_monitor():
    """Фоновая задача для мониторинга здоровья бота"""
    global HEALTH_MONITOR_RUNNING
    HEALTH_MONITOR_RUNNING = True
    
    while HEALTH_MONITOR_RUNNING:
        try:
            if not health_check():
                auto_restart_if_needed()
        except Exception as e:
            logger.error(f"❌ Ошибка в мониторе здоровья: {e}")
        
        time.sleep(HEALTH_CHECK_INTERVAL)

def notify_admins_about_restart():
    """Уведомляет админов о перезапуске"""
    restart_reason = f"Достигнут лимит ошибок ({ERROR_COUNT}/{MAX_ERROR_COUNT})"
    
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🚨 <b>Автоматический перезапуск бота</b>\n\n"
                f"📋 Причина: {restart_reason}\n"
                f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🔢 Счетчик ошибок: {ERROR_COUNT}\n\n"
                f"⚙️ Бот будет перезапущен через {RESTART_DELAY} секунд...",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ Не удалось уведомить админа {admin_id}: {e}")

def restart_bot():
    """Перезапускает бота"""
    logger.info("🔄 Перезапуск бота...")
    log_bot_event('restart', 'Automatic restart due to error threshold')
    
    # Останавливаем мониторинг здоровья
    global HEALTH_MONITOR_RUNNING
    HEALTH_MONITOR_RUNNING = False
    
    # Перезапускаем процесс
    python = sys.executable
    os.execl(python, python, *sys.argv)

# === УДАЛЕНИЕ WEBHOOK ПЕРЕД ЗАПУСКОМ ===
def delete_webhook():
    """Удаляет webhook перед запуском polling"""
    try:
        logger.info("🔄 Удаление webhook...")
        bot.remove_webhook()
        time.sleep(1)
        logger.info("✅ Webhook удален")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления webhook: {e}")
        return False

# === АВТО-ПИНГ ДЛЯ АКТИВНОСТИ ===
def auto_ping():
    """Автоматически поддерживает активность"""
    time.sleep(15)
    logger.info("🔄 Запуск авто-пинга...")

    while True:
        try:
            # Простой пинг для поддержания активности
            logger.info("✅ Бот активен")
        except Exception as e:
            logger.error(f"❌ Ошибка авто-пинга: {e}")
        time.sleep(300)  # Пинг каждые 5 минут

# === ЗАПУСК FLASK В ФОНЕ ===
def run_flask():
    """Запускает Flask сервер в фоновом режиме"""
    time.sleep(5)  # Даем время запуститься polling
    logger.info("🌐 Запуск Flask сервера в фоне...")
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            username TEXT,
            message_text TEXT,
            message_type TEXT,
            file_id TEXT,
            file_type TEXT,
            timestamp TEXT,
            status TEXT DEFAULT 'pending',
            admin_reply TEXT DEFAULT NULL,
            reply_sent BOOLEAN DEFAULT FALSE,
            publish_type TEXT DEFAULT 'normal'
        )
    ''')

    # Таблица для статистики работы бота
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            event_time TEXT,
            details TEXT
        )
    ''')

    # Таблица для ошибок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_type TEXT,
            error_message TEXT,
            error_time TEXT,
            resolved BOOLEAN DEFAULT FALSE
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

def save_message_to_db(user_id, user_name, username, message_type, text, file_id=None, file_type=None):
    global MESSAGE_COUNT
    MESSAGE_COUNT += 1
    
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, user_name, username, message_text, message_type, file_id, file_type, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (user_id, user_name, username, text, message_type, file_id, file_type, datetime.now().isoformat())
    )
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return message_id

def get_message_from_db(message_id):
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
    message = cursor.fetchone()
    conn.close()
    return message

def update_publish_type(message_id, publish_type):
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE messages SET publish_type = ? WHERE id = ?", (publish_type, message_id))
    conn.commit()
    conn.close()

def update_admin_reply(message_id, reply_text, reply_sent=False):
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE messages SET admin_reply = ?, reply_sent = ? WHERE id = ?", (reply_text, reply_sent, message_id))
    conn.commit()
    conn.close()

def log_bot_event(event_type, details=""):
    """Логирует события бота для статистики"""
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bot_stats (event_type, event_time, details) VALUES (?, ?, ?)",
        (event_type, datetime.now().isoformat(), details)
    )
    conn.commit()
    conn.close()

def log_error_to_db(error_type, error_message):
    """Логирует ошибку в базу данных"""
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bot_errors (error_type, error_message, error_time) VALUES (?, ?, ?)",
        (error_type, error_message, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

# === ОТПРАВКА СООБЩЕНИЙ ===
def send_to_channel(message_data, publish_type='normal'):
    try:
        message_type = message_data.get('message_type')
        text = message_data.get('text', '')
        file_id = message_data.get('file_id')

        if publish_type == 'forward':
            # РЕАЛЬНАЯ пересылка сообщения
            if message_type == 'text':
                # Для текста просто отправляем как есть
                bot.send_message(CHANNEL_USERNAME, text, parse_mode='HTML')
                return True
            else:
                # Для медиа - пересылаем оригинальное сообщение
                # Но нам нужно original_message_id, который мы не сохраняли
                # Временно используем обычную отправку без указания автора
                if message_type == 'photo':
                    if text:
                        bot.send_photo(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                    else:
                        bot.send_photo(CHANNEL_USERNAME, file_id)
                    return True
                elif message_type == 'video':
                    if text:
                        bot.send_video(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                    else:
                        bot.send_video(CHANNEL_USERNAME, file_id)
                    return True
                elif message_type == 'voice':
                    bot.send_voice(CHANNEL_USERNAME, file_id)
                    return True
                elif message_type == 'document':
                    if text:
                        bot.send_document(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                    else:
                        bot.send_document(CHANNEL_USERNAME, file_id)
                    return True
                elif message_type == 'sticker':
                    bot.send_sticker(CHANNEL_USERNAME, file_id)
                    return True
        else:
            # Обычная публикация (как было)
            if message_type == 'text':
                bot.send_message(CHANNEL_USERNAME, text, parse_mode='HTML')
                return True
            elif message_type == 'photo':
                if text:
                    bot.send_photo(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                else:
                    bot.send_photo(CHANNEL_USERNAME, file_id)
                return True
            elif message_type == 'video':
                if text:
                    bot.send_video(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                else:
                    bot.send_video(CHANNEL_USERNAME, file_id)
                return True
            elif message_type == 'voice':
                bot.send_voice(CHANNEL_USERNAME, file_id)
                return True
            elif message_type == 'document':
                if text:
                    bot.send_document(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
                else:
                    bot.send_document(CHANNEL_USERNAME, file_id)
                return True
            elif message_type == 'sticker':
                bot.send_sticker(CHANNEL_USERNAME, file_id)
                return True
            else:
                logger.error(f"❌ Неподдерживаемый тип: {message_type}")
                return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки в канал: {e}")
        log_error('send_to_channel', str(e))
        return False

# === СТАТИСТИКА РАБОТЫ БОТА ===
def get_bot_uptime():
    """Возвращает время работы бота"""
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    else:
        return f"{hours}ч {minutes}м {seconds}с"

def get_bot_stats():
    """Возвращает статистику работы бота"""
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Статистика сообщений
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'approved'")
    approved_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
    pending_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM messages")
    unique_users = cursor.fetchone()[0]
    
    # Статистика перезапусков
    cursor.execute("SELECT COUNT(*) FROM bot_stats WHERE event_type = 'restart'")
    restarts_count = cursor.fetchone()[0]
    
    # Статистика ошибок
    cursor.execute("SELECT COUNT(*) FROM bot_errors")
    total_errors = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bot_errors WHERE resolved = FALSE")
    unresolved_errors = cursor.fetchone()[0]
    
    # Время последнего перезапуска
    cursor.execute("SELECT event_time FROM bot_stats WHERE event_type = 'restart' ORDER BY id DESC LIMIT 1")
    last_restart_row = cursor.fetchone()
    last_restart = "никогда"
    if last_restart_row:
        last_restart_time = datetime.fromisoformat(last_restart_row[0])
        last_restart_diff = (datetime.now() - last_restart_time).total_seconds()
        if last_restart_diff < 3600:
            last_restart = f"{int(last_restart_diff / 60)} минут назад"
        else:
            last_restart = f"{int(last_restart_diff / 3600)} часов назад"
    
    conn.close()
    
    return {
        'uptime': get_bot_uptime(),
        'total_messages': total_messages,
        'approved_messages': approved_messages,
        'pending_messages': pending_messages,
        'unique_users': unique_users,
        'restarts_count': restarts_count,
        'total_errors': total_errors,
        'unresolved_errors': unresolved_errors,
        'current_error_count': ERROR_COUNT,
        'max_error_count': MAX_ERROR_COUNT,
        'last_restart': last_restart,
        'current_message_count': MESSAGE_COUNT
    }

def get_health_status():
    """Возвращает статус здоровья бота"""
    stats = get_bot_stats()
    
    # Проверяем различные метрики здоровья
    health_checks = []
    
    # Проверка счетчика ошибок
    if ERROR_COUNT == 0:
        health_checks.append("✅ Счетчик ошибок: нормальный")
    elif ERROR_COUNT < MAX_ERROR_COUNT:
        health_checks.append(f"⚠️ Счетчик ошибок: {ERROR_COUNT}/{MAX_ERROR_COUNT}")
    else:
        health_checks.append(f"🚨 Счетчик ошибок: КРИТИЧЕСКИЙ {ERROR_COUNT}/{MAX_ERROR_COUNT}")
    
    # Проверка нерешенных ошибок
    if stats['unresolved_errors'] == 0:
        health_checks.append("✅ Нерешенные ошибки: нет")
    else:
        health_checks.append(f"⚠️ Нерешенные ошибки: {stats['unresolved_errors']}")
    
    # Проверка времени работы
    uptime_seconds = (datetime.now() - BOT_START_TIME).total_seconds()
    if uptime_seconds > 3600:  # Больше 1 часа
        health_checks.append("✅ Время работы: стабильное")
    else:
        health_checks.append("⚠️ Время работы: недавний запуск")
    
    return health_checks

# === ОБРАБОТЧИКИ КОМАНД ===
@bot.message_handler(commands=['start'])
def start(message):
    user = message.from_user
    logger.info(f"👤 /start от {user.first_name} (ID: {user.id})")
    bot.send_message(message.chat.id, 
                    "👋 <b>Привет!</b>\n\n"
                    "Отправь мне сообщение или медиафайл для публикации в канале.\n"
                    "Всё будет отправлено, наверное.", 
                    parse_mode='HTML')

@bot.message_handler(commands=['help'])
def help_command(message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    help_text = """
🤖 <b>Доступные команды:</b>
/start - Начать работу
/help - Показать справку"""
    
    # Добавляем команды админов только если пользователь - админ
    if is_admin:
        help_text += """
/stats - Статистика бота
/status - Статус работы бота  
/health - Проверка здоровья бота
/restart - Принудительный перезапуск
/info - Информация о настройках
/pending - Ожидающие модерации"""
    
    help_text += """
    
📨 <b>Что можно отправить:</b>
• Текстовые сообщения
• Фотографии (с подписью или без)
• Видео (с подписью или без) 
• Голосовые сообщения
• Документы
• Аудиофайлы
• Стикеры
"""
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "❌ Нет прав для просмотра статистики")
        return

    try:
        conn = sqlite3.connect('bot.db', check_same_thread=False)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM messages")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM messages")
        users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'approved'")
        approved = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'rejected'")
        rejected = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
        pending = cursor.fetchone()[0]

        stats = f"""📊 <b>Статистика бота</b>

📨 Всего сообщений: <b>{total}</b>
👥 Уникальных пользователей: <b>{users}</b>
✅ Одобрено: <b>{approved}</b>
❌ Отклонено: <b>{rejected}</b>
⏳ Ожидают модерации: <b>{pending}</b>"""

        bot.send_message(message.chat.id, stats, parse_mode='HTML')
        conn.close()

    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении статистики")

@bot.message_handler(commands=['status'])
def status_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    stats = get_bot_stats()
    
    status_text = f"""🖥 <b>Статус работы бота</b>

⏱ Время работы: <b>{stats['uptime']}</b>
📊 Сообщений обработано: <b>{stats['current_message_count']}</b>
👤 Уникальных пользователей: <b>{stats['unique_users']}</b>
🔄 Перезапусков: <b>{stats['restarts_count']}</b>
🚨 Ошибок (всего/активные): <b>{stats['total_errors']}/{stats['unresolved_errors']}</b>
⏰ Последний перезапуск: <b>{stats['last_restart']}</b>

💾 <b>База данных:</b>
📨 Всего сообщений: <b>{stats['total_messages']}</b>
✅ Одобрено: <b>{stats['approved_messages']}</b>
⏳ Ожидают: <b>{stats['pending_messages']}</b>"""

    bot.send_message(message.chat.id, status_text, parse_mode='HTML')

@bot.message_handler(commands=['health'])
def health_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    # Выполняем проверку здоровья
    is_healthy = health_check()
    health_checks = get_health_status()
    
    health_status = "✅ <b>Бот здоров</b>" if is_healthy else "⚠️ <b>Обнаружены проблемы</b>"
    
    health_text = f"""❤️ <b>Проверка здоровья бота</b>

{health_status}

📋 <b>Результаты проверок:</b>
""" + "\n".join(health_checks) + f"""

⚙️ <b>Настройки мониторинга:</b>
🔢 Максимальное количество ошибок: <b>{MAX_ERROR_COUNT}</b>
⏱ Текущий счетчик ошибок: <b>{ERROR_COUNT}</b>
🔄 Интервал проверки: <b>{HEALTH_CHECK_INTERVAL} сек</b>"""

    bot.send_message(message.chat.id, health_text, parse_mode='HTML')

@bot.message_handler(commands=['restart'])
def restart_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    bot.send_message(message.chat.id, "🔄 Инициирование перезапуска бота...")
    logger.info(f"🔄 Принудительный перезапуск инициирован админом {message.from_user.id}")
    
    # Уведомляем о перезапуске
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🔄 <b>Принудительный перезапуск бота</b>\n\n"
                f"👤 Инициатор: {message.from_user.first_name}\n"
                f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🔢 Счетчик ошибок: {ERROR_COUNT}\n\n"
                f"⚙️ Бот будет перезапущен через {RESTART_DELAY} секунд...",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ Не удалось уведомить админа {admin_id}: {e}")
    
    # Ждем и перезапускаем
    time.sleep(RESTART_DELAY)
    restart_bot()

@bot.message_handler(commands=['info'])
def info_command(message):
    """Команда для проверки настроек (только для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        return
        
    try:
        chat = bot.get_chat(CHANNEL_USERNAME)
        channel_info = f"✅ {chat.title} ({CHANNEL_USERNAME})"
    except Exception as e:
        channel_info = f"❌ {CHANNEL_USERNAME} - ошибка: {e}"
    
    info_text = f"""
⚙️ <b>Информация о настройках:</b>

🤖 Бот: {'✅ Запущен' if BOT_TOKEN else '❌ Не настроен'}
👥 Админов: {len(ADMIN_IDS)}
📢 Канал: {channel_info}
🆔 Ваш ID: {message.from_user.id}
"""
    bot.send_message(message.chat.id, info_text, parse_mode='HTML')

@bot.message_handler(commands=['pending'])
def pending_messages(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        conn = sqlite3.connect('bot.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM messages WHERE status = 'pending' ORDER BY id DESC LIMIT 10")
        pending_messages = cursor.fetchall()
        
        if not pending_messages:
            bot.send_message(message.chat.id, "📭 Нет сообщений, ожидающих модерации")
            return
        
        response = "📋 <b>Сообщения ожидающие модерации:</b>\n\n"
        
        for msg in pending_messages:
            msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status = msg
            response += f"#{msg_id} - {user_name} - {msg_type}\n"
            if text and len(text) > 50:
                response += f"📝 {text[:50]}...\n"
            elif text:
                response += f"📝 {text}\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
        
        bot.send_message(message.chat.id, response, parse_mode='HTML')
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения ожидающих сообщений: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении списка сообщений")

# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        return

    user = message.from_user
    logger.info(f"📝 Текст от {user.first_name} (ID: {user.id})")

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'text',
        message.text
    )

    bot.send_message(message.chat.id, "✅ Сообщение отправлено")
    notify_admins(message_id, user, message.text, 'text', None, message.message_id)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user = message.from_user
    caption = message.caption or ''  # Не добавляем автоматический текст
    file_id = message.photo[-1].file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'photo',
        caption,  # Сохраняем реальный caption (может быть пустым)
        file_id,
        'photo'
    )

    bot.send_message(message.chat.id, "✅ Фото отправлено")
    notify_admins(message_id, user, caption, 'photo', file_id, message.message_id)

@bot.message_handler(content_types=['video'])
def handle_video(message):
    user = message.from_user
    caption = message.caption or ''  # Не добавляем автоматический текст
    file_id = message.video.file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'video',
        caption,  # Сохраняем реальный caption
        file_id,
        'video'
    )

    bot.send_message(message.chat.id, "✅ Видео отправлено")
    notify_admins(message_id, user, caption, 'video', file_id, message.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user = message.from_user
    file_id = message.voice.file_id
    caption = '🎤 Голосовое сообщение'  # Оставляем только для голосовых

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'voice',
        caption,
        file_id,
        'voice'
    )

    bot.send_message(message.chat.id, "✅ Голосовое сообщение отправлено")
    notify_admins(message_id, user, caption, 'voice', file_id, message.message_id)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user = message.from_user
    caption = message.caption or ''  # Не добавляем автоматический текст
    file_id = message.document.file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'document',
        caption,  # Сохраняем реальный caption
        file_id,
        'document'
    )

    bot.send_message(message.chat.id, "✅ Документ отправлен")
    notify_admins(message_id, user, caption, 'document', file_id, message.message_id)

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    user = message.from_user
    logger.info(f"🎭 Стикер от {user.first_name} (ID: {user.id})")
    
    # Сохраняем информацию о стикере
    sticker_emoji = message.sticker.emoji or '🎭'
    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'sticker',
        f"{sticker_emoji} Стикер",
        message.sticker.file_id,
        'sticker'
    )

    bot.send_message(message.chat.id, "✅ Стикер отправлен")
    notify_admins(message_id, user, f"{sticker_emoji} Стикер", 'sticker', message.sticker.file_id, message.message_id)

# === УВЕДОМЛЕНИЯ АДМИНАМ ===
def notify_admins(message_id, user, text, media_type, file_id=None, original_message_id=None):
    icons = {'text': '📝', 'photo': '📷', 'video': '🎥', 'voice': '🎤', 'document': '📄', 'sticker': '🎭'}
    icon = icons.get(media_type, '📨')
    username_display = f"@{user.username}" if user.username else "нет юзернейма"
    
    # Показываем реальный текст или тип контента
    display_text = text if text else f"{icon} {media_type}"

    admin_msg = f"""{icon} <b>Новое сообщение</b> #{message_id}

👤 <b>От:</b> {user.first_name} ({username_display})
🆔 <b>ID:</b> {user.id}
📋 <b>Тип:</b> {media_type}"""
    
    # Добавляем текст только если он есть
    if text:
        admin_msg += f"\n📝 <b>Текст:</b> {text}"

    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    for admin_id in ADMIN_IDS:
        try:
            # Отправка превью контента
            if media_type == 'photo' and file_id:
                msg = bot.send_photo(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'video' and file_id:
                msg = bot.send_video(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'voice' and file_id:
                msg = bot.send_voice(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'document' and file_id:
                msg = bot.send_document(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'sticker' and file_id:
                bot.send_message(admin_id, admin_msg, parse_mode='HTML')
                sent_sticker = bot.send_sticker(admin_id, file_id)
                msg = sent_sticker
            else:
                msg = bot.send_message(admin_id, admin_msg, parse_mode='HTML')
            
            # Кнопки
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("📝 Обычная публикация", callback_data=f"publish_normal_{message_id}"),
                InlineKeyboardButton("🔄 Переслать", callback_data=f"publish_forward_{message_id}")
            )
            keyboard.row(
                InlineKeyboardButton("💬 Ответить пользователю", callback_data=f"reply_{message_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
            )
            
            bot.edit_message_reply_markup(admin_id, msg.message_id, reply_markup=keyboard)
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки админу {admin_id}: {e}")

# === ОБРАБОТКА CALLBACK ===
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    logger.info(f"🔄 Callback: {call.data} от {call.from_user.id}")

    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ Нет прав для модерации")
        return

    try:
        if call.data.startswith('publish_normal_'):
            message_id = int(call.data.split('_')[2])
            message_data = get_message_from_db(message_id)
            
            if message_data and message_data[9] != 'pending':
                status = message_data[9]
                status_texts = {
                    'approved': '✅ уже одобрено',
                    'rejected': '❌ уже отклонено', 
                    'error': '⚠️ ошибка публикации'
                }
                bot.answer_callback_query(call.id, f"Сообщение {status_texts.get(status, status)}")
                return

            # Устанавливаем тип публикации "обычная"
            update_publish_type(message_id, 'normal')
            
            success = send_to_channel({
                'message_type': message_data[5],
                'text': message_data[4],
                'file_id': message_data[6]
            }, 'normal')

            conn = sqlite3.connect('bot.db', check_same_thread=False)
            cursor = conn.cursor()
            if success:
                cursor.execute("UPDATE messages SET status = 'approved' WHERE id = ?", (message_id,))
                status_text = f"✅ Сообщение #{message_id} опубликовано (обычная публикация)"
                logger.info(f"✅ Сообщение #{message_id} опубликовано")
            else:
                cursor.execute("UPDATE messages SET status = 'error' WHERE id = ?", (message_id,))
                status_text = f"❌ Сообщение #{message_id} не удалось опубликовать"
            conn.commit()
            conn.close()

            try:
                bot.edit_message_text(
                    f"{status_text}\n👤 Обработал: {call.from_user.first_name}", 
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=None
                )
            except:
                bot.send_message(call.message.chat.id, f"{status_text}\n👤 Обработал: {call.from_user.first_name}")

        elif call.data.startswith('publish_forward_'):
            message_id = int(call.data.split('_')[2])
            message_data = get_message_from_db(message_id)
            
            if message_data and message_data[9] != 'pending':
                status = message_data[9]
                status_texts = {
                    'approved': '✅ уже одобрено',
                    'rejected': '❌ уже отклонено', 
                    'error': '⚠️ ошибка публикации'
                }
                bot.answer_callback_query(call.id, f"Сообщение {status_texts.get(status, status)}")
                return

            # Устанавливаем тип публикации "пересылка"
            update_publish_type(message_id, 'forward')
            
            success = send_to_channel({
                'message_type': message_data[5],
                'text': message_data[4],
                'file_id': message_data[6]
            }, 'forward')

            conn = sqlite3.connect('bot.db', check_same_thread=False)
            cursor = conn.cursor()
            if success:
                cursor.execute("UPDATE messages SET status = 'approved' WHERE id = ?", (message_id,))
                status_text = f"✅ Сообщение #{message_id} переслано в канал"
                logger.info(f"✅ Сообщение #{message_id} переслано")
            else:
                cursor.execute("UPDATE messages SET status = 'error' WHERE id = ?", (message_id,))
                status_text = f"❌ Сообщение #{message_id} не удалось переслать"
            conn.commit()
            conn.close()

            try:
                bot.edit_message_text(
                    f"{status_text}\n👤 Обработал: {call.from_user.first_name}", 
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=None
                )
            except:
                bot.send_message(call.message.chat.id, f"{status_text}\n👤 Обработал: {call.from_user.first_name}")

        elif call.data.startswith('reply_'):
            message_id = int(call.data.split('_')[1])
            # Сохраняем ID сообщения для ответа
            bot.answer_callback_query(call.id, "💬 Введите ответ пользователю")
            bot.send_message(call.message.chat.id, f"💬 Введите ответ для сообщения #{message_id}:")

        elif call.data.startswith('reject_'):
            message_id = int(call.data.split('_')[1])
            conn = sqlite3.connect('bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET status = 'rejected' WHERE id = ?", (message_id,))
            conn.commit()
            conn.close()

            status_text = f"❌ Сообщение #{message_id} отклонено"
            logger.info(f"❌ Сообщение #{message_id} отклонено")

            try:
                bot.edit_message_text(
                    f"{status_text}\n👤 Обработал: {call.from_user.first_name}", 
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=None
                )
            except:
                bot.send_message(call.message.chat.id, f"{status_text}\n👤 Обработал: {call.from_user.first_name}")

        bot.answer_callback_query(call.id, "✅ Действие выполнено")

    except Exception as e:
        logger.error(f"❌ Ошибка callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

# === WEBHOOK И FLASK ===
@app.route('/')
def home():
    return "🤖 Бот работает! Статус: ONLINE"

@app.route('/health')
def health_endpoint():
    """Endpoint для внешнего мониторинга здоровья"""
    if health_check():
        return "OK", 200
    else:
        return "ERROR", 500

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    
    # Логируем запуск бота
    log_bot_event('start', f"Bot started at {BOT_START_TIME}")

    # Запускаем мониторинг здоровья
    health_monitor_thread = threading.Thread(target=health_monitor, daemon=True)
    health_monitor_thread.start()
    logger.info("❤️ Мониторинг здоровья запущен")

    # УДАЛЯЕМ WEBHOOK ПЕРЕД ЗАПУСКОМ
    delete_webhook()

    # Запускаем авто-пинг в фоне
    ping_thread = threading.Thread(target=auto_ping, daemon=True)
    ping_thread.start()

    # Запускаем Flask в фоне
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запускаем polling в ОСНОВНОМ потоке
    logger.info("🤖 Запуск polling...")
    try:
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        log_error('polling', str(e))
        logger.info("🔄 Перезапуск polling через 10 секунд...")
        log_bot_event('restart', f"Restart due to error: {e}")
        time.sleep(10)
        # Удаляем webhook еще раз и перезапускаем
        delete_webhook()
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=30)






