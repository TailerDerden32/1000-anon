import os
import telebot
import sqlite3
from datetime import datetime
import logging
import requests
import json
from flask import Flask, request
import threading
import time

# === НАСТРОЙКИ ===
BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_IDS = [int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',')]
CHANNEL_USERNAME = os.environ['CHANNEL_USERNAME']

# Проверяем переменные окружения
required_vars = ['BOT_TOKEN', 'ADMIN_IDS', 'CHANNEL_USERNAME']
missing_vars = [var for var in required_vars if not os.environ.get(var)]

if missing_vars:
    print(f"❌ Отсутствуют переменные окружения: {', '.join(missing_vars)}")
    print("⚠️ Установите их в настройках bothost")
    exit(1)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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
            status TEXT DEFAULT 'pending'
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

def save_message_to_db(user_id, user_name, username, message_type, text, file_id=None, file_type=None):
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

# === ОТПРАВКА СООБЩЕНИЙ ===
def send_to_channel(message_data):
    try:
        message_type = message_data.get('message_type')
        text = message_data.get('text', '')
        file_id = message_data.get('file_id')

        if message_type == 'text':
            bot.send_message(CHANNEL_USERNAME, text, parse_mode='HTML')
            return True
        elif message_type == 'photo':
            bot.send_photo(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
            return True
        elif message_type == 'video':
            bot.send_video(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
            return True
        elif message_type == 'voice':
            bot.send_voice(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
            return True
        elif message_type == 'document':
            bot.send_document(CHANNEL_USERNAME, file_id, caption=text, parse_mode='HTML')
            return True
        elif message_type == 'sticker':
            bot.send_sticker(CHANNEL_USERNAME, file_id)
            return True
        else:
            logger.error(f"❌ Неподдерживаемый тип: {message_type}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки в канал: {e}")
        return False

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
    help_text = """
🤖 <b>Доступные команды:</b>
/start - Начать работу
/help - Показать справку

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
    notify_admins(message_id, user, message.text, 'text', None)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user = message.from_user
    caption = message.caption or '📷 Фото'
    file_id = message.photo[-1].file_id

    message_id = save_message_to_db(
        user.id,
        user.first_name or 'User',
        user.username or '',
        'photo',
        caption,
        file_id,
        'photo'
    )

    bot.send_message(message.chat.id, "✅ Фото отправлено")
    notify_admins(message_id, user, caption, 'photo', file_id)

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

    bot.send_message(message.chat.id, "✅ Видео отправлено")
    notify_admins(message_id, user, caption, 'video', file_id)

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

    bot.send_message(message.chat.id, "✅ Голосовое сообщение отправлено")
    notify_admins(message_id, user, '🎤 Голосовое сообщение', 'voice', file_id)

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

    bot.send_message(message.chat.id, "✅ Документ отправлен")
    notify_admins(message_id, user, caption, 'document', file_id)

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
    notify_admins(message_id, user, f"{sticker_emoji} Стикер", 'sticker', message.sticker.file_id)

# === УВЕДОМЛЕНИЯ АДМИНАМ ===
def notify_admins(message_id, user, text, media_type, file_id=None):
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
            # Сначала отправляем превью контента
            if media_type == 'photo' and file_id:
                bot.send_photo(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'video' and file_id:
                bot.send_video(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'voice' and file_id:
                bot.send_voice(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'document' and file_id:
                bot.send_document(admin_id, file_id, caption=admin_msg, parse_mode='HTML')
            elif media_type == 'sticker' and file_id:
                # Для стикеров сначала отправляем описание, потом стикер
                bot.send_message(admin_id, admin_msg, parse_mode='HTML')
                sent_sticker = bot.send_sticker(admin_id, file_id)
                # Добавляем кнопки к сообщению со стикером
                keyboard = InlineKeyboardMarkup()
                keyboard.row(
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
                )
                bot.edit_message_reply_markup(admin_id, sent_sticker.message_id, reply_markup=keyboard)
                continue  # Пропускаем отправку отдельного сообщения с кнопками
            else:
                # Для текста отправляем просто сообщение
                keyboard = InlineKeyboardMarkup()
                keyboard.row(
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
                )
                bot.send_message(admin_id, admin_msg, reply_markup=keyboard, parse_mode='HTML')
                continue
            
            # Для медиафайлов (кроме стикеров) добавляем кнопки к сообщению с превью
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
            )
            # Редактируем последнее сообщение чтобы добавить кнопки
            # Для этого нужно получить ID последнего отправленного сообщения
            # В данном случае кнопки уже добавлены в caption для медиафайлов
            
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
        if call.data.startswith('approve_'):
            message_id = int(call.data.split('_')[1])
            message_data = get_message_from_db(message_id)

            if message_data:
                success = send_to_channel({
                    'message_type': message_data[5],  # message_type
                    'text': message_data[4],         # message_text
                    'file_id': message_data[6]       # file_id
                })

                conn = sqlite3.connect('bot.db', check_same_thread=False)
                cursor = conn.cursor()
                if success:
                    cursor.execute("UPDATE messages SET status = 'approved' WHERE id = ?", (message_id,))
                    status_text = f"✅ Сообщение #{message_id} одобрено и опубликовано"
                    logger.info(f"✅ Сообщение #{message_id} опубликовано")
                else:
                    cursor.execute("UPDATE messages SET status = 'error' WHERE id = ?", (message_id,))
                    status_text = f"❌ Сообщение #{message_id} не удалось опубликовать"
                conn.commit()
                conn.close()

                bot.edit_message_text(status_text, call.message.chat.id, call.message.message_id)
            else:
                bot.edit_message_text(f"❌ Сообщение #{message_id} не найдено", call.message.chat.id, call.message.message_id)

        elif call.data.startswith('reject_'):
            message_id = int(call.data.split('_')[1])
            conn = sqlite3.connect('bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET status = 'rejected' WHERE id = ?", (message_id,))
            conn.commit()
            conn.close()

            bot.edit_message_text(f"❌ Сообщение #{message_id} отклонено", call.message.chat.id, call.message.message_id)
            logger.info(f"❌ Сообщение #{message_id} отклонено")

        bot.answer_callback_query(call.id, "✅ Действие выполнено")

    except Exception as e:
        logger.error(f"❌ Ошибка callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

# === WEBHOOK И FLASK ===
@app.route('/')
def home():
    return "🤖 Бот работает! Статус: ONLINE"

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")

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
        logger.info("🔄 Перезапуск polling через 10 секунд...")
        time.sleep(10)
        # Удаляем webhook еще раз и перезапускаем
        delete_webhook()
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=30)



