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
    Message, CallbackQuery, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import yt_dlp

# ==================== KONFIGURATSIYA ====================
BOT_TOKEN = "BOT_TOKEN"  
ADMIN_ID_STR = "ADMIN_ID"

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except:
    ADMIN_ID = 0

ADMIN_IDS = [ADMIN_ID]             
MAX_FILE_SIZE = 50 * 1024 * 1024    

BOT_PREFIX = BOT_TOKEN.split(':')[0] if ':' in BOT_TOKEN else "unknown"
TEMP_DIR = f"downloads_{BOT_PREFIX}"               
DB_FILE = f"db_downloader_{BOT_PREFIX}.sqlite"

user_urls = {}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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

def add_user(uid, uname, fname, lname):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, joined_date) VALUES (?, ?, ?, ?, ?)""", (uid, uname, fname, lname, now))
    conn.commit()
    conn.close()

def is_admin(uid):
    return uid in ADMIN_IDS

def is_blocked(uid):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""SELECT is_blocked FROM users WHERE user_id=?""", (uid,))
    row = cur.fetchone()
    conn.close()
    return row and row[0] == 1

def block_user(uid):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""UPDATE users SET is_blocked=1 WHERE user_id=?""", (uid,))
    conn.commit()
    conn.close()

def unblock_user(uid):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""UPDATE users SET is_blocked=0 WHERE user_id=?""", (uid,))
    conn.commit()
    conn.close()

def get_all_users(only_active=True):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    if only_active:
        cur.execute("""SELECT user_id, username, first_name FROM users WHERE is_blocked=0""")
    else:
        cur.execute("""SELECT user_id, username, first_name FROM users""")
    users = cur.fetchall()
    conn.close()
    return users

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM users""")
    tot = cur.fetchone()[0]
    cur.execute("""SELECT COUNT(*) FROM users WHERE is_blocked=1""")
    blk = cur.fetchone()[0]
    cur.execute("""SELECT SUM(downloads_count) FROM users""")
    dl = cur.fetchone()[0] or 0
    conn.close()
    return {"tot": tot, "blk": blk, "dl": dl}

def inc_dl(uid):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""UPDATE users SET downloads_count = downloads_count + 1 WHERE user_id=?""", (uid,))
    conn.commit()
    conn.close()

def add_broadcast(aid, txt, sent, failed):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""INSERT INTO broadcasts (admin_id, message_text, sent_count, failed_count, created_at) VALUES (?, ?, ?, ?, ?)""", (aid, txt, sent, failed, now))
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

# ==================== HOLATLAR ====================
class BcastState(StatesGroup):
    wait_msg = State()
    confirm = State()
    wait_block = State()
    wait_unblock = State()

# ==================== KLAVIATURALAR ====================
def main_kb(is_admin_user=False):
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text="📥 Yuklash"))
    b.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        b.add(KeyboardButton(text="👨‍💼 Admin panel"))
    b.adjust(2)
    return b.as_markup(resize_keyboard=True)

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Statistika", callback_data="admin_stats")
    b.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    b.button(text="🔇 Bloklash", callback_data="admin_block")
    b.button(text="🔊 Qaytarish", callback_data="admin_unblock")
    b.button(text="📢 Reklama", callback_data="admin_bcast")
    b.button(text="🗑 Kesh tozalash", callback_data="admin_clear")
    b.adjust(2)
    return b.as_markup()

def media_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🎬 Video (MP4)", callback_data="dl_video")  
    b.button(text="🎵 Audio (MP3)", callback_data="dl_audio")
    b.button(text="❌ Bekor qilish", callback_data="dl_cancel")
    b.adjust(2)
    return b.as_markup()

def back_admin():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="back_admin")
    return b.as_markup()

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    add_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    if is_blocked(m.from_user.id):
        await m.answer("""🚫 Siz bloklangansiz. Admin bilan bog'laning.""")
        return
    
    await m.answer(f"""Assalomu alaykum, <b>{m.from_user.first_name}</b>! 👋\n\nMen Instagram, YouTube va TikTok dan video va audio yuklab beruvchi botman.\n\n📥 Menga link yuboring va kerakli formatni tanlang.\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML", reply_markup=main_kb(is_admin(m.from_user.id)))

@dp.message(F.text == "📥 Yuklash")
async def yuklash_help(m: Message):
    await m.answer("""📤 Quyidagi platformalardan link yuboring:\n• Instagram (Reels, Post, Stories)\n• YouTube (video, Shorts)\n• TikTok\n\nSo‘ng video yoki audio tanlaysiz.""")

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(m: Message):
    await m.answer("""🤖 <b>Bot haqida</b>\n\nBu bot Instagram, YouTube va TikTok dan video va audio yuklab beradi.\n\n<b>Qanday ishlatish:</b>\n1. Linkni yuboring\n2. Video yoki audio tanlang\n3. Yuklab olinib, sizga yuboriladi\n\n<b>Limit:</b> 50 MB gacha\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML")

@dp.callback_query(F.data == "dl_cancel")
async def dl_cancel(c: CallbackQuery):
    await c.message.edit_text("""❌ Amaliyot bekor qilindi.""")
    await c.answer()

# ----------------- URL HANDLER -----------------
@dp.message()
async def handle_url(m: Message):
    if is_blocked(m.from_user.id): return
    url = m.text.strip()
    if not re.match(r'https?://', url): return
    
    plat = detect_platform(url)
    if plat == "unknown":
        await m.answer("""❌ Faqat YouTube, Instagram va TikTok linklari qabul qilinadi.""")
        return
    
    user_urls[m.from_user.id] = url
    await m.answer("""Qanday formatda yuklaymiz?""", reply_markup=media_kb())

# ----------------- DOWNLOAD CALLBACKS -----------------
@dp.callback_query(F.data.in_(["dl_video", "dl_audio"]))
async def dl_media(c: CallbackQuery):
    uid = c.from_user.id
    url = user_urls.get(uid)
    if not url:
        await c.message.edit_text("""❌ Xatolik: Link topilmadi. Qaytadan havolani yuboring.""")
        return
    
    mtype = "video" if c.data == "dl_video" else "audio"
    msg = await c.message.edit_text(f"""⏳ <i>{mtype.capitalize()} yuklanmoqda... Kuting</i>""", parse_mode="HTML")
    
    if mtype == "video":
        opts = {'format': 'best[filesize<50M]/best', 'outtmpl': f'{TEMP_DIR}/%(title)s.%(ext)s', 'quiet': True, 'ignoreerrors': True}
    else: 
        opts = {'format': 'bestaudio/best', 'outtmpl': f'{TEMP_DIR}/%(title)s.%(ext)s', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}], 'quiet': True, 'ignoreerrors': True}
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Media')
            
            fn = ydl.prepare_filename(info)
            if mtype == "audio": fn = fn.rsplit('.', 1)[0] + '.mp3'
            
            if not os.path.exists(fn):
                files = os.listdir(TEMP_DIR)
                files.sort(key=lambda x: os.path.getctime(os.path.join(TEMP_DIR, x)), reverse=True)
                fn = os.path.join(TEMP_DIR, files[0])
            
            fsize = os.path.getsize(fn)
            if fsize > MAX_FILE_SIZE:
                await msg.edit_text(f"""❌ Media hajmi katta ({fsize/1024/1024:.1f} MB). Telegram limiti 50 MB.""")
                os.remove(fn)
                return
            
            with open(fn, 'rb') as f:
                if mtype == "video":
                    await c.message.answer_video(types.BufferedInputFile(f.read(), "video.mp4"), caption=f"""✅ Video tayyor!\n📹 {title[:50]}...\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML")
                else:
                    await c.message.answer_audio(types.BufferedInputFile(f.read(), "audio.mp3"), caption=f"""✅ Audio tayyor!\n🎵 {title[:50]}...\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML")
            
            inc_dl(uid)
            await msg.delete()
            os.remove(fn)
            
    except Exception as e:
        await msg.edit_text(f"""❌ Xatolik yuz berdi. Bu yopiq profil yoki noto'g'ri link bo'lishi mumkin.""")

# ----------------- ADMIN PANEL -----------------
@dp.message(F.text == "👨‍💼 Admin panel")
async def adm_st(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer("""👨‍💼 <b>Admin panel</b>\n\n⚙️ Yaratuvchi: @vsf911""", parse_mode="HTML", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_stats")
async def adm_stat(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    st = get_stats()
    await c.message.edit_text(f"""📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {st['tot']}\n🔇 Bloklanganlar: {st['blk']}\n📥 Yuklamalar: {st['dl']}\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML", reply_markup=back_admin())

@dp.callback_query(F.data == "admin_users")
async def adm_usr(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    u = get_all_users(False)
    if not u:
        await c.message.edit_text("""Foydalanuvchilar yo'q.""", reply_markup=back_admin())
        return
    t = """👥 <b>Foydalanuvchilar:</b>\n\n"""
    for uid, un, fn in u[:20]:
        t += f"""• {fn} (@{un}) - <code>{uid}</code>\n"""
    await c.message.edit_text(t, parse_mode="HTML", reply_markup=back_admin())

@dp.callback_query(F.data == "admin_block")
async def adm_blk1(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""Bloklash uchun ID yuboring:""", reply_markup=back_admin())
    await state.set_state(BcastState.wait_block)

@dp.message(BcastState.wait_block)
async def adm_blk2(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        block_user(int(m.text.strip()))
        await m.answer("""✅ Bloklandi.""", reply_markup=main_kb(True))
    except: await m.answer("""❌ Xato ID.""", reply_markup=main_kb(True))
    await state.clear()

@dp.callback_query(F.data == "admin_unblock")
async def adm_ublk1(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""Qaytarish uchun ID yuboring:""", reply_markup=back_admin())
    await state.set_state(BcastState.wait_unblock)

@dp.message(BcastState.wait_unblock)
async def adm_ublk2(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        unblock_user(int(m.text.strip()))
        await m.answer("""✅ Qaytarildi.""", reply_markup=main_kb(True))
    except: await m.answer("""❌ Xato ID.""", reply_markup=main_kb(True))
    await state.clear()

@dp.callback_query(F.data == "admin_bcast")
async def adm_bc1(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""📢 Xabarni yuboring:""", reply_markup=back_admin())
    await state.set_state(BcastState.wait_msg)

@dp.message(BcastState.wait_msg)
async def adm_bc2(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(mid=m.message_id, cid=m.chat.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Yuborish", callback_data="bcast_conf")
    kb.button(text="❌ Bekor", callback_data="dl_cancel")
    await m.copy_to(m.chat.id, reply_markup=kb.as_markup())
    await state.set_state(BcastState.confirm)

@dp.callback_query(F.data == "bcast_conf")
async def adm_bc3(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    d = await state.get_data()
    await c.message.edit_text("""📨 Yuborilmoqda...""")
    u = get_all_users(True)
    s = f = 0
    for uid, _, _ in u:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=d['cid'], message_id=d['mid'])
            s += 1
            await asyncio.sleep(0.05)
        except: f += 1
    add_broadcast(c.from_user.id, "Bcast", s, f)
    await c.message.edit_text(f"""✅ Ketdi: {s}\n❌ Xato: {f}""", reply_markup=back_admin())
    await state.clear()

@dp.callback_query(F.data == "admin_clear")
async def adm_clr(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    cnt = 0
    for f in os.listdir(TEMP_DIR):
        try:
            os.remove(os.path.join(TEMP_DIR, f))
            cnt += 1
        except: pass
    await c.message.edit_text(f"""✅ {cnt} ta vaqtinchalik fayl tozalandi.""", reply_markup=back_admin())

@dp.callback_query(F.data == "back_admin")
async def bck_adm(c: CallbackQuery):
    await c.message.edit_text("""👨‍💼 <b>Admin panel</b>""", parse_mode="HTML", reply_markup=admin_kb())

async def main():
    init_db()
    for f in os.listdir(TEMP_DIR):
        try: os.remove(os.path.join(TEMP_DIR, f))
        except: pass
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Video Downloader Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
