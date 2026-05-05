import telebot
import sqlite3
import threading
import time
import subprocess
import os
import sys
import asyncio
import re
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests
from aiohttp import web

# ================= KONFIGURATSIYA =================
BOT_TOKEN = "8734482130:AAGg_kg2l2qct3wvgMYm-YaR86vCFcSdt4M"
ADMIN_ID = 8426582765
CHANNEL_USERNAME = "@vsf_lvl"
DEVELOPER_USERNAME = "@vsf911"
REFERRAL_BONUS = 5000

PRICES = {
    "video_downloader": 30000,
    "ai_image": 40000,
    "ai_video": 80000,
    "sms_bomber": 20000
}

BOT_CODES = {
    "video_downloader": "hosted_bots/video_downloader.py",
    "ai_image": "hosted_bots/ai_image.py",
    "ai_video": "hosted_bots/ai_video.py",
    "sms_bomber": "hosted_bots/sms_bomber.py"
}

# ================= XAVFSIZ XOTIRA =================
user_states = {}

# ================= DATABASE =================
os.makedirs("hosted_bots", exist_ok=True)

conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER DEFAULT 0,
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
conn.commit()

# ================= YORDAMCHI FUNKSIYALAR =================
def add_user(user_id, username, first_name, referrer_id=None):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        if referrer_id and referrer_id != user_id:
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (referrer_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (REFERRAL_BONUS, referrer_id))
                conn.commit()
                try:
                    bot.send_message(referrer_id, f"🎉 <b>Tabriklaymiz!</b>\nSizning taklif havolangiz orqali do‘stingiz ro‘yxatdan o‘tdi.\n💰 Hisobingizga <b>+{REFERRAL_BONUS:,} so‘m</b> qo‘shildi!", parse_mode="HTML")
                except: pass
        
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, balance, referrer_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username or "", first_name, 0, referrer_id or 0, datetime.now().isoformat()))
        conn.commit()
        return True
    return False

def get_user_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_balance(user_id, amount):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    else:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, balance, referrer_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, "", "Foydalanuvchi", amount, 0, datetime.now().isoformat()))
    conn.commit()

def deduct_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def is_premium(user_id):
    cursor.execute("SELECT is_premium FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] == 1 if row else False

def set_premium(user_id, status=1):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (status, user_id))
    else:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, balance, referrer_id, is_premium, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, "", "Foydalanuvchi", 0, 0, status, datetime.now().isoformat()))
    conn.commit()

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

# ================= AQLLI HOSTING MEXANIZMI =================
def host_bot(bot_type, bot_token, chat_id, user_id):
    code_path = BOT_CODES.get(bot_type)
    if not code_path or not os.path.exists(code_path):
        return False, f"Bot shabloni topilmadi! ({code_path})"
        
    with open(code_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    code = re.sub(r'^BOT_TOKEN\s*=\s*["\'].*?["\']', f'BOT_TOKEN = "{bot_token}"', code, flags=re.MULTILINE)
    code = re.sub(r'^TOKEN\s*=\s*["\'].*?["\']', f'TOKEN = "{bot_token}"', code, flags=re.MULTILINE)
    code = re.sub(r'^ADMIN_ID_STR\s*=\s*["\'].*?["\']', f'ADMIN_ID_STR = "{chat_id}"', code, flags=re.MULTILINE)
    
    if "aiogram" in code and "start_polling" in code:
        code = code.replace("await dp.start_polling(bot)", "await bot.delete_webhook(drop_pending_updates=True)\n    await dp.start_polling(bot)")
    if "telebot" in code and "infinity_polling" in code:
        code = code.replace("bot.infinity_polling(", "try: bot.remove_webhook()\nexcept: pass\nbot.infinity_polling(")
    
    bot_filename = f"hosted_bots/bot_{user_id}_{bot_type}_{int(time.time())}.py"
    with open(bot_filename, 'w', encoding='utf-8') as f:
        f.write(code)
        
    log_filename = f"hosted_bots/log_{user_id}_{bot_type}.txt"
    log_file = open(log_filename, "w")
    
    try:
        proc = subprocess.Popen([sys.executable, bot_filename], stdout=log_file, stderr=log_file, start_new_session=True)
        time.sleep(2)
        if proc.poll() is not None:  
            with open(log_filename, "r") as lf:
                error_text = lf.read()
            return False, f"Python Xatosi (Bot quladi):\n{error_text[-500:]}"
            
        return True, bot_filename
    except Exception as e:
        return False, str(e)

# ================= TELEGRAM BOT =================
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🤖 Bot yaratish"), KeyboardButton("🤖 Botlarim"))
    markup.add(KeyboardButton("🗣 Referal"), KeyboardButton("👤 Shaxsiy kabinet"))
    markup.add(KeyboardButton("🚀 Saytga kirish"), KeyboardButton("💳 Hisob to'ldirish"))
    markup.add(KeyboardButton("💬 Murojaat"), KeyboardButton("📚 Qo'llanma"))
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
    user_states.pop(uid, None) 
    text = f"✨ <b>Assalomu alaykum, {first_name}! VSF Hosting Botga xush kelibsiz</b> ✨\n\nBu yerda o'z shaxsiy premium botlaringizni yaratishingiz mumkin."
    bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "👤 Shaxsiy kabinet")
def profile(m):
    uid = m.from_user.id
    text = f"📋 <b>Shaxsiy Kabinet</b>\n\n🆔 Chat ID: <code>{uid}</code>\n💰 Balans: <b>{get_user_balance(uid):,} so‘m</b>\n💎 Premium: <b>{'✅ Faol' if is_premium(uid) else '❌ Yo‘q'}</b>"
    bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🗣 Referal")
def referral_menu(m):
    bot_info = bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={m.from_user.id}"
    text = (
        f"🎁 <b>Do‘stlaringizni taklif qiling va bonus oling!</b>\n\n"
        f"🔗 Sizning havolangiz orqali to‘liq ro‘yxatdan o‘tgan har bir do‘stingiz uchun <b>{REFERRAL_BONUS:,} so‘m</b> taqdim etiladi.\n\n"
        f"👇 Boshlash uchun havolangiz:\n<code>{ref_link}</code>"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Do'stlarga yuborish", url=f"https://t.me/share/url?url={ref_link}&text=Zo'r bot yaratish platformasi!"))
    bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 Saytga kirish")
def website_link(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🌐 Saytga o'tish", url="http://abdurazokhov.carrd.co"))
    bot.send_message(m.chat.id, "👇 Quyidagi tugmani bosib bizning rasmiy saytimizga tashrif buyuring:", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ["💳 Hisob to'ldirish", "💬 Murojaat"])
def topup(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👨‍💻 Admin @vsf911", url="https://t.me/vsf911"))
    bot.send_message(m.chat.id, "📞 Ma'muriyat bilan bog‘lanish va hisobni to'ldirish uchun adminga yozing.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📚 Qo'llanma")
def channel(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 Kanalimizga o'tish", url="https://t.me/vsf_lvl"))
    bot.send_message(m.chat.id, f"📚 Barcha yangiliklar va qo'llanmalar kanalimizda: {CHANNEL_USERNAME}", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🤖 Botlarim")
def my_bots(m):
    uid = m.from_user.id
    bots = get_user_bots(uid)
    if not bots:
        bot.send_message(uid, "Siz hali bot yaratmadingiz. <b>🤖 Bot yaratish</b> tugmasi orqali boshlang!", parse_mode="HTML", reply_markup=main_menu())
        return
    for b in bots:
        _, _, bot_type, token, chat_id, name, status, users_count, _ = b
        status_emoji = "🟢" if status == "running" else "🔴"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 To‘xtatish" if status=="running" else "▶️ Ishga tushirish", callback_data=f"toggle_{b[0]}"))
        markup.add(InlineKeyboardButton("📄 Xatolikni ko'rish (Log)", callback_data=f"log_{b[0]}"))
        markup.add(InlineKeyboardButton("🗑 O‘chirish", callback_data=f"delete_{b[0]}"))
        bot.send_message(uid, f"{status_emoji} <b>{name}</b>\nTuri: {bot_type}\nHolati: {status.capitalize()}", parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🤖 Bot yaratish")
def create_bot(m):
    bot.send_message(m.chat.id, "🛠 <b>Qanday bot yaratmoqchisiz?</b>\nKerakli xizmatni tanlang:", parse_mode="HTML", reply_markup=bot_types_menu())

@bot.callback_query_handler(func=lambda call: not call.data.startswith("admin_"))
def user_callbacks(call):
    uid = call.from_user.id
    if call.data.startswith("buy_"):
        bot_type = call.data[4:]
        price = PRICES[bot_type]
        bal = get_user_balance(uid)
        
        if bal < price and not is_premium(uid):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("💰 Hisob to‘ldirish", url="https://t.me/vsf911"))
            bot.send_message(uid, f"❌ <b>Mablag‘ yetarli emas!</b>\n\nSumma: {price:,} so‘m\nBalans: {bal:,} so‘m", parse_mode="HTML", reply_markup=markup)
            return
            
        user_states[uid] = bot_type
        bot.send_message(uid, "✅ <b>Token va Chat ID yuboring:</b>\nFormat: <code>TOKEN|CHAT_ID</code>", parse_mode="HTML")
        
    elif call.data.startswith("toggle_"):
        bot_id = int(call.data[7:])
        cursor.execute("SELECT status FROM user_bots WHERE id=? AND user_id=?", (bot_id, uid))
        row = cursor.fetchone()
        if row:
            new_status = "stopped" if row[0]=="running" else "running"
            update_bot_status(bot_id, new_status)
            bot.answer_callback_query(call.id, f"✅ Bot holati: {new_status}")
            bot.delete_message(call.message.chat.id, call.message.message_id)
            my_bots(call.message)
            
    elif call.data.startswith("delete_"):
        bot_id = int(call.data[7:])
        delete_user_bot(bot_id, uid)
        bot.answer_callback_query(call.id, "✅ Bot o‘chirildi")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
    elif call.data.startswith("log_"):
        bot_id = int(call.data[4:])
        cursor.execute("SELECT bot_type FROM user_bots WHERE id=? AND user_id=?", (bot_id, uid))
        row = cursor.fetchone()
        if row:
            log_path = f"hosted_bots/log_{uid}_{row[0]}.txt"
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    content = f.read()[-3000:]
                bot.send_message(uid, f"📄 <b>Log:</b>\n<pre>{content or 'Boʻsh'}</pre>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def process_token(message):
    uid = message.from_user.id
    text = message.text.strip()
    if "|" not in text:
        bot.send_message(uid, "❌ Xato format!")
        user_states.pop(uid, None)
        return
        
    bot_type = user_states.pop(uid)
    token, chat_id = text.split("|", 1)
    
    try:
        tb = telebot.TeleBot(token.strip())
        bot_info = tb.get_me()
        bot_name = bot_info.first_name
    except:
        bot.send_message(uid, "❌ Token xato!")
        return
        
    if not is_premium(uid):
        deduct_balance(uid, PRICES.get(bot_type, 0))
        
    bot.send_message(uid, "⚙️ Tayyorlanmoqda...")
    success, str_result = host_bot(bot_type, token.strip(), chat_id.strip(), uid)
    
    if success:
        bid = add_user_bot(uid, bot_type, token.strip(), chat_id.strip(), bot_name)
        update_bot_status(bid, "running")
        bot.send_message(uid, f"✅ <b>{bot_name}</b> ishga tushdi!", parse_mode="HTML")
    else:
        add_balance(uid, PRICES.get(bot_type, 0))
        bot.send_message(uid, f"❌ Xato:\n<pre>{str_result}</pre>", parse_mode="HTML")

@bot.message_handler(commands=['admin'])
def admin_panel(m):
    if m.from_user.id != ADMIN_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💰 Pul qo'shish", callback_data="admin_add_funds"), InlineKeyboardButton("📊 Stat", callback_data="admin_stats"))
    bot.send_message(m.chat.id, "👑 Admin Panel", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_ID: return
    if call.data == "admin_add_funds":
        msg = bot.send_message(call.message.chat.id, "ID SUMMA yuboring:")
        bot.register_next_step_handler(msg, admin_process_funds)
    elif call.data == "admin_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        bot.edit_message_text(f"Foydalanuvchilar: {cursor.fetchone()[0]}", call.message.chat.id, call.message.message_id)

def admin_process_funds(message):
    try:
        uid, amt = message.text.split()
        add_balance(int(uid), int(amt))
        bot.send_message(ADMIN_ID, "✅ Qo'shildi")
    except: pass

async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    while True: # <--- IKKI NUQTA MANA SHU YERDA!
        await asyncio.sleep(3600)

def run_telegram():
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    threading.Thread(target=run_telegram, daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_web())
            
