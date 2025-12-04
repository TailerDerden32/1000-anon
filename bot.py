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

# === ПАТИ ДЛЯ БАЗЫ ДАННЫХ ===
DATA_DIR = '/app/data'
DB_PATH = os.path.join(DATA_DIR, 'bot.db')

if not os.path.exists(DATA_DIR):
    DATA_DIR = '/app'
    DB_PATH = os.path.join(DATA_DIR, 'bot.db')
    print(f"⚠️ Volume не найден, используем рабочую директорию: {DATA_DIR}")

os.makedirs(DATA_DIR, exist_ok=True)
print(f"📁 База данных будет сохранена в: {DB_PATH}")

# === ЗАГРУЗКА КОНФИГУРАЦИИ ===
def load_config():
    try:
        config = {
            'BOT_TOKEN': os.environ.get('BOT_TOKEN'),
            'ADMIN_IDS': [int(x.strip()) for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()],
            'CHANNEL_USERNAME': os.environ.get('CHANNEL_USERNAME')
        }
        print("✅ Конфигурация загружена из переменных окружения")
        return config
    except Exception as e:
        print(f"❌ Ошибка загрузки конфигурации: {e}")
        return None

config = load_config()
if not config:
    print("❌ Не удалось загрузить конфигурацию")
    exit(1)

# === НАСТРОЙКИ ===
BOT_TOKEN = config.get('BOT_TOKEN')
ADMIN_IDS = config.get('ADMIN_IDS', [])
CHANNEL_USERNAME = config.get('CHANNEL_USERNAME')

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не найден")
    exit(1)
if not ADMIN_IDS:
    print("❌ ADMIN_IDS не найден")
    exit(1)
if not CHANNEL_USERNAME:
    print("❌ CHANNEL_USERNAME не найден")
    exit(1)

print(f"✅ BOT_TOKEN: {BOT_TOKEN[:10]}...")
print(f"✅ ADMIN_IDS: {ADMIN_IDS}")
print(f"✅ CHANNEL_USERNAME: {CHANNEL_USERNAME}")

HEALTH_CHECK_INTERVAL = 300
MAX_ERROR_COUNT = 3
RESTART_DELAY = 60

BOT_START_TIME = datetime.now()
MESSAGE_COUNT = 0
LAST_RESTART_TIME = datetime.now()
ERROR_COUNT = 0
LAST_ERROR_TIME = None
HEALTH_MONITOR_RUNNING = False
media_groups = {}

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(DATA_DIR, 'bot_health.log'))
    ]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

try:
    bot_info = bot.get_me()
    logger.info(f"✅ Бот запущен: {bot_info.first_name} (@{bot_info.username})")
except Exception as e:
    logger.error(f"❌ Ошибка доступа к боту: {e}")
    logger.error("⚠️ Проверьте правильность BOT_TOKEN")
    exit(1)

try:
    chat = bot.get_chat(CHANNEL_USERNAME)
    logger.info(f"✅ Канал найден: {chat.title}")
except Exception as e:
    logger.error(f"❌ Ошибка доступа к каналу {CHANNEL_USERNAME}: {e}")
    logger.error("⚠️ Проверьте: 1) Юзернейм канала 2) Бот добавлен как администратор")

# === СИСТЕМА МОНИТОРИНГА ЗДОРОВЬЯ ===
def log_error(error_type, error_message):
    global ERROR_COUNT, LAST_ERROR_TIME
    ERROR_COUNT += 1
    LAST_ERROR_TIME = datetime.now()
    logger.error(f"🚨 Ошибка [{error_type}]: {error_message}")
    logger.error(f"📊 Счетчик ошибок: {ERROR_COUNT}/{MAX_ERROR_COUNT}")
    log_bot_event('error', f"{error_type}: {error_message}")

def reset_error_count():
    global ERROR_COUNT
    ERROR_COUNT = 0
    logger.info("🔄 Счетчик ошибок сброшен")

def health_check():
    try:
        bot.get_me()
        bot.get_chat(CHANNEL_USERNAME)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        logger.info("❤️ Проверка здоровья: все системы работают нормально")
        reset_error_count()
        return True
    except Exception as e:
        log_error('health_check', str(e))
        return False

def health_monitor():
    global HEALTH_MONITOR_RUNNING
    HEALTH_MONITOR_RUNNING = True
    while HEALTH_MONITOR_RUNNING:
        try:
            if not health_check():
                logger.error("🔄 Проблемы с здоровьем бота")
        except Exception as e:
            logger.error(f"❌ Ошибка в мониторе здоровья: {e}")
        time.sleep(HEALTH_CHECK_INTERVAL)

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            event_time TEXT,
            details TEXT
        )
    ''')

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
    logger.info(f"✅ База данных инициализирована: {DB_PATH}")

init_db()

def save_message_to_db(user_id, user_name, username, message_type, text, file_id=None, file_type=None):
    global MESSAGE_COUNT
    MESSAGE_COUNT += 1
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
    message = cursor.fetchone()
    conn.close()
    
    if message and message[5] == 'photo' and message[6]:
        try:
            file_ids = json.loads(message[6])
            if isinstance(file_ids, list) and len(file_ids) > 0:
                message_list = list(message)
                message_list[6] = file_ids[0]
                message_list.append(file_ids)
                return tuple(message_list)
        except (json.JSONDecodeError, TypeError):
            pass
    
    return message

def update_publish_type(message_id, publish_type):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE messages SET publish_type = ? WHERE id = ?", (publish_type, message_id))
    conn.commit()
    conn.close()

def update_admin_reply(message_id, reply_text, reply_sent=False):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE messages SET admin_reply = ?, reply_sent = ? WHERE id = ?", (reply_text, reply_sent, message_id))
    conn.commit()
    conn.close()

def log_bot_event(event_type, details=""):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bot_stats (event_type, event_time, details) VALUES (?, ?, ?)",
        (event_type, datetime.now().isoformat(), details)
    )
    conn.commit()
    conn.close()

# === ОТПРАВКА СООБЩЕНИЙ (ИСПРАВЛЕНА) ===
def send_to_channel(message_data, publish_type='normal', admin_id=None):
    try:
        message_type = message_data.get('message_type')
        text = message_data.get('text', '')
        file_id = message_data.get('file_id')
        file_ids = message_data.get('file_ids', [])
        
        if not file_ids and file_id:
            file_ids = [file_id]

        if publish_type == 'forward' and admin_id:
            target_chat = admin_id
            forward_text = "🔄 <b>Перешлите это сообщение в канал:</b>"
        else:
            target_chat = CHANNEL_USERNAME
            forward_text = ""

        if forward_text:
            bot.send_message(target_chat, forward_text, parse_mode='HTML')

        if message_type == 'text':
            bot.send_message(target_chat, text, parse_mode='HTML')
            return True
            
        elif message_type == 'photo':
            if len(file_ids) > 1:
                media = []
                for i, photo_id in enumerate(file_ids):
                    media.append(telebot.types.InputMediaPhoto(
                        photo_id, 
                        caption=text if i == 0 else None,
                        parse_mode='HTML'
                    ))
                bot.send_media_group(target_chat, media)
            else:
                bot.send_photo(target_chat, file_ids[0], caption=text, parse_mode='HTML')
            return True
            
        elif message_type == 'video':
            bot.send_video(target_chat, file_ids[0], caption=text, parse_mode='HTML')
            return True
            
        elif message_type == 'voice':
            bot.send_voice(target_chat, file_ids[0], caption=text, parse_mode='HTML')
            return True
            
        elif message_type == 'document':
            bot.send_document(target_chat, file_ids[0], caption=text, parse_mode='HTML')
            return True
            
        elif message_type == 'sticker':
            bot.send_sticker(target_chat, file_ids[0])
            return True
            
        else:
            logger.error(f"❌ Неподдерживаемый тип: {message_type}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки в канал: {e}")
        log_error('send_to_channel', str(e))
        return False

# === ОБРАБОТКА ГРУПП МЕДИА (ИСПРАВЛЕНА) ===
def process_media_group(media_group_id):
    if media_group_id not in media_groups:
        return
        
    group_data = media_groups[media_group_id]
    user = group_data['user']
    caption = group_data['caption']
    file_ids = group_data['file_ids']
    
    if not file_ids:
        return
    
    file_ids_json = json.dumps(file_ids)
    
    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'photo',
        caption,
        file_ids_json,
        'photo'
    )
    
    bot.send_message(user.id, f"✅ {len(file_ids)} фото отправлено на модерацию")
    notify_admins_group(message_id, user, caption, 'photo', file_ids)
    
    del media_groups[media_group_id]

# === УВЕДОМЛЕНИЯ АДМИНАМ ДЛЯ ГРУПП ===
def notify_admins_group(message_id, user, text, media_type, file_ids):
    icons = {'text': '📝', 'photo': '📷', 'video': '🎥', 'voice': '🎤', 'document': '📄', 'sticker': '🎭'}
    icon = icons.get(media_type, '📨')
    username_display = f"@{user.username}" if user.username else "нет юзернейма"

    admin_msg = f"""{icon} <b>Новое сообщение</b> #{message_id}

👤 <b>От:</b> {user.first_name} ({username_display})
🆔 <b>ID:</b> {user.id}
📋 <b>Тип:</b> {media_type} ({len(file_ids)} шт.)
📝 <b>Текст:</b> {text if text else 'Нет текста'}"""

    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    for admin_id in ADMIN_IDS:
        try:
            if len(file_ids) > 1:
                media = []
                for i, file_id in enumerate(file_ids):
                    media.append(telebot.types.InputMediaPhoto(
                        file_id, 
                        caption=admin_msg if i == 0 else None,
                        parse_mode='HTML'
                    ))
                sent_messages = bot.send_media_group(admin_id, media)
                
                keyboard = InlineKeyboardMarkup()
                keyboard.row(
                    InlineKeyboardButton("📝 Обычная публикация", callback_data=f"publish_normal_{message_id}"),
                    InlineKeyboardButton("🔄 Переслать", callback_data=f"publish_forward_{message_id}")
                )
                keyboard.row(
                    InlineKeyboardButton("💬 Ответить пользователю", callback_data=f"reply_{message_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
                )
                
                bot.send_message(admin_id, "📋 Выберите действие для группы медиа:", reply_markup=keyboard)
                
            else:
                msg = bot.send_photo(admin_id, file_ids[0], caption=admin_msg, parse_mode='HTML')
                
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

# === СТАТИСТИКА ===
def get_bot_uptime():
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    else:
        return f"{hours}ч {minutes}м {seconds}с"

def get_bot_stats():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'approved'")
    approved_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
    pending_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM messages")
    unique_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bot_stats WHERE event_type = 'restart'")
    restarts_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bot_errors")
    total_errors = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'uptime': get_bot_uptime(),
        'total_messages': total_messages,
        'approved_messages': approved_messages,
        'pending_messages': pending_messages,
        'unique_users': unique_users,
        'restarts_count': restarts_count,
        'total_errors': total_errors,
        'current_error_count': ERROR_COUNT,
        'current_message_count': MESSAGE_COUNT
    }

# === ОБРАБОТКА ОТВЕТОВ АДМИНОВ ===
user_reply_mode = {}

@bot.message_handler(func=lambda message: message.from_user.id in ADMIN_IDS and message.text and not message.text.startswith('/'))
def handle_admin_reply(message):
    admin_id = message.from_user.id
    
    if admin_id in user_reply_mode:
        target_message_id = user_reply_mode[admin_id]
        
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, user_name, message_text FROM messages WHERE id = ?", (target_message_id,))
            message_data = cursor.fetchone()
            
            if message_data:
                user_id, user_name, original_text = message_data
                
                try:
                    reply_text = f"💬 <b>Ответ от администратора:</b>\n\n{message.text}"
                    bot.send_message(user_id, reply_text, parse_mode='HTML')
                    
                    update_admin_reply(target_message_id, message.text, True)
                    
                    bot.send_message(admin_id, f"✅ Ответ отправлен пользователю {user_name}")
                    logger.info(f"💬 Ответ админа {admin_id} отправлен пользователю {user_id}")
                    
                except Exception as e:
                    error_msg = f"❌ Не удалось отправить ответ пользователю: {e}"
                    bot.send_message(admin_id, error_msg)
                    logger.error(f"❌ Ошибка отправки ответа пользователю: {e}")
            
            else:
                bot.send_message(admin_id, "❌ Сообщение не найдено в базе данных")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки ответа админа: {e}")
            bot.send_message(admin_id, "❌ Ошибка при обработке ответа")
        
        del user_reply_mode[admin_id]
        
    else:
        handle_text(message)

# === ОСНОВНЫЕ КОМАНДЫ ===
@bot.message_handler(commands=['start'])
def start(message):
    user = message.from_user
    logger.info(f"👤 /start от {user.first_name} (ID: {user.id})")
    bot.send_message(message.chat.id, 
                    "👋 <b>Привет!</b>\n\n"
                    "Отправь мне сообщение или медиафайл для публикации в канале.\n"
                    "Всё будет отправлено на модерацию.", 
                    parse_mode='HTML')

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
🤖 <b>Доступные команды:</b>
/start - Начать работу
/help - Показать справку
/stats - Статистика бота (админы)
/pending - Сообщения на модерации (админы)

📨 <b>Что можно отправить:</b>
• Текстовые сообщения
• Фотографии (с подписью или без)
• Видео (с подписью или без) 
• Голосовые сообщения
• Документы
• Стикеры
• Опросы (просто отправьте текст опроса)
"""
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "❌ Нет прав для просмотра статистики")
        return

    try:
        stats = get_bot_stats()
        
        stats_text = f"""📊 <b>Статистика бота</b>

⏱ Время работы: <b>{stats['uptime']}</b>
📨 Всего сообщений: <b>{stats['total_messages']}</b>
👥 Уникальных пользователей: <b>{stats['unique_users']}</b>
✅ Одобрено: <b>{stats['approved_messages']}</b>
⏳ Ожидают модерации: <b>{stats['pending_messages']}</b>
🔄 Перезапусков: <b>{stats['restarts_count']}</b>
🚨 Ошибок: <b>{stats['total_errors']}</b>"""

        bot.send_message(message.chat.id, stats_text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении статистики")

@bot.message_handler(commands=['pending'])
def pending_messages(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, user_id, user_name, username, message_text, message_type, file_id, file_type, timestamp, status FROM messages WHERE status = 'pending' ORDER BY id DESC LIMIT 10")
        pending_messages = cursor.fetchall()
        
        if not pending_messages:
            bot.send_message(message.chat.id, "📭 Нет сообщений, ожидающих модерации")
            return
        
        bot.send_message(message.chat.id, "📋 <b>Сообщения ожидающие модерации:</b>", parse_mode='HTML')
        
        for msg in pending_messages:
            msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status = msg
            
            message_text = f"📨 <b>#{msg_id}</b> - {user_name} - {msg_type}\n"
            if text and len(text) > 100:
                message_text += f"📝 {text[:100]}..."
            elif text:
                message_text += f"📝 {text}"
            else:
                message_text += "📝 Нет текста"
            
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            quick_keyboard = InlineKeyboardMarkup()
            quick_keyboard.row(
                InlineKeyboardButton("👁 Просмотреть", callback_data=f"view_{msg_id}"),
                InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{msg_id}")
            )
            
            bot.send_message(
                message.chat.id, 
                message_text,
                parse_mode='HTML',
                reply_markup=quick_keyboard
            )
        
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
    
    if user.id in ADMIN_IDS and user.id in user_reply_mode:
        return
    
    logger.info(f"📝 Текст от {user.first_name} (ID: {user.id})")

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'text',
        message.text
    )

    bot.send_message(message.chat.id, "✅ Сообщение отправлено на модерацию")
    notify_admins(message_id, user, message.text, 'text', None, message.message_id)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user = message.from_user
    caption = message.caption or '📷 Фото'
    
    file_id = message.photo[-1].file_id
    
    if message.media_group_id:
        media_group_id = message.media_group_id
        
        if media_group_id not in media_groups:
            media_groups[media_group_id] = {
                'user': user,
                'caption': caption,
                'file_ids': [],
                'timestamp': datetime.now()
            }
            threading.Timer(1.0, process_media_group, [media_group_id]).start()
        
        media_groups[media_group_id]['file_ids'].append(file_id)
        
    else:
        message_id = save_message_to_db(
            user.id,
            user.first_name or 'User',
            user.username or '',
            'photo',
            caption,
            file_id,
            'photo'
        )

        bot.send_message(message.chat.id, "✅ Фото отправлено на модерацию")
        notify_admins(message_id, user, caption, 'photo', file_id, message.message_id)

@bot.message_handler(content_types=['video'])
def handle_video(message):
    user = message.from_user
    caption = message.caption or '🎥 Видео'
    file_id = message.video.file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'video',
        caption,
        file_id,
        'video'
    )

    bot.send_message(message.chat.id, "✅ Видео отправлено на модерацию")
    notify_admins(message_id, user, caption, 'video', file_id, message.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user = message.from_user
    file_id = message.voice.file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'voice',
        '🎤 Голосовое сообщение',
        file_id,
        'voice'
    )

    bot.send_message(message.chat.id, "✅ Голосовое сообщение отправлено на модерацию")
    notify_admins(message_id, user, '🎤 Голосовое сообщение', 'voice', file_id, message.message_id)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user = message.from_user
    caption = message.caption or '📄 Документ'
    file_id = message.document.file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'document',
        caption,
        file_id,
        'document'
    )

    bot.send_message(message.chat.id, "✅ Документ отправлен на модерацию")
    notify_admins(message_id, user, caption, 'document', file_id, message.message_id)

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    user = message.from_user
    logger.info(f"🎭 Стикер от {user.first_name} (ID: {user.id})")
    
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

    bot.send_message(message.chat.id, "✅ Стикер отправлен на модерацию")
    notify_admins(message_id, user, f"{sticker_emoji} Стикер", 'sticker', message.sticker.file_id, message.message_id)

# === УВЕДОМЛЕНИЯ АДМИНАМ ===
def notify_admins(message_id, user, text, media_type, file_id=None, original_message_id=None):
    icons = {'text': '📝', 'photo': '📷', 'video': '🎥', 'voice': '🎤', 'document': '📄', 'sticker': '🎭'}
    icon = icons.get(media_type, '📨')
    username_display = f"@{user.username}" if user.username else "нет юзернейма"

    admin_msg = f"""{icon} <b>Новое сообщение</b> #{message_id}

👤 <b>От:</b> {user.first_name} ({username_display})
🆔 <b>ID:</b> {user.id}
📋 <b>Тип:</b> {media_type}
📝 <b>Текст:</b> {text if text else 'Нет текста'}"""

    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    for admin_id in ADMIN_IDS:
        try:
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
            
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("📝 Опубликовать", callback_data=f"publish_normal_{message_id}"),
                InlineKeyboardButton("🔄 Переслать", callback_data=f"publish_forward_{message_id}")
            )
            keyboard.row(
                InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{message_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
            )
            
            if media_type != 'sticker':
                bot.edit_message_reply_markup(admin_id, msg.message_id, reply_markup=keyboard)
            else:
                bot.send_message(admin_id, "Выберите действие:", reply_markup=keyboard)
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки админу {admin_id}: {e}")

# === ОБРАБОТКА CALLBACK (ИСПРАВЛЕНА) ===
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    logger.info(f"🔄 Callback: {call.data} от {call.from_user.id}")

    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ Нет прав для модерации")
        return

    try:
        if call.data.startswith('view_'):
            message_id = int(call.data.split('_')[1])
            message_data = get_message_from_db(message_id)

            if not message_data:
                bot.answer_callback_query(call.id, "❌ Сообщение не найдено")
                return

            if len(message_data) > 13:
                msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status, admin_reply, reply_sent, publish_type, all_file_ids = message_data
            else:
                msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status, admin_reply, reply_sent, publish_type = message_data
                all_file_ids = [file_id]

            username_display = f"@{username}" if username else "нет юзернейма"
            
            file_count = len(all_file_ids) if isinstance(all_file_ids, list) else 1
            detail_text = f"""📋 <b>Детали сообщения #{msg_id}</b>

👤 <b>Пользователь:</b> {user_name} ({username_display})
🆔 <b>ID пользователя:</b> {user_id}
📋 <b>Тип:</b> {msg_type} ({file_count} шт.)
📝 <b>Текст:</b> {text if text else 'Нет текста'}
⏰ <b>Время:</b> {timestamp[:16]}
📊 <b>Статус:</b> {status}"""

            if admin_reply:
                detail_text += f"\n💬 <b>Ответ админа:</b> {admin_reply}"

            bot.send_message(call.message.chat.id, detail_text, parse_mode='HTML')

            if msg_type == 'photo':
                if isinstance(all_file_ids, list) and len(all_file_ids) > 1:
                    media = []
                    for i, photo_id in enumerate(all_file_ids):
                        media.append(telebot.types.InputMediaPhoto(
                            photo_id,
                            caption=f"📷 Фото {i+1} из {len(all_file_ids)} из сообщения #{msg_id}" if i == 0 else None
                        ))
                    bot.send_media_group(call.message.chat.id, media)
                elif file_id:
                    bot.send_photo(call.message.chat.id, file_id, caption=f"📷 Фото из сообщения #{msg_id}")
            
            elif msg_type == 'video' and file_id:
                bot.send_video(call.message.chat.id, file_id, caption=f"🎥 Видео из сообщения #{msg_id}")
            elif msg_type == 'document' and file_id:
                bot.send_document(call.message.chat.id, file_id, caption=f"📄 Документ из сообщения #{msg_id}")
            elif msg_type == 'voice' and file_id:
                bot.send_voice(call.message.chat.id, file_id, caption=f"🎤 Голосовое из сообщения #{msg_id}")

            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{msg_id}"),
                InlineKeyboardButton("📝 Опубликовать", callback_data=f"publish_normal_{msg_id}")
            )
            keyboard.row(
                InlineKeyboardButton("🔄 Переслать", callback_data=f"publish_forward_{msg_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{msg_id}")
            )

            bot.send_message(call.message.chat.id, "Выберите действие:", reply_markup=keyboard)
            bot.answer_callback_query(call.id, "✅ Детали сообщения")

        elif call.data.startswith('publish_normal_') or call.data.startswith('publish_forward_'):
            message_id = int(call.data.split('_')[2])
            action = call.data.split('_')[1]
            message_data = get_message_from_db(message_id)
            
            if not message_data:
                bot.answer_callback_query(call.id, "❌ Сообщение не найдено")
                return
                
            if len(message_data) > 13:
                msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status, admin_reply, reply_sent, publish_type, all_file_ids = message_data
            else:
                msg_id, user_id, user_name, username, text, msg_type, file_id, file_type, timestamp, status, admin_reply, reply_sent, publish_type = message_data
                all_file_ids = [file_id]
                
            if status != 'pending':
                status_texts = {
                    'approved': '✅ уже одобрено',
                    'rejected': '❌ уже отклонено', 
                    'error': '⚠️ ошибка публикации'
                }
                bot.answer_callback_query(call.id, f"Сообщение {status_texts.get(status, status)}")
                return

            update_publish_type(message_id, action)
            
            message_data_for_send = {
                'message_type': msg_type,
                'text': text,
                'file_id': file_id,
                'file_ids': all_file_ids if isinstance(all_file_ids, list) else [file_id]
            }
            
            if action == 'forward':
                success = send_to_channel(message_data_for_send, 'forward', call.from_user.id)
            else:
                success = send_to_channel(message_data_for_send, 'normal')

            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            if success:
                cursor.execute("UPDATE messages SET status = 'approved' WHERE id = ?", (message_id,))
                action_text = "опубликовано" if action == 'normal' else "отправлено для пересылки"
                status_text = f"✅ Сообщение #{message_id} {action_text}"
                logger.info(f"✅ Сообщение #{message_id} {action_text} ({len(all_file_ids) if isinstance(all_file_ids, list) else 1} файлов)")
            else:
                cursor.execute("UPDATE messages SET status = 'error' WHERE id = ?", (message_id,))
                status_text = f"❌ Сообщение #{message_id} не удалось отправить"
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
            message_data = get_message_from_db(message_id)
            
            if not message_data:
                bot.answer_callback_query(call.id, "❌ Сообщение не найдено")
                return
            
            user_reply_mode[call.from_user.id] = message_id
            
            user_name = message_data[2]
            message_text = message_data[4]
            
            context_text = f"💬 <b>Ответ на сообщение #{message_id}</b>\n\n"
            context_text += f"👤 <b>Пользователь:</b> {user_name}\n"
            context_text += f"📝 <b>Сообщение:</b> {message_text[:100]}{'...' if len(message_text) > 100 else ''}\n\n"
            context_text += "✍️ <b>Введите ваш ответ:</b>"
            
            bot.send_message(call.message.chat.id, context_text, parse_mode='HTML')
            bot.answer_callback_query(call.id, "💬 Введите ответ пользователю")

        elif call.data.startswith('reject_'):
            message_id = int(call.data.split('_')[1])
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    if health_check():
        return "OK", 200
    else:
        return "ERROR", 500

def delete_webhook():
    try:
        logger.info("🔄 Удаление webhook...")
        bot.remove_webhook()
        time.sleep(1)
        logger.info("✅ Webhook удален")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления webhook: {e}")
        return False

def auto_ping():
    time.sleep(15)
    logger.info("🔄 Запуск авто-пинга...")

    while True:
        try:
            logger.info("✅ Бот активен")
        except Exception as e:
            logger.error(f"❌ Ошибка авто-пинга: {e}")
        time.sleep(300)

def run_flask():
    time.sleep(5)
    
    ports = [8080, 8081, 8082, 8083, 8084]
    
    for port in ports:
        try:
            logger.info(f"🌐 Попытка запуска Flask сервера на порту {port}...")
            app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
            break
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning(f"⚠️ Порт {port} занят, пробуем следующий...")
                continue
            else:
                logger.error(f"❌ Ошибка запуска Flask: {e}")
                break
    else:
        logger.error("❌ Не удалось запустить Flask сервер: все порты заняты")

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    logger.info(f"📁 База данных: {DB_PATH}")
    
    log_bot_event('start', f"Bot started at {BOT_START_TIME}")

    health_monitor_thread = threading.Thread(target=health_monitor, daemon=True)
    health_monitor_thread.start()
    logger.info("❤️ Мониторинг здоровья запущен")

    delete_webhook()

    ping_thread = threading.Thread(target=auto_ping, daemon=True)
    ping_thread.start()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("🤖 Запуск polling...")
    try:
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        log_error('polling', str(e))
        logger.info("🔄 Перезапуск polling через 10 секунд...")
        log_bot_event('restart', f"Restart due to error: {e}")
        time.sleep(10)
        delete_webhook()
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=30)
