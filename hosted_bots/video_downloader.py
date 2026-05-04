import asyncio
import logging
import sqlite3
import os
import re
from datetime import datetime
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
import yt_dlp

# ==================== KONFIGURATSIYA (Builder o'zgartiradi) ====================
BOT_TOKEN = "8734482130:AAGg_kg2l2qct3wvgMYm-YaR86vCFcSdt4M"  
ADMIN_ID_STR = "8426582765"

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except:
    ADMIN_ID = 0

ADMIN_IDS = [ADMIN_ID]             
MAX_FILE_SIZE = 50 * 1024 * 1024    

# Izolyatsiya qilingan papkalar va baza
BOT_PREFIX = BOT_TOKEN.split(':')[0] if ':' in BOT_TOKEN else "unknown"
TEMP_DIR = f"downloads_{BOT_PREFIX}"               
DB_FILE = f"db_downloader_{BOT_PREFIX}.sqlite"

# URL larni vaqtincha saqlash uchun
user_temp_urls = {}

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO)

# ==================== BOT VA DISPATCHER ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== PAPKALARNI YARATISH ====================
os.makedirs(TEMP_DIR, exist_ok=True)

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
            downloads_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cached_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            media_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            added_date TEXT,
            UNIQUE(url, media_type)
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
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row and row[0] == 1

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
    cur.execute("SELECT SUM(downloads_count) FROM users")
    total_downloads = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM cached_media")
    cached_media = cur.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "total_downloads": total_downloads,
        "cached_media": cached_media
    }

def increment_downloads(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET downloads_count = downloads_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_cached_file_id(url, media_type):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT file_id FROM cached_media WHERE url=? AND media_type=?", (url, media_type))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def cache_media(url, media_type, file_id, platform, title):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""
        INSERT OR REPLACE INTO cached_media (url, media_type, file_id, platform, title, added_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (url, media_type, file_id, platform, title, now))
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

# ==================== PLATFORMANI ANIQLASH ====================
def detect_platform(url):
    patterns = {
        "youtube": r"(youtube\.com|youtu\.be)",
        "instagram": r"(instagram\.com)",
        "tiktok": r"(tiktok\.com)"
    }
    for platform, pattern in patterns.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "unknown"

# ==================== MEDIA YUKLASH (VIDEO/AUDIO) ====================
async def download_media(url, media_type, user_id):
    platform = detect_platform(url)
    if platform == "unknown":
        return None, "Faqat YouTube, Instagram va TikTok linklari qabul qilinadi."
    
    if media_type == "video":
        ydl_opts = {
            'format': 'best[filesize<50M]/best',
            'outtmpl': f'{TEMP_DIR}/%(title)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
        }
    else: 
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{TEMP_DIR}/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
        }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Media')
            
            if media_type == "video":
                filename = ydl.prepare_filename(info)
            else:
                filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            
            if not os.path.exists(filename):
                files = os.listdir(TEMP_DIR)
                files.sort(key=lambda x: os.path.getctime(os.path.join(TEMP_DIR, x)), reverse=True)
                if files:
                    filename = os.path.join(TEMP_DIR, files[0])
                else:
                    return None, "Yuklab olishda xatolik: fayl topilmadi."
            
            return filename, title
    except Exception as e:
        logging.error(f"Download error: {e}")
        return None, f"Xatolik: {str(e)[:100]}"

# ==================== HOLATLAR ====================
class BroadcastState(StatesGroup):
    waiting_for_message = State()
    confirm_broadcast = State()
    waiting_block_id = State()
    waiting_unblock_id = State()

# ==================== KLAVIATURALAR ====================
def main_menu_keyboard(is_admin_user=False):
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="📥 Yuklash"))
    builder.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        builder.add(KeyboardButton(text="👨‍💼 Admin panel"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    builder.button(text="🔇 Bloklash", callback_data="admin_block_user")
    builder.button(text="🔊 Blokdan chiqarish", callback_data="admin_unblock_user")
    builder.button(text="📢 Reklama yuborish", callback_data="admin_broadcast")
    builder.button(text="🗑 Kesh tozalash", callback_data="admin_clear_cache")
    builder.adjust(2)
    return builder.as_markup()

def media_type_keyboard(url):
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Video (MP4)", callback_data=f"dl_video")  
    builder.button(text="🎵 Audio (MP3)", callback_data=f"dl_audio")
    builder.button(text="❌ Bekor qilish", callback_data="dl_cancel")
    builder.adjust(2)
    return builder.as_markup()

def back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Orqaga", callback_data="back_to_admin")
    return builder.as_markup()

def cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="❌ Bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    admin = is_admin(user.id)
    
    if is_blocked(user.id):
        await message.answer("🚫 Siz bloklangansiz. Admin bilan bog'laning.")
        return
    
    await message.answer(
        f"Assalomu alaykum, {user.first_name}! 👋\n\n"
        f"Men Instagram, YouTube va TikTok dan video va audio yuklab beruvchi botman.\n\n"
        f"📥 Menga link yuboring va kerakli formatni tanlang.\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(admin)
    )

@dp.message(F.text == "📥 Yuklash")
async def yuklash_help(message: Message):
    await message.answer(
        "📤 Quyidagi platformalardan link yuboring:\n"
        "• Instagram (Reels, Post, Stories)\n"
        "• YouTube (video, Shorts)\n"
        "• TikTok\n\n"
        "So‘ng video yoki audio tanlaysiz.\n\n"
        "⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML"
    )

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(message: Message):
    await message.answer(
        "🤖 <b>Bot haqida</b>\n\n"
        "Bu bot Instagram, YouTube va TikTok dan video va audio yuklab beradi.\n\n"
        "<b>Qanday ishlatish:</b>\n"
        "1. Linkni yuboring\n"
        "2. Video yoki audio tanlang\n"
        "3. Yuklab olinib, sizga yuboriladi\n\n"
        "<b>Limit:</b> 50 MB gacha\n\n"
        "⚙️ <b>Yaratuvchi:</b> @vsf911",
        parse_mode="HTML"
    )

@dp.message(F.text == "👨‍💼 Admin panel")
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    await message.answer("👨‍💼 Admin panel\n⚙️ Yaratuvchi: @vsf911", reply_markup=admin_panel_keyboard())

# ----------------- ADMIN CALLBACKLAR -----------------
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q")
        return
    stats = get_stats()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: {stats['total_users']}\n"
        f"🔇 Bloklanganlar: {stats['blocked_users']}\n"
        f"📥 Yuklamalar: {stats['total_downloads']}\n"
        f"💾 Keshdagi media: {stats['cached_media']}\n\n"
        f"⚙️ <b>Yaratuvchi:</b> @vsf911"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    users = get_all_users(only_active=False)
    if not users:
        await callback.message.edit_text("Foydalanuvchilar yo'q.", reply_markup=back_keyboard())
        return
    text = "👥 <b>Foydalanuvchilar ro'yxati:</b>\n\n"
    for uid, username, name in users[:20]:
        username_str = f"@{username}" if username else "no username"
        text += f"• {name} ({username_str}) - <code>{uid}</code>\n"
    if len(users) > 20:
        text += f"\n...va yana {len(users)-20} ta"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_block_user")
async def admin_block_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("Bloklash uchun foydalanuvchi Telegram ID sini yuboring:", reply_markup=back_keyboard())
    await state.set_state(BroadcastState.waiting_block_id)
    await callback.answer()

@dp.message(BroadcastState.waiting_block_id)
async def process_admin_block_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        user_id = int(message.text.strip())
        block_user(user_id)
        await message.answer(f"✅ Foydalanuvchi {user_id} bloklandi.", reply_markup=main_menu_keyboard(True))
    except:
        await message.answer("❌ Noto'g'ri ID.", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_unblock_user")
async def admin_unblock_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("Blokdan chiqarish uchun foydalanuvchi Telegram ID sini yuboring:", reply_markup=back_keyboard())
    await state.set_state(BroadcastState.waiting_unblock_id)
    await callback.answer()

@dp.message(BroadcastState.waiting_unblock_id)
async def process_admin_unblock_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        user_id = int(message.text.strip())
        unblock_user(user_id)
        await message.answer(f"✅ Foydalanuvchi {user_id} blokdan chiqarildi.", reply_markup=main_menu_keyboard(True))
    except:
        await message.answer("❌ Noto'g'ri ID.", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("📢 Yubormoqchi bo'lgan xabaringizni yozing:", reply_markup=back_keyboard())
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def admin_broadcast_preview(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    await state.update_data(broadcast_text=message.text)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Ha, yuborish", callback_data="broadcast_confirm")
    keyboard.button(text="❌ Yo'q", callback_data="broadcast_cancel")
    await message.answer(f"📢 Xabar:\n\n{message.text}\n\nYuborishni tasdiqlaysizmi?", reply_markup=keyboard.as_markup())
    await state.set_state(BroadcastState.confirm_broadcast)

@dp.callback_query(F.data == "broadcast_confirm")
async def admin_broadcast_send(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    text = data.get('broadcast_text')
    await callback.message.edit_text("📨 Xabar yuborilmoqda...")
    users = get_all_users(only_active=True)
    sent = failed = 0
    for uid, _, _ in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    add_broadcast(callback.from_user.id, text, sent, failed)
    await callback.message.edit_text(f"✅ Yuborildi: {sent}, Xatolik: {failed}", reply_markup=back_keyboard())
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "broadcast_cancel")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Bekor qilindi.", reply_markup=back_keyboard())
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "admin_clear_cache")
async def admin_clear_cache(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    count = 0
    for f in os.listdir(TEMP_DIR):
        try:
            os.remove(os.path.join(TEMP_DIR, f))
            count += 1
        except: pass
    await callback.message.edit_text(f"✅ {count} ta vaqtinchalik fayl o'chirildi.", reply_markup=back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback: CallbackQuery):
    await callback.message.edit_text("👨‍💼 Admin panel\n⚙️ Yaratuvchi: @vsf911", reply_markup=admin_panel_keyboard())
    await callback.answer()

# ----------------- ASOSIY URL HANDLER -----------------
@dp.message()
async def handle_url(message: Message):
    user_id = message.from_user.id
    if is_blocked(user_id):
        await message.answer("🚫 Siz bloklangansiz.")
        return
    
    url = message.text.strip()
    if not re.match(r'https?://', url):
        return
    
    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer("❌ Faqat YouTube, Instagram va TikTok linklari qabul qilinadi.")
        return
    
    await message.answer("Qanday formatda yuklaymiz?", reply_markup=media_type_keyboard(url))
    user_temp_urls[user_id] = url

# ----------------- TANLOV CALLBACKLARI -----------------
@dp.callback_query(F.data == "dl_video")
async def download_video_call(callback: CallbackQuery):
    user_id = callback.from_user.id
    original_url = user_temp_urls.get(user_id)
    if not original_url:
        await callback.message.edit_text("❌ Xatolik: URL topilmadi. Qaytadan link yuboring.")
        return
    
    await callback.message.edit_text("⏳ Video yuklanmoqda...")
    await process_download(callback.message, original_url, "video", user_id)

@dp.callback_query(F.data == "dl_audio")
async def download_audio_call(callback: CallbackQuery):
    user_id = callback.from_user.id
    original_url = user_temp_urls.get(user_id)
    if not original_url:
        await callback.message.edit_text("❌ Xatolik: URL topilmadi. Qaytadan link yuboring.")
        return
    
    await callback.message.edit_text("