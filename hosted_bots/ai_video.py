import asyncio
import logging
import sqlite3
import os
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
DB_FILE = f"db_ai_video_{BOT_PREFIX}.sqlite"

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
class VideoStates(StatesGroup):
    waiting_t2v_prompt = State()
    
    waiting_i2v_photo = State()
    waiting_i2v_prompt = State()
    
    waiting_broadcast = State()
    confirm_broadcast = State()
    waiting_block_id = State()
    waiting_unblock_id = State()

# ==================== KLAVIATURALAR ====================
def main_menu_keyboard(is_admin_user=False):
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🎬 Matndan Video (Text2Video)"))
    builder.add(KeyboardButton(text="🎞 Rasmdan Video (Img2Video)"))
    builder.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        builder.add(KeyboardButton(text="👨‍💼 Admin panel"))
    builder.adjust(1, 1, 2)
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

def aspect_ratio_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 16:9 (Yotish format)", callback_data="aspect_16:9")
    builder.button(text="📱 9:16 (Tik format)", callback_data="aspect_9:16")
    builder.button(text="❌ Bekor qilish", callback_data="cancel_action")
    builder.adjust(2, 1)
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
async def generate_video_api(prompt, aspect_ratio="16:9", image_url=None):
    """Vetrex (Veo 3.1) orqali video yasash va Seedance zaxirasi"""
    
    # 1. Asosiy API: Vetrex (Veo 3.1)
    url = "https://vetrex.site/v1/videos/generations"
    payload = {
        "prompt": prompt,
        "model": "veo-3.1"
    }
    
    if image_url:
        payload["images"] = [image_url]
    else:
        payload["aspect_ratio"] = aspect_ratio
        
    async with aiohttp.ClientSession() as session:
        try:
            # So'rov yuborish
            async with session.post(url, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    task_id = data.get('task_id')
                    
                    if task_id:
                        check_url = data.get('check_url', f"https://vetrex.site/v1/videos/results/{task_id}")
                        
                        # Tayyor bo'lishini kutish (Polling) - Maksimal 5 daqiqa
                        for _ in range(30):
                            await asyncio.sleep(10) # Har 10 soniyada tekshiradi
                            async with session.get(check_url, timeout=10) as status_resp:
                                if status_resp.status == 200:
                                    status_data = await status_resp.json()
                                    if status_data.get('status') == 'completed':
                                        return status_data.get('url'), None
                                    elif status_data.get('status') == 'failed':
                                        return None, status_data.get('error', 'API xatosi')
        except Exception as e:
            logging.error(f"Veo 3.1 API Error: {e}")
            
        # 2. Zaxira API: Seedance (Agar Veo 3.1 ishlamasa)
        try:
            seedance_url = "https://zecora0.serv00.net/ai/Seedance.php"
            data = {"text": prompt, "model": "Seedance"}
            if image_url: data["images"] = image_url
            
            async with session.post(seedance_url, data=data, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success') and data.get('url'):
                        return data.get('url'), None
        except Exception as e:
            logging.error(f"Seedance API Error: {e}")
            
    return None, "Server bilan ulanishda xatolik yoki vaqt tugadi."

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
        f"🎥 Men <b>Veo 3.1 (Cinematic AI Motion)</b> orqali 4K sifatdagi videolarni matndan yoki rasmdan harakatlantirib beruvchi botman.\n\n"
        f"Kerakli bo'limni tanlang 👇\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(admin)
    )

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤖 <b>Bot imkoniyatlari (Veo 3.1 API):</b>\n\n"
        "1️⃣ <b>Matndan Video:</b> O'z ssenariyingizni (prompt) yozasiz, bot 8 soniyalik realistik video yasab beradi.\n"
        "2️⃣ <b>Rasmdan Video:</b> O'zingiz xohlagan rasmni yuborasiz, bot rasmdagi obyektlarni harakatga keltirib, jonlantirib beradi.\n\n"
        "<i>Eslatma: Promptlarni (ta'riflarni) 🇬🇧 Ingliz tilida yozish tavsiya etiladi!</i>\n\n"
        "⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "cancel_action")
async def cancel_action_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Amaliyot bekor qilindi.")
    await callback.answer()

# ----------------- 1. TEXT TO VIDEO -----------------
@dp.message(F.text == "🎬 Matndan Video (Text2Video)")
async def t2v_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "🎬 <b>Matndan Video Yaratish</b>\n\n"
        "Qanday video yaratmoqchisiz? Ssenariyni to'liq tasvirlab yozing (Masalan: <i>A futuristic city at sunset, 4k, cinematic</i>):",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(VideoStates.waiting_t2v_prompt)

@dp.message(VideoStates.waiting_t2v_prompt)
async def t2v_prompt_received(message: Message, state: FSMContext):
    prompt = message.text
    if not prompt: return
    await state.update_data(prompt=prompt)
    await message.answer(
        "🖥 <b>Videonining o'lchamini (Aspect Ratio) tanlang:</b>",
        parse_mode="HTML", reply_markup=aspect_ratio_keyboard()
    )
    # State-ni tozalaymiz, chunki endi inline button kutiladi
    await state.set_state(None)

@dp.callback_query(F.data.startswith("aspect_"))
async def t2v_process(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    prompt = data.get("prompt")
    if not prompt:
        await callback.message.edit_text("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return
    
    aspect_ratio = callback.data.split("_")[1] # "16:9" or "9:16"
    
    msg = await callback.message.edit_text("⏳ <i>Video yaratilmoqda (Bu jarayon 1-3 daqiqa vaqt oladi, iltimos kuting)...</i>", parse_mode="HTML")
    await state.clear()
    
    video_url, error = await generate_video_api(prompt, aspect_ratio=aspect_ratio)
    
    if video_url:
        caption = f"🎬 <b>Prompt:</b> {prompt[:500]}\n📐 <b>Format:</b> {aspect_ratio} (Veo 3.1)\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"
        try:
            await callback.message.answer_video(video_url, caption=caption, parse_mode="HTML")
            increment_generations(callback.from_user.id)
        except Exception as e:
            await callback.message.answer(f"❌ Videoni yuklashda xatolik: {e}\n🔗 Link: {video_url}")
    else:
        await callback.message.answer(f"❌ Xatolik yuz berdi: {error}")
    await msg.delete()

# ----------------- 2. IMAGE TO VIDEO -----------------
@dp.message(F.text == "🎞 Rasmdan Video (Img2Video)")
async def i2v_start(message: Message, state: FSMContext):
    if is_blocked(message.from_user.id): return
    await message.answer(
        "🎞 <b>Rasmni Harakatga Keltirish</b>\n\n"
        "Jonlantirmoqchi bo'lgan <b>rasmingizni</b> yuboring:",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(VideoStates.waiting_i2v_photo)

@dp.message(VideoStates.waiting_i2v_photo, F.photo)
async def i2v_photo_received(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    
    await state.update_data(img_url=img_url)
    await message.answer(
        "✅ Rasm qabul qilindi!\n\nEndi rasmda nimalar harakatlanishi kerakligini <b>prompt</b> qilib yozing (masalan: <i>make it move, realistic motion</i>):",
        parse_mode="HTML", reply_markup=cancel_inline()
    )
    await state.set_state(VideoStates.waiting_i2v_prompt)

@dp.message(VideoStates.waiting_i2v_prompt)
async def i2v_process(message: Message, state: FSMContext):
    prompt = message.text
    if not prompt: return
    
    data = await state.get_data()
    img_url = data.get("img_url")
    
    msg = await message.answer("⏳ <i>Rasm videoga aylantirilmoqda (1-3 daqiqa kuting)...</i>", parse_mode="HTML")
    await state.clear()
    
    video_url, error = await generate_video_api(prompt, image_url=img_url)
    
    caption = f"🎞 <b>Harakatlantirildi:</b> {prompt[:500]}\n✨ <b>Model:</b> Veo 3.1\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"
    
    if video_url:
        try:
            await message.answer_video(video_url, caption=caption, parse_mode="HTML")
            increment_generations(message.from_user.id)
        except Exception as e:
            await message.answer(f"❌ Videoni yuborishda xatolik: {e}\n🔗 Link: {video_url}")
    else:
        await message.answer(f"❌ Serverda xatolik yuz berdi: {error}")
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
        f"🎬 Yaratilgan videolar (Jami): {stats['total_gens']}\n\n"
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
    await state.set_state(VideoStates.waiting_block_id)

@dp.message(VideoStates.waiting_block_id)
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
    await state.set_state(VideoStates.waiting_unblock_id)

@dp.message(VideoStates.waiting_unblock_id)
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
    await state.set_state(VideoStates.waiting_broadcast)

@dp.message(VideoStates.waiting_broadcast)
async def admin_broadcast_preview(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    
    await state.update_data(bcast_msg_id=message.message_id, bcast_chat_id=message.chat.id)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Ha, yuborish", callback_data="broadcast_confirm")
    keyboard.button(text="❌ Yo'q", callback_data="cancel_action")
    
    await message.copy_to(message.chat.id, reply_markup=keyboard.as_markup())
    await message.answer("Yuborishni tasdiqlaysizmi?")
    await state.set_state(VideoStates.confirm_broadcast)

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
       