import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
from datetime import datetime

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ['ADMIN_ID'])
CHANNEL_USERNAME = os.environ['CHANNEL_USERNAME']
# =========================================

bot = telebot.TeleBot(BOT_TOKEN)

# База данных
def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            message_text TEXT,
            timestamp TEXT,
            status TEXT DEFAULT 'pending',
            channel_message_id INTEGER,
            admin_message_id INTEGER
        )
    ''')
    
    # Проверяем существование колонки admin_message_id и добавляем если нет
    cursor.execute("PRAGMA table_info(messages)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'admin_message_id' not in columns:
        cursor.execute('ALTER TABLE messages ADD COLUMN admin_message_id INTEGER')
        print("✅ Добавлена колонка admin_message_id в базу данных")
    
    conn.commit()
    conn.close()

init_db()

@bot.message_handler(commands=['start'])
def start(message):
    user = message.from_user
    print(f"👤 Новый пользователь: {user.first_name}")
    
    if user.id == ADMIN_ID:
        bot.send_message(
            message.chat.id, 
            "👑 Привет, администратор!\n"
            "Используй:\n"
            "/stats - статистика\n"
            "/pending - ожидающие сообщения\n"
            "/moderate - режим модерации"
        )
    else:
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {user.first_name}!\n\n"
            "📝 Отправляй сообщения - они будут анонимными."
        )

@bot.message_handler(commands=['stats'])
def stats(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
    pending_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'approved'")
    approved_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'rejected'")
    rejected_messages = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM messages")
    unique_users = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = (
        f"📊 Статистика бота:\n\n"
        f"📨 Всего сообщений: {total_messages}\n"
        f"👥 Уникальных пользователей: {unique_users}\n"
        f"⏳ Ожидают: {pending_messages}\n"
        f"✅ Одобрено: {approved_messages}\n"
        f"❌ Отклонено: {rejected_messages}\n"
        f"🕒 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(commands=['pending'])
def pending_messages(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_name, message_text, timestamp 
        FROM messages 
        WHERE status = 'pending' 
        ORDER BY timestamp DESC 
        LIMIT 10
    ''')
    messages = cursor.fetchall()
    conn.close()
    
    if not messages:
        bot.send_message(message.chat.id, "📭 Нет сообщений")
        return
    
    for msg_id, user_name, msg_text, timestamp in messages:
        time = datetime.fromisoformat(timestamp).strftime('%d.%m.%Y %H:%M')
        
        admin_message = (
            f"⏳ Сообщение\n\n"
            f"💬 Текст: {msg_text}\n\n"
            f"👤 Отправитель: {user_name}\n"
            f"📅 Время: {time}\n"
            f"🆔 ID: {msg_id}"
        )
        
        keyboard = InlineKeyboardMarkup()
        keyboard.row(
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{msg_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{msg_id}")
        )
        
        try:
            sent_msg = bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
            
            conn = sqlite3.connect('bot.db')
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE messages SET admin_message_id = ? WHERE id = ?",
                (sent_msg.message_id, msg_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Ошибка отправки сообщения админу: {e}")

@bot.message_handler(commands=['moderate'])
def moderate_mode(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_name, message_text, timestamp 
        FROM messages 
        WHERE status = 'pending' 
        ORDER BY timestamp ASC 
        LIMIT 1
    ''')
    message_data = cursor.fetchone()
    conn.close()
    
    if not message_data:
        bot.send_message(message.chat.id, "📭 Нет сообщений")
        return
    
    msg_id, user_name, msg_text, timestamp = message_data
    time = datetime.fromisoformat(timestamp).strftime('%d.%m.%Y %H:%M')
    
    admin_message = (
        f"⏳ Сообщение\n\n"
        f"💬 Текст: {msg_text}\n\n"
        f"👤 Отправитель: {user_name}\n"
        f"📅 Время: {time}\n"
        f"🆔 ID: {msg_id}"
    )
    
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{msg_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{msg_id}")
    )
    keyboard.add(InlineKeyboardButton("⏭️ Следующее", callback_data="next_pending"))
    
    try:
        sent_msg = bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
        
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE messages SET admin_message_id = ? WHERE id = ?",
            (sent_msg.message_id, msg_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка отправки сообщения админу: {e}")

@bot.message_handler(content_types=['text'])
def handle_message(message):
    user = message.from_user
    text = message.text.strip()
    
    if text.startswith('/'):
        return
    
    print(f"📨 Сообщение от {user.first_name}: {text}")
    
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, user_name, message_text, timestamp, status) VALUES (?, ?, ?, ?, 'pending')",
        (user.id, f"{user.first_name} (@{user.username})", text, datetime.now().isoformat())
    )
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, "✅")
    
    notify_admin_about_new_message(message_id, user, text)

def notify_admin_about_new_message(message_id, user, text):
    """Уведомляет админа о новом сообщении"""
    admin_message = (
        f"🆕 Новое сообщение\n\n"
        f"💬 Текст: {text}\n\n"
        f"👤 Отправитель:\n"
        f"   Имя: {user.first_name}\n"
        f"   Фамилия: {user.last_name or 'Не указана'}\n"
        f"   Username: @{user.username or 'Не указан'}\n"
        f"   ID: {user.id}\n\n"
        f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"🆔 ID: {message_id}"
    )
    
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{message_id}")
    )
    
    try:
        sent_msg = bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
        
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE messages SET admin_message_id = ? WHERE id = ?",
            (sent_msg.message_id, message_id)
        )
        conn.commit()
        conn.close()
        
        print(f"✅ Уведомление отправлено админу о сообщении #{message_id}")
    except Exception as e:
        print(f"❌ Ошибка отправки админу: {e}")

# Обработка callback-запросов (кнопок)
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    
    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ У вас нет прав")
        return
    
    data = call.data
    
    if data.startswith('approve_'):
        message_id = int(data.split('_')[1])
        approve_message(call, message_id)
        
    elif data.startswith('reject_'):
        message_id = int(data.split('_')[1])
        reject_message(call, message_id)
        
    elif data == 'next_pending':
        show_next_pending(call)
    
    bot.answer_callback_query(call.id)

def approve_message(call, message_id):
    """Одобряет сообщение и отправляет в канал"""
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT message_text, admin_message_id FROM messages WHERE id = ?', (message_id,))
    message_data = cursor.fetchone()
    
    if not message_data:
        return
    
    message_text, admin_message_id = message_data
    
    try:
        channel_message = (
            f"💬 Анонимное сообщение:\n\n"
            f"{message_text}\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        sent_message = bot.send_message(CHANNEL_USERNAME, channel_message)
        
        cursor.execute(
            "UPDATE messages SET status = 'approved', channel_message_id = ? WHERE id = ?",
            (sent_message.message_id, message_id)
        )
        conn.commit()
        
        if admin_message_id:
            try:
                cursor.execute('SELECT user_name, message_text, timestamp FROM messages WHERE id = ?', (message_id,))
                original_data = cursor.fetchone()
                if original_data:
                    user_name, msg_text, timestamp = original_data
                    time = datetime.fromisoformat(timestamp).strftime('%d.%m.%Y %H:%M')
                    
                    updated_message = (
                        f"✅ ОДОБРЕНО\n\n"
                        f"💬 Текст: {msg_text}\n\n"
                        f"👤 Отправитель: {user_name}\n"
                        f"📅 Время: {time}\n"
                        f"🆔 ID: {message_id}\n\n"
                        f"📢 Опубликовано: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    )
                    
                    bot.edit_message_text(
                        updated_message,
                        ADMIN_ID,
                        admin_message_id
                    )
            except Exception as e:
                print(f"⚠️ Не удалось обновить сообщение: {e}")
        
        print(f"✅ Сообщение #{message_id} одобрено и отправлено в канал")
        
    except Exception as e:
        print(f"❌ Ошибка отправки в канал: {e}")
    
    finally:
        conn.close()

def reject_message(call, message_id):
    """Отклоняет сообщение"""
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT admin_message_id FROM messages WHERE id = ?', (message_id,))
    result = cursor.fetchone()
    admin_message_id = result[0] if result else None
    
    cursor.execute(
        "UPDATE messages SET status = 'rejected' WHERE id = ?",
        (message_id,)
    )
    conn.commit()
    
    if admin_message_id:
        try:
            cursor.execute('SELECT user_name, message_text, timestamp FROM messages WHERE id = ?', (message_id,))
            original_data = cursor.fetchone()
            if original_data:
                user_name, msg_text, timestamp = original_data
                time = datetime.fromisoformat(timestamp).strftime('%d.%m.%Y %H:%M')
                
                updated_message = (
                    f"❌ ОТКЛОНЕНО\n\n"
                    f"💬 Текст: {msg_text}\n\n"
                    f"👤 Отправитель: {user_name}\n"
                    f"📅 Время: {time}\n"
                    f"🆔 ID: {message_id}\n\n"
                    f"⏰ Отклонено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
                
                bot.edit_message_text(
                    updated_message,
                    ADMIN_ID,
                    admin_message_id
                )
        except Exception as e:
            print(f"⚠️ Не удалось обновить сообщение: {e}")
    
    conn.close()
    print(f"❌ Сообщение #{message_id} отклонено")

def show_next_pending(call):
    """Показывает следующее сообщение"""
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_name, message_text, timestamp 
        FROM messages 
        WHERE status = 'pending' 
        ORDER BY timestamp ASC 
        LIMIT 1
    ''')
    message_data = cursor.fetchone()
    conn.close()
    
    if not message_data:
        bot.edit_message_text(
            "📭 Нет сообщений",
            call.message.chat.id,
            call.message.message_id
        )
        return
    
    msg_id, user_name, msg_text, timestamp = message_data
    time = datetime.fromisoformat(timestamp).strftime('%d.%m.%Y %H:%M')
    
    admin_message = (
        f"⏳ Сообщение\n\n"
        f"💬 Текст: {msg_text}\n\n"
        f"👤 Отправитель: {user_name}\n"
        f"📅 Время: {time}\n"
        f"🆔 ID: {msg_id}"
    )
    
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{msg_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{msg_id}")
    )
    keyboard.add(InlineKeyboardButton("⏭️ Следующее", callback_data="next_pending"))
    
    bot.edit_message_text(
        admin_message,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboard
    )

if __name__ == "__main__":
    print("🚀 Бот запущен на сервере!")
    bot.infinity_polling()