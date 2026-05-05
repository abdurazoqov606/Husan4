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
    Message, CallbackQuery, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ==================== KONFIGURATSIYA ====================
BOT_TOKEN = "BOT_TOKEN"  
ADMIN_ID_STR = "ADMIN_ID"

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except:
    ADMIN_ID = 0

ADMIN_IDS = [ADMIN_ID]             

BOT_PREFIX = BOT_TOKEN.split(':')[0] if ':' in BOT_TOKEN else "unknown"
DB_FILE = f"db_ai_video_{BOT_PREFIX}.sqlite"

logging.basicConfig(level=logging.INFO)
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
    cur.execute("""SELECT SUM(generations_count) FROM users""")
    gen = cur.fetchone()[0] or 0
    conn.close()
    return {"tot": tot, "blk": blk, "gen": gen}

def inc_gen(uid):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""UPDATE users SET generations_count = generations_count + 1 WHERE user_id=?""", (uid,))
    conn.commit()
    conn.close()

def add_broadcast(aid, txt, sent, failed):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute("""INSERT INTO broadcasts (admin_id, message_text, sent_count, failed_count, created_at) VALUES (?, ?, ?, ?, ?)""", (aid, txt, sent, failed, now))
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
def main_kb(is_admin_user=False):
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text="🎬 Matndan Video (Text2Video)"))
    b.add(KeyboardButton(text="🎞 Rasmdan Video (Img2Video)"))
    b.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        b.add(KeyboardButton(text="👨‍💼 Admin panel"))
    b.adjust(1, 1, 2)
    return b.as_markup(resize_keyboard=True)

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Statistika", callback_data="admin_stats")
    b.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    b.button(text="🔇 Bloklash", callback_data="admin_block_user")
    b.button(text="🔊 Blokdan chiqarish", callback_data="admin_unblock_user")
    b.button(text="📢 Reklama yuborish", callback_data="admin_broadcast")
    b.adjust(2)
    return b.as_markup()

def aspect_ratio_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🖥 16:9 (Yotish format)", callback_data="aspect_16:9")
    b.button(text="📱 9:16 (Tik format)", callback_data="aspect_9:16")
    b.button(text="❌ Bekor qilish", callback_data="cancel_action")
    b.adjust(2, 1)
    return b.as_markup()

def cancel_inline():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Bekor qilish", callback_data="cancel_action")
    return b.as_markup()

def back_to_admin():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="back_to_admin")
    return b.as_markup()

# ==================== API FUNKSIYALAR ====================
async def generate_video_api(prompt, aspect_ratio="16:9", image_url=None):
    url = "https://vetrex.site/v1/videos/generations"
    payload = {"prompt": prompt, "model": "veo-3.1"}
    
    if image_url:
        payload["images"] = [image_url]
    else:
        payload["aspect_ratio"] = aspect_ratio
        
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    task_id = data.get('task_id')
                    if task_id:
                        check_url = data.get('check_url', f"https://vetrex.site/v1/videos/results/{task_id}")
                        for _ in range(30):
                            await asyncio.sleep(10)
                            async with session.get(check_url, timeout=10) as status_resp:
                                if status_resp.status == 200:
                                    status_data = await status_resp.json()
                                    if status_data.get('status') == 'completed':
                                        return status_data.get('url'), None
                                    elif status_data.get('status') == 'failed':
                                        return None, status_data.get('error', 'API xatosi')
        except:
            pass
            
        try:
            seedance_url = "https://zecora0.serv00.net/ai/Seedance.php"
            data = {"text": prompt, "model": "Seedance"}
            if image_url: data["images"] = image_url
            async with session.post(seedance_url, data=data, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success') and data.get('url'):
                        return data.get('url'), None
        except:
            pass
            
    return None, "Server bilan ulanishda xatolik yoki vaqt tugadi."

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    add_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    if is_blocked(m.from_user.id):
        await m.answer("""🚫 Siz bloklangansiz. Admin bilan bog'laning.""")
        return
    await m.answer(f"""Assalomu alaykum, <b>{m.from_user.first_name}</b>! 👋\n\n🎥 Men <b>Veo 3.1 (Cinematic AI Motion)</b> orqali 4K sifatdagi videolarni matndan yoki rasmdan harakatlantirib beruvchi botman.\n\nKerakli bo'limni tanlang 👇\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML", reply_markup=main_kb(is_admin(m.from_user.id)))

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("""🤖 <b>Bot imkoniyatlari (Veo 3.1 API):</b>\n\n1️⃣ <b>Matndan Video:</b> O'z ssenariyingizni (prompt) yozasiz, bot 8 soniyalik realistik video yasab beradi.\n2️⃣ <b>Rasmdan Video:</b> O'zingiz xohlagan rasmni yuborasiz, bot rasmdagi obyektlarni harakatga keltirib, jonlantirib beradi.\n\n<i>Eslatma: Promptlarni 🇬🇧 Ingliz tilida yozing!</i>\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML")

@dp.callback_query(F.data == "cancel_action")
async def cancel_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("""❌ Amaliyot bekor qilindi.""")
    await c.answer()

# ----------------- 1. TEXT TO VIDEO -----------------
@dp.message(F.text == "🎬 Matndan Video (Text2Video)")
async def t2v_start(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id): return
    await m.answer("""🎬 <b>Matndan Video Yaratish</b>\n\nQanday video yaratmoqchisiz? Ssenariyni yozing:""", parse_mode="HTML", reply_markup=cancel_inline())
    await state.set_state(VideoStates.waiting_t2v_prompt)

@dp.message(VideoStates.waiting_t2v_prompt)
async def t2v_prompt_received(m: Message, state: FSMContext):
    if not m.text: return
    await state.update_data(prompt=m.text)
    await m.answer("""🖥 <b>Videonining o'lchamini (Aspect Ratio) tanlang:</b>""", parse_mode="HTML", reply_markup=aspect_ratio_kb())
    await state.set_state(None)

@dp.callback_query(F.data.startswith("aspect_"))
async def t2v_process(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    prompt = data.get("prompt")
    if not prompt:
        await c.message.edit_text("""❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.""")
        return
    aspect_ratio = c.data.split("_")[1]
    msg = await c.message.edit_text("""⏳ <i>Video yaratilmoqda (1-3 daqiqa)...</i>""", parse_mode="HTML")
    await state.clear()
    video_url, error = await generate_video_api(prompt, aspect_ratio=aspect_ratio)
    if video_url:
        cap = f"""🎬 <b>Prompt:</b> {prompt[:500]}\n📐 <b>Format:</b> {aspect_ratio} (Veo 3.1)\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"""
        try:
            await c.message.answer_video(video_url, caption=cap, parse_mode="HTML")
            inc_gen(c.from_user.id)
        except:
            await c.message.answer(f"""❌ Yuklash xatosi: {video_url}""")
    else:
        await c.message.answer(f"""❌ Xatolik: {error}""")
    await msg.delete()

# ----------------- 2. IMAGE TO VIDEO -----------------
@dp.message(F.text == "🎞 Rasmdan Video (Img2Video)")
async def i2v_start(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id): return
    await m.answer("""🎞 <b>Rasmni Harakatga Keltirish</b>\n\nJonlantirmoqchi bo'lgan <b>rasmingizni</b> yuboring:""", parse_mode="HTML", reply_markup=cancel_inline())
    await state.set_state(VideoStates.waiting_i2v_photo)

@dp.message(VideoStates.waiting_i2v_photo, F.photo)
async def i2v_photo(m: Message, state: FSMContext):
    f = await bot.get_file(m.photo[-1].file_id)
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    await state.update_data(img_url=url)
    await m.answer("""✅ Rasm qabul qilindi!\n\nEndi nimalar harakatlanishi kerakligini (prompt) yozing:""", parse_mode="HTML", reply_markup=cancel_inline())
    await state.set_state(VideoStates.waiting_i2v_prompt)

@dp.message(VideoStates.waiting_i2v_prompt)
async def i2v_process(m: Message, state: FSMContext):
    if not m.text: return
    data = await state.get_data()
    msg = await m.answer("""⏳ <i>Rasm videoga aylantirilmoqda...</i>""", parse_mode="HTML")
    await state.clear()
    video_url, error = await generate_video_api(m.text, image_url=data.get("img_url"))
    if video_url:
        cap = f"""🎞 <b>Harakatlantirildi:</b> {m.text[:500]}\n✨ <b>Model:</b> Veo 3.1\n\n⚙️ <b>Yaratuvchi:</b> @vsf911"""
        try:
            await m.answer_video(video_url, caption=cap, parse_mode="HTML")
            inc_gen(m.from_user.id)
        except:
            await m.answer(f"""❌ Yuklash xatosi: {video_url}""")
    else:
        await m.answer(f"""❌ Xato: {error}""")
    await msg.delete()

# ----------------- ADMIN PANEL HANDLERLARI -----------------
@dp.message(F.text == "👨‍💼 Admin panel")
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer("""👨‍💼 <b>Admin panel</b>\n\n⚙️ Yaratuvchi: @vsf911""", parse_mode="HTML", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    st = get_stats()
    await c.message.edit_text(f"""📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {st['tot']}\n🔇 Bloklanganlar: {st['blk']}\n🎬 Videolar: {st['gen']}\n\n⚙️ <b>Yaratuvchi:</b> @vsf911""", parse_mode="HTML", reply_markup=back_to_admin())

@dp.callback_query(F.data == "admin_users")
async def admin_users(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    users = get_all_users(only_active=False)
    if not users:
        await c.message.edit_text("""Foydalanuvchilar yo'q.""", reply_markup=back_to_admin())
        return
    text = """👥 <b>Foydalanuvchilar:</b>\n\n"""
    for uid, un, fn in users[:20]:
        text += f"""• {fn} (@{un}) - <code>{uid}</code>\n"""
    await c.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_admin())

@dp.callback_query(F.data == "admin_block_user")
async def admin_block_prmpt(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""Bloklash uchun ID yuboring:""", reply_markup=back_to_admin())
    await state.set_state(VideoStates.waiting_block_id)

@dp.message(VideoStates.waiting_block_id)
async def process_admin_block(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        block_user(int(m.text.strip()))
        await m.answer("""✅ Bloklandi.""", reply_markup=main_kb(True))
    except:
        await m.answer("""❌ Noto'g'ri ID.""", reply_markup=main_kb(True))
    await state.clear()

@dp.callback_query(F.data == "admin_unblock_user")
async def admin_unblock_prmpt(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""Qaytarish uchun ID yuboring:""", reply_markup=back_to_admin())
    await state.set_state(VideoStates.waiting_unblock_id)

@dp.message(VideoStates.waiting_unblock_id)
async def process_admin_unblock(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    try:
        unblock_user(int(m.text.strip()))
        await m.answer("""✅ Qaytarildi.""", reply_markup=main_kb(True))
    except:
        await m.answer("""❌ Noto'g'ri ID.""", reply_markup=main_kb(True))
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_bcast_st(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("""📢 Xabaringizni yozing:""", reply_markup=back_to_admin())
    await state.set_state(VideoStates.waiting_broadcast)

@dp.message(VideoStates.waiting_broadcast)
async def admin_bcast_prev(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(mid=m.message_id, cid=m.chat.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Yuborish", callback_data="broadcast_confirm")
    kb.button(text="❌ Bekor", callback_data="cancel_action")
    await m.copy_to(m.chat.id, reply_markup=kb.as_markup())
    await m.answer("""Yuborishni tasdiqlaysizmi?""")
    await state.set_state(VideoStates.confirm_broadcast)

@dp.callback_query(F.data == "broadcast_confirm")
async def admin_bcast_send(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    d = await state.get_data()
    await c.message.edit_text("""📨 Yuborilmoqda...""")
    users = get_all_users(True)
    sent = failed = 0
    for uid, _, _ in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=d['cid'], message_id=d['mid'])
            sent += 1
            await asyncio.sleep(0.05) 
        except:
            failed += 1
    add_broadcast(c.from_user.id, "Bcast", sent, failed)
    await c.message.edit_text(f"""✅ Ketdi: {sent}, ❌ Xato: {failed}""", reply_markup=back_to_admin())
    await state.clear()

@dp.callback_query(F.data == "back_to_admin")
async def bck_adm(c: CallbackQuery):
    await c.message.edit_text("""👨‍💼 <b>Admin panel</b>""", parse_mode="HTML", reply_markup=admin_kb())

async def main():
    init_db()
    # Webhookni tozalash kafolati
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Veo 3.1 AI Video Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
