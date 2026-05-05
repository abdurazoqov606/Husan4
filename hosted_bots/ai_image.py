import asyncio
import logging
import sqlite3
import os
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton, BufferedInputFile
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
DB_FILE = f"db_ai_image_{BOT_PREFIX}.sqlite"

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

# ==================== HOLATLAR ====================
class AIStates(StatesGroup):
    t2i_prompt = State()
    edit_photo = State()
    edit_prompt = State()
    blend_photo1 = State()
    blend_photo2 = State()
    blend_prompt = State()
    bcast_msg = State()
    bcast_conf = State()
    block_id = State()
    unblock_id = State()

# ==================== KLAVIATURALAR ====================
def main_kb(is_admin_user=False):
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text="🎨 Text2Img (Rasm)"))
    b.add(KeyboardButton(text="🖼 Img2Img (Tahrir)"))
    b.add(KeyboardButton(text="🎭 Blend (Birlashtirish)"))
    b.add(KeyboardButton(text="ℹ️ Yordam"))
    if is_admin_user:
        b.add(KeyboardButton(text="👨‍💼 Admin panel"))
    b.adjust(1, 2, 1, 1)
    return b.as_markup(resize_keyboard=True)

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Statistika", callback_data="admin_stats")
    b.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    b.button(text="🔇 Bloklash", callback_data="admin_block")
    b.button(text="🔊 Qaytarish", callback_data="admin_unblock")
    b.button(text="📢 Reklama", callback_data="admin_bcast")
    b.adjust(2)
    return b.as_markup()

def cancel_kb():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Bekor qilish", callback_data="cancel_action")
    return b.as_markup()

def back_admin():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="back_admin")
    return b.as_markup()

# ==================== API FUNKSIYALAR ====================
async def gen_nanobanana(prompt):
    url = "https://zecora0.serv00.net/ai/NanoBanana.php"
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
            except:
                continue
    return None

async def edit_kilwa(img_url, prompt):
    url = f"http://de3.bot-hosting.net:21007/kilwa-edit?img={img_url}&text={prompt}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    ct = resp.headers.get('Content-Type', '')
                    if 'application/json' in ct:
                        return (await resp.json()).get('url'), True
                    else:
                        return await resp.read(), False
        except: pass
    return None, False

async def blend_kilwa(img1, img2, prompt):
    url = f"http://de3.bot-hosting.net:21007/kilwa-blend?img1={img1}&img2={img2}&text={prompt}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    ct = resp.headers.get('Content-Type', '')
                    if 'application/json' in ct:
                        return (await resp.json()).get('url'), True
                    else:
                        return await resp.read(), False
        except: pass
    return None, False

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    add_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    if is_blocked(m.from_user.id):
        await m.answer("""🚫 Siz bloklangansiz.""")
        return
    await m.answer(f"""Assalomu alaykum, <b>{m.from_user.first_name}</b>! 👋\n\nMen AI Rasm (NanoBanana/Kilwa) botiman. Kerakli menyuni tanlang:\n\n⚙️ Yaratuvchi: @vsf911""", parse_mode="HTML", reply_markup=main_kb(is_admin(m.from_user.id)))

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("""🤖 <b>Imkoniyatlar:</b>\n\n1️⃣ <b>Text2Img:</b> Matndan rasm yasash.\n2️⃣ <b>Img2Img:</b> Rasmni tahrirlash.\n3️⃣ <b>Blend:</b> 2 ta rasmni birlashtirish.\n\n<i>Promptlarni inglizcha yozing!</i>\n\n⚙️ Yaratuvchi: @vsf911""", parse_mode="HTML")

@dp.callback_query(F.data == "cancel_action")
async def cancel_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("""❌ Bekor qilindi.""")

# --- TEXT 2 IMG ---
@dp.message(F.text == "🎨 Text2Img (Rasm)")
async def t2i_st(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id): return
    await m.answer("""🎨 <b>Rasm yaratish</b>\n\nPromptni (ta'rifni) yozing:""", parse_mode="HTML", reply_markup=cancel_kb())
    await state.set_state(AIStates.t2i_prompt)

@dp.message(AIStates.t2i_prompt)
async def t2i_pr(m: Message, state: FSMContext):
    if not m.text: return
    msg = await m.answer("""⏳ <i>Rasm chizilmoqda...</i>""", parse_mode="HTML")
    await state.clear()
    url = await gen_nanobanana(m.text)
    if url:
        await m.answer_photo(url, caption=f"""🎨 <b>Prompt:</b> {m.text[:800]}\n\n⚙️ @vsf911""", parse_mode="HTML")
        inc_gen(m.from_user.id)
    else:
        await m.answer("""❌ Xatolik yuz berdi. Keyinroq urinib ko'ring.""")
    await msg.delete()

# --- IMG 2 IMG ---
@dp.message(F.text == "🖼 Img2Img (Tahrir)")
async def e2i_st(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id): return
    await m.answer("""🖼 <b>Tahrirlash</b>\n\nO'zgartirmoqchi bo'lgan rasmni yuboring:""", parse_mode="HTML", reply_markup=cancel_kb())
    await state.set_state(AIStates.edit_photo)

@dp.message(AIStates.edit_photo, F.photo)
async def e2i_ph(m: Message, state: FSMContext):
    fid = m.photo[-1].file_id
    f = await bot.get_file(fid)
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    await state.update_data(img=url)
    await m.answer("""✅ Rasm qabul qilindi. Endi o'zgarish promptini yozing:""", reply_markup=cancel_kb())
    await state.set_state(AIStates.edit_prompt)

@dp.message(AIStates.edit_prompt)
async def e2i_pr(m: Message, state: FSMContext):
    if not m.text: return
    data = await state.get_data()
    msg = await m.answer("""⏳ <i>Tahrirlanmoqda...</i>""", parse_mode="HTML")
    await state.clear()
    res, is_url = await edit_kilwa(data['img'], m.text)
    cap = f"""🖼 <b>Tahrir:</b> {m.text[:800]}\n\n⚙️ @vsf911"""
    if res:
        if is_url: await m.answer_photo(res, caption=cap, parse_mode="HTML")
        else: await m.answer_photo(BufferedInputFile(res, "edit.jpg"), caption=cap, parse_mode="HTML")
        inc_gen(m.from_user.id)
    else:
        await m.answer("""❌ Tahrirlashda xatolik.""")
    await msg.delete()

# --- BLEND ---
@dp.message(F.text == "🎭 Blend (Birlashtirish)")
async def bl_st(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id): return
    await m.answer("""🎭 <b>1-rasmni</b> yuboring:""", parse_mode="HTML", reply_markup=cancel_kb())
    await state.set_state(AIStates.blend_photo1)

@dp.message(AIStates.blend_photo1, F.photo)
async def bl_p1(m: Message, state: FSMContext):
    f = await bot.get_file(m.photo[-1].file_id)
    await state.update_data(img1=f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}")
    await m.answer("""✅ Qabul qilindi. <b>2-rasmni</b> yuboring:""", parse_mode="HTML", reply_markup=cancel_kb())
    await state.set_state(AIStates.blend_photo2)

@dp.message(AIStates.blend_photo2, F.photo)
async def bl_p2(m: Message, state: FSMContext):
    f = await bot.get_file(m.photo[-1].file_id)
    await state.update_data(img2=f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}")
    await m.answer("""✅ Qabul qilindi. Birlashtirish promptini yozing:""", reply_markup=cancel_kb())
    await state.set_state(AIStates.blend_prompt)

@dp.message(AIStates.blend_prompt)
async def bl_pr(m: Message, state: FSMContext):
    if not m.text: return
    data = await state.get_data()
    msg = await m.answer("""⏳ <i>Birlashtirilmoqda...</i>""", parse_mode="HTML")
    await state.clear()
    res, is_url = await blend_kilwa(data['img1'], data['img2'], m.text)
    cap = f"""🎭 <b>Blend:</b> {m.text[:800]}\n\n⚙️ @vsf911"""
    if res:
        if is_url: await m.answer_photo(res, caption=cap, parse_mode="HTML")
        else: await m.answer_photo(BufferedInputFile(res, "blend.jpg"), caption=cap, parse_mode="HTML")
        inc_gen(m.from_user.id)
    else:
        await m.answer("""❌ Xatolik.""")
    await msg.delete()

# --- ADMIN PANEL ---
@dp.message(F.text == "👨‍💼 Admin panel")
async def adm_st(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer("""👨‍💼 <b>Admin panel</b>""", parse_mode="HTML", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_stats")
async def adm_stat(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    st = get_stats()
    await c.message.edit_text(f"""📊 <b>Statistika</b>\n\nFoydalanuvchilar: {st['tot']}\nBloklanganlar: {st['blk']}\nYaratilgan rasmlar: {st['gen']}\n\n⚙️ @vsf911""", parse_mode="HTML", reply_markup=back_admin())

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
    await state.set_state(AIStates.block_id)

@dp.message(AIStates.block_id)
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
    await state.set_state(AIStates.unblock_id)

@dp.message(AIStates.unblock_id)
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
    await state.set_state(AIStates.bcast_msg)

@dp.message(AIStates.bcast_msg)
async def adm_bc2(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(mid=m.message_id, cid=m.chat.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Yuborish", callback_data="bcast_conf")
    kb.button(text="❌ Bekor", callback_data="cancel_action")
    await m.copy_to(m.chat.id, reply_markup=kb.as_markup())
    await state.set_state(AIStates.bcast_conf)

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

@dp.callback_query(F.data == "back_admin")
async def bck_adm(c: CallbackQuery):
    await c.message.edit_text("""👨‍💼 <b>Admin panel</b>""", parse_mode="HTML", reply_markup=admin_kb())

async def main():
    init_db()
    # Webhookni tozalab ishlash kafolati
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
