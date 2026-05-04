import telebot
import sqlite3
import threading
import time
import subprocess
import os
import sys
import asyncio
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests
from aiohttp import web

# ================= KONFIGURATSIYA =================
BOT_TOKEN = "8734482130:AAGg_kg2l2qct3wvgMYm-YaR86vCFcSdt4M"
ADMIN_ID = 8426582765
CHANNEL_USERNAME = "@vsf_lvl"
DEVELOPER_USERNAME = "@vsf911"

PRICES = {
    "video_downloader": 30000,
    "ai_image": 40000,
    "ai_video": 80000,
    "sms_bomber": 20000
}

# Child bot fayllari (hosted_bots/ papkasida)
BOT_CODES = {
    "video_downloader": "hosted_bots/video_downloader.py",
    "ai_image": "hosted_bots/ai_image.py",
    "ai_video": "hosted_bots/ai_video.py",
    "sms_bomber": "hosted_bots/sms_bomber.py"
}

# ================= DATABASE =================
os.makedirs("hosted_bots", exist_ok=True)

conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER DEFAULT 5000,
    referrer_id INTEGER DEFAULT 0,
    is_premium INTEGER DEFAULT 0,
    created_at TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS user_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    bot_type TEXT,
    bot_token TEXT,
    chat_id TEXT,
    bot_name TEXT,
    status TEXT DEFAULT 'stopped',
    users_count INTEGER DEFAULT 0,
    created_at TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS premium_users (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    added_by INTEGER,
    added_at TEXT
)
''')
conn.commit()

# ================= YORDAMCHI FUNKSIYALAR =================
def add_user(user_id, username, first_name, referrer_id=None):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        bonus = 0
        if referrer_id and referrer_id != user_id:
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (referrer_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET balance = balance + 5000 WHERE user_id = ?", (referrer_id,))
                conn.commit()
                bonus = 5000
                try:
                    bot.send_message(referrer_id, f"🎉 Sizning referralingiz {first_name} botni ishga tushirdi!\n💰 Hisobingizga +5000 so‘m qo‘shildi!")
                except: pass
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, balance, referrer_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username or "", first_name, 5000 if not referrer_id else 0, referrer_id or 0, datetime.now().isoformat()))
        conn.commit()
        return bonus
    return 0

def get_user_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row: return row[0]
    cursor.execute("SELECT balance FROM premium_users WHERE user_id = ?", (user_id,))
    r = cursor.fetchone()
    return r[0] if r else 0

def get_user_bots(user_id):
    cursor.execute("SELECT * FROM user_bots WHERE user_id = ?", (user_id,))
    return cursor.fetchall()

def add_user_bot(user_id, bot_type, bot_token, chat_id, bot_name):
    cursor.execute('''
        INSERT INTO user_bots (user_id, bot_type, bot_token, chat_id, bot_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, bot_type, bot_token, chat_id, bot_name, datetime.now().isoformat()))
    conn.commit()
    return cursor.lastrowid

def update_bot_status(bot_id, status):
    cursor.execute("UPDATE user_bots SET status = ? WHERE id = ?", (status, bot_id))
    conn.commit()

def delete_user_bot(bot_id, user_id):
    cursor.execute("DELETE FROM user_bots WHERE id = ? AND user_id = ?", (bot_id, user_id))
    conn.commit()

def is_premium(user_id):
    cursor.execute("SELECT * FROM premium_users WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def deduct_balance(user_id, amount):
    if is_premium(user_id):
        cursor.execute("UPDATE premium_users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    else:
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def host_bot(bot_type, bot_token, chat_id, user_id):
    code_path = BOT_CODES.get(bot_type)
    if not code_path or not os.path.exists(code_path):
        return False, "Bot kodi topilmadi!"
    with open(code_path, 'r', encoding='utf-8') as f:
        code = f.read()
    # Token va chat_id ni almashtirish
    code = code.replace("BOT_TOKEN", bot_token)
    code = code.replace("ADMIN_ID", str(chat_id))
    if bot_type == "video_downloader":
        code = code.replace("BOT_TOKEN", bot_token)
    elif bot_type == "ai_image":
        code = code.replace("$z1='zzzzzzz'", f"$z1='{bot_token}'")
    elif bot_type == "ai_video":
        code = code.replace("VETREX_TOKEN", bot_token)
    # Saqlash
    bot_filename = f"hosted_bots/bot_{user_id}_{bot_type}_{int(time.time())}.py"
    with open(bot_filename, 'w', encoding='utf-8') as f:
        f.write(code)
    # Ishga tushirish
    try:
        proc = subprocess.Popen([sys.executable, bot_filename], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        return True, bot_filename
    except Exception as e:
        return False, str(e)

# ================= TELEGRAM BOT =================
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# Menyu
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🤖 Botlarim"), KeyboardButton("🛒 Bot yaratish"))
    markup.add(KeyboardButton("👤 Shaxsiy kabinet"), KeyboardButton("📢 Kanalimiz"))
    markup.add(KeyboardButton("💬 Yordam"), KeyboardButton("💰 Hisob to'ldirish"))
    return markup

def bot_types_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"📥 Video yuklab oluvchi - {PRICES['video_downloader']:,} so‘m", callback_data="buy_video_downloader"))
    markup.add(InlineKeyboardButton(f"🎨 AI rasm yasash - {PRICES['ai_image']:,} so‘m", callback_data="buy_ai_image"))
    markup.add(InlineKeyboardButton(f"🎬 AI video yasash - {PRICES['ai_video']:,} so‘m", callback_data="buy_ai_video"))
    markup.add(InlineKeyboardButton(f"💣 SMS bomber - {PRICES['sms_bomber']:,} so‘m", callback_data="buy_sms_bomber"))
    return markup

@bot.message_handler(commands=['start'])
def start_command(message):
    uid = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    ref = None
    if len(message.text.split()) > 1:
        try: ref = int(message.text.split()[1])
        except: pass
    add_user(uid, username, first_name, ref)
    text = f"✨ <b>Assalomu alaykum, {first_name}! VSF Hosting Botga xush kelibsiz</b> ✨\n\n💰 Balans: {get_user_balance(uid):,} so‘m"
    bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "👤 Shaxsiy kabinet")
def profile(m):
    uid = m.from_user.id
    text = f"🆔 Chat ID: <code>{uid}</code>\n💰 Balans: {get_user_balance(uid):,} so‘m\n💎 Premium: {'✅ Ha' if is_premium(uid) else '❌ Yo‘q'}"
    bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "💰 Hisob to'ldirish")
def topup(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📩 Admin @vsf911", url="https://t.me/vsf911"))
    bot.send_message(m.chat.id, "💰 Admin bilan bog‘laning: @vsf911", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📢 Kanalimiz")
def channel(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 Obuna bo‘lish", url="https://t.me/vsf_lvl"))
    bot.send_message(m.chat.id, f"📢 Kanal: {CHANNEL_USERNAME}", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💬 Yordam")
def help_cmd(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👨‍💻 Admin @vsf911", url="https://t.me/vsf911"))
    bot.send_message(m.chat.id, "👨‍💻 Yordam: @vsf911", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🤖 Botlarim")
def my_bots(m):
    uid = m.from_user.id
    bots = get_user_bots(uid)
    if not bots:
        bot.send_message(uid, "Siz hali bot yaratmadingiz.", reply_markup=main_menu())
        return
    for b in bots:
        _, _, bot_type, token, chat_id, name, status, users_count, _ = b
        status_emoji = "🟢" if status == "running" else "🔴"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 To‘xtatish" if status=="running" else "▶️ Ishga tushirish", callback_data=f"toggle_{b[0]}"))
        markup.add(InlineKeyboardButton("🗑 O‘chirish", callback_data=f"delete_{b[0]}"))
        bot.send_message(uid, f"{status_emoji} <b>{name}</b>\n👥 {users_count} foydalanuvchi", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🛒 Bot yaratish")
def create_bot(m):
    bot.send_message(m.chat.id, "🤖 Bot turini tanlang:", reply_markup=bot_types_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    if call.data.startswith("buy_"):
        bot_type = call.data[4:]
        price = PRICES[bot_type]
        bal = get_user_balance(uid)
        if bal < price:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("💰 Hisob to‘ldirish", url="https://t.me/vsf911"))
            bot.send_message(uid, f"❌ Mablag‘ yetarli emas!\nKerak: {price:,} so‘m\nSizda: {bal:,} so‘m\nAdmin @vsf911", reply_markup=markup)
            return
        deduct_balance(uid, price)
        bot.send_message(uid, "✅ To‘lov amalga oshirildi!\n\n📌 <b>Bot yaratish uchun:</b>\n1. @BotFather ga o‘ting\n2. /newbot → nom va username bering\n3. Token oling\n4. Botingizga /start yozib, chat ID oling\n5. Quyidagi formatda yuboring:\n<code>TOKEN|CHAT_ID</code>\n\nMisol: <code>1234567890:ABCdefGHIjkl|123456789</code>", parse_mode="HTML")
        bot.register_next_step_handler(call.message, process_token, bot_type)
    elif call.data.startswith("toggle_"):
        bot_id = int(call.data[7:])
        cursor.execute("SELECT status FROM user_bots WHERE id=? AND user_id=?", (bot_id, uid))
        row = cursor.fetchone()
        if row:
            new_status = "stopped" if row[0]=="running" else "running"
            update_bot_status(bot_id, new_status)
            bot.answer_callback_query(call.id, f"✅ Bot {new_status}")
        my_bots(call.message)
    elif call.data.startswith("delete_"):
        bot_id = int(call.data[7:])
        delete_user_bot(bot_id, uid)
        bot.answer_callback_query(call.id, "✅ Bot o‘chirildi")
        my_bots(call.message)

def process_token(message, bot_type):
    uid = message.from_user.id
    text = message.text.strip()
    if "|" not in text:
        bot.send_message(uid, "❌ Noto‘g‘ri format! Token|ChatID")
        return
    token, chat_id = text.split("|", 1)
    token, chat_id = token.strip(), chat_id.strip()
    try:
        tb = telebot.TeleBot(token)
        tb.get_me()
    except:
        bot.send_message(uid, "❌ Token noto‘g‘ri!")
        return
    bot_name = f"{bot_type}_bot"
    success, result = host_bot(bot_type, token, chat_id, uid)
    if success:
        bid = add_user_bot(uid, bot_type, token, chat_id, bot_name)
        update_bot_status(bid, "running")
        bot.send_message(uid, f"✅ <b>Bot ishga tushdi!</b>\nNomi: {bot_name}\nToken: <code>{token[:20]}...</code>", parse_mode="HTML")
    else:
        bot.send_message(uid, f"❌ Xatolik: {result}")

# ================= ADMIN PANEL (faqat admin uchun) =================
@bot.message_handler(commands=['admin'])
def admin_panel(m):
    if m.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT user_id, balance FROM premium_users")
    prems = cursor.fetchall()
    text = "👨‍💻 Admin panel\n\nPremium foydalanuvchilar:\n"
    for pid, bal in prems:
        text += f"🆔 {pid} → {bal} so‘m\n"
    text += "\n➕ Premium qo‘shish: /add_premium ID BALANS"
    bot.send_message(ADMIN_ID, text)

@bot.message_handler(commands=['add_premium'])
def add_premium(m):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, uid, bal = m.text.split()
        uid, bal = int(uid), int(bal)
        cursor.execute("INSERT OR REPLACE INTO premium_users (user_id, balance, added_by, added_at) VALUES (?,?,?,?)",
                       (uid, bal, ADMIN_ID, datetime.now().isoformat()))
        conn.commit()
        bot.send_message(ADMIN_ID, f"✅ Premium qo‘shildi: {uid} | {bal} so‘m")
        bot.send_message(uid, f"🎉 Siz premium foydalanuvchi bo‘ldingiz! Balans: {bal} so‘m")
    except: bot.send_message(ADMIN_ID, "❌ Format: /add_premium 123456789 50000")

# ================= WEB SERVER (Render uchun) =================
async def health_check(request):
    return web.Response(text="Bot ishlayapti")

async def start_web():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    while True:
        await asyncio.sleep(3600)

def run_telegram():
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    threading.Thread(target=run_telegram, daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_web())