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
    ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
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
DB_FILE = f"db_ai_image_{BOT_PREFIX}.sqlite"

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
            generations_count INTEGER DEFAULT 0
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
    if user_id in ADMIN_IDS:
        return True
    return False

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
    cur.execute("SELECT SUM(generations_count) FROM users")
    total_gens = cur.fetchone()[0] or 0
    conn.close()
    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "total_gens": total_gens
    }

def increment_generations(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET generations_count = generations_count + 1 WHERE user_id=?", (user_id,))
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
class AIStates(StatesGroup):
    waiting_t2i_prompt = State()
    
    waiting_edit_photo = State()
    waiting_edit_prompt = State()
    
    waiting_blend_photo1 = State()
    waiting_blend_photo2 = State()
    waiting_blend_prompt = State()
    
    waiting_broadcast = State()
    confirm_broadcast = State()
    waiting_block_id = State()
    waiting_unblock_id = State()

# ==================== KLAVIATURALAR ====================
def main_menu_keyboard(is_admin_user=False):
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🎨 Rasm yaratish (Text2Img)"))
    builder.add(KeyboardButton(text="🖼 Rasm tahrirlash (Img2Img)"))
    builder.add(KeyboardButton(text="🎭 Rasmlarni birlashtirish (Blend)"))
    builder.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        builder.add(KeyboardButton(text="👨‍💼 Admin panel"))
    builder.adjust(1, 2, 1, 1)
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
async def generate_nanobanana(prompt):
    url = "https://zecora0.serv00.net/ai/NanoBanana.php"
    # Zaxira modellar ro'yxati (bittasi ishlamasa ikkinchisi ishlaydi)
    models = ["NanoBanana2", "NanoBanana", "NanoBananaPro"]
    
    async with aiohttp.ClientSession() as session:
        for model in models:
            data = {"text": prompt, "model": model, "ratio": "1:1", "res": "2K"}
            try:
                async with session.post(url, data=data, timeout=60) as resp:
                    if resp.status == 200:
                        res_json = await resp.json()
                        if res_json.get("success") and res_json.get("url"):
                            return res_json.get("url")
            except Exception as e:
                logging.error(f"NanoBanana {model} error: {e}")
                continue # Keyingi modelga o'tish
    return None

async def edit_image_kilwa(img_url, prompt):
    url = f"http://de3.bot-hosting.net:21007/kilwa-edit?img={img_url}&text={prompt}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'application/json' in content_type:
                        data = await resp.json()
                        return data.get('url', None), True # URL qaytsa
                    else:
                        img_bytes = await resp.read()
                        return img_bytes, False # Rasm baytlari qaytsa
        except Exception as e:
            logging.error(f"Kilwa Edit error: {e}")
    return None, False

async def blend_image_kilwa(img1_url, img2_url, prompt):
    url = f"http://de3.bot-hosting.net:21007/kilwa-blend?img1={img1_url}&img2={img2_url}&text={prompt}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'application/json' in content_type:
                        data = await resp.json()
                        return data.get('url', None), True
                    else:
                        img_bytes = await resp.read()
                        return img_bytes, False
        except Exception as e:
            logging.error(f"Kilwa Blend error: {e}")
    return None, False

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
        f"🎨 Men eng so'nggi Neyrotarmoqlar orqali (Matndan rasm, Tahrirlash, Birlashtirish) amallarini bajaruvchi Mukammal AI botman.\n\n"
        f"Pastdagi menyudan kerakli bo'limni tanlang 👇\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(admin)
    )

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤖 <b>Bot imkoniyatlari:</b>\n\n"
        "1️⃣ <b>Text2Img:</b> O'z hayolotingizdagi rasmni so'zlar orqali yarating.\n"
        "2️⃣ <b>Img2Img:</b> Mavjud rasmni yuboring va uni o'zgartirish uchun matn yozing.\n"
        "3️⃣ <b>Blend:</b> 2 ta rasmni yuboring va ularni bitta san'at asariga aylantiring.\n\n"
        "<i>Barcha so'rovlarni iloji boricha 🇬🇧 Ingliz tilida yozsangiz, sifatliroq natija olasiz!</i>\n\n"
        "⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "cancel_action")
async def cancel_action_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Amaliyot bekor qilindi.")
    await callback.answer()

# ----------------- 1. TEXT TO IMAGE -----------------
@dp.message(F.text == "🎨 Rasm yaratish (Text2Img)")
async def t2i_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "🎨 <b>Rasm yaratish bo'limi</b>\n\n"
        "Qanday rasm yaratmoqchisiz? Iltimos, batafsil ta'rif (prompt) yozing (Ingliz tilida tavsiya etiladi):",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(AIStates.waiting_t2i_prompt)

@dp.message(AIStates.waiting_t2i_prompt)
async def t2i_process(message: Message, state: FSMContext):
    prompt = message.text
    if not prompt: return
    
    msg = await message.answer("⏳ <i>Rasm chizilmoqda, bu 1-2 daqiqa vaqt olishi mumkin... Kuting</i>", parse_mode="HTML")
    await state.clear()
    
    img_url = await generate_nanobanana(prompt)
    
    if img_url:
        caption = f"🎨 <b>Prompt:</b> {prompt[:800]}\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"
        try:
            await message.answer_photo(img_url, caption=caption, parse_mode="HTML")
            increment_generations(message.from_user.id)
        except Exception as e:
            await message.answer(f"❌ Rasmni yuborishda xatolik: {e}")
    else:
        await message.answer("❌ Kechirasiz, API serverlarida vaqtinchalik xatolik yuz berdi. Birozdan so'ng urinib ko'ring.")
    await msg.delete()

# ----------------- 2. IMAGE EDIT -----------------
@dp.message(F.text == "🖼 Rasm tahrirlash (Img2Img)")
async def edit_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "🖼 <b>Rasm tahrirlash bo'limi</b>\n\n"
        "O'zgartirmoqchi bo'lgan <b>rasmingizni</b> yuboring:",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(AIStates.waiting_edit_photo)

@dp.message(AIStates.waiting_edit_photo, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    
    await state.update_data(edit_img_url=img_url)
    await message.answer(
        "✅ Rasm qabul qilindi!\n\nEndi rasmni qanday tahrirlash kerakligi haqida <b>matn (prompt)</b> yozing:",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(AIStates.waiting_edit_prompt)

@dp.message(AIStates.waiting_edit_prompt)
async def edit_process(message: Message, state: FSMContext):
    prompt = message.text
    if not prompt: return
    
    data = await state.get_data()
    img_url = data.get("edit_img_url")
    
    msg = await message.answer("⏳ <i>Rasm tahrirlanmoqda... Kuting</i>", parse_mode="HTML")
    await state.clear()
    
    result, is_url = await edit_image_kilwa(img_url, prompt)
    
    caption = f"🖼 <b>Tahrirlandi:</b> {prompt[:800]}\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"
    
    if result:
        try:
            if is_url:
                await message.answer_photo(result, caption=caption, parse_mode="HTML")
            else:
                await message.answer_photo(BufferedInputFile(result, "edited.jpg"), caption=caption, parse_mode="HTML")
            increment_generations(message.from_user.id)
        except Exception as e:
            await message.answer(f"❌ Rasmni yuborishda xatolik: {e}")
    else:
        await message.answer("❌ Kechirasiz, Tahrirlash API da xatolik yuz berdi. Boshqa rasm bilan urinib ko'ring.")
    await msg.delete()

# ----------------- 3. IMAGE BLEND -----------------
@dp.message(F.text == "🎭 Rasmlarni birlashtirish (Blend)")
async def blend_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "🎭 <b>Rasmlarni birlashtirish</b>\n\n"
        "Iltimos, <b>1-rasmni</b> yuboring (Asosiy fon yoki shakl uchun):",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(AIStates.waiting_blend_photo1)

@dp.message(AIStates.waiting_blend_photo1, F.photo)
async def blend_photo1(message: Message, state: FSMContext):
    file = await bot.get_file(message.photo[-1].file_id)
    img1_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    
    await state.update_data(blend_img1=img1_url)
    await message.answer("✅ 1-rasm qabul qilindi!\n\nEndi <b>2-rasmni</b> yuboring (Stil yoki tekstura uchun):", reply_markup=cancel_inline())
    await state.set_state(AIStates.waiting_blend_photo2)

@dp.message(AIStates.waiting_blend_photo2, F.photo)
async def blend_photo2(message: Message, state: FSMContext):
    file = await bot.get_file(message.photo[-1].file_id)
    img2_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    
    await state.update_data(blend_img2=img2_url)
    await message.answer(
        "✅ 2-rasm ham qabul qilindi!\n\nEndi bu ikki rasmni qanday uyg'unlashtirish haqida <b>matn (prompt)</b> yozing (masalan: 'make it cinematic'):",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(AIStates.waiting_blend_prompt)

@dp.message(AIStates.waiting_blend_prompt)
async def blend_process(message: Message, state: FSMContext):
    prompt = message.text
    if not prompt: return
    
    data = await state.get_data()
    img1_url = data.get("blend_img1")
    img2_url = data.get("blend_img2")
    
    msg = await message.answer("⏳ <i>Rasmlar birlashtirilmoqda... Kuting</i>", parse_mode="HTML")
    await state.clear()
    
    result, is_url = await blend_image_kilwa(img1_url, img2_url, prompt)
    
    caption = f"🎭 <b>Birlashtirildi:</b> {prompt[:800]}\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"
    
    if result:
        try:
            if is_url:
                await message.answer_photo(result, caption=caption, parse_mode="HTML")
            else:
                await message.answer_photo(BufferedInputFile(result, "blended.jpg"), caption=caption, parse_mode="HTML")
            increment_generations(message.from_user.id)
        except Exception as e:
            await message.answer(f"❌ Rasmni yuborishda xatolik: {e}")
    else:
        await message.answer("❌ Kechirasiz, Blend API da xatolik yuz berdi.")
    await msg.delete()

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
        f"🖼 Yaratilgan rasmlar (Jami): {stats['total_gens']}\n\n"
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
    await state.set_state(AIStates.waiting_block_id)

@dp.message(AIStates.waiting_block_id)
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
    await state.set_state(AIStates.waiting_unblock_id)

@dp.message(AIStates.waiting_unblock_id)
async def process_admin_unblock_user(message: Message, state: FSMContext):
    if not is_admin(message