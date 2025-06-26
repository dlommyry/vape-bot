"""
Plumbus Shop v2 — категории, склад, корзина, заказы
Категории:
    one_time  – «Одноразовые системы»
    pod       – «Многоразовые системы»
    juice     – «Жидкости»
    other     – «Разное»
"""

import logging, sqlite3, pathlib, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardButton, InlineKeyboardMarkup)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS

# ───────────────── база
DB = "/data/vape_shop.db"                          # используйте том Railway
def ensure_db():
    first = not pathlib.Path(DB).exists()
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY,
        name TEXT, description TEXT, category TEXT DEFAULT 'other');
    CREATE TABLE IF NOT EXISTS flavors  (id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER, flavor TEXT, qty INTEGER);
    CREATE TABLE IF NOT EXISTS cart     (rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, flavor_id INTEGER, qty INTEGER);
    CREATE TABLE IF NOT EXISTS waitlist (user_id INTEGER, flavor_id INTEGER);
    CREATE TABLE IF NOT EXISTS orders   (id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, items TEXT, ts TEXT, status TEXT);
    """); con.commit(); con.close()
    if first: logging.info("Создана новая база %s", DB)
ensure_db()
db  = sqlite3.connect(DB)
cur = db.cursor()

# ───────────────── бот
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ---------- категории
CATS = {
    "one_time": "Одноразовые системы",
    "pod":      "Многоразовые системы",
    "juice":    "Жидкости",
    "other":    "Разное"
}

# ---------- клавиатуры
def user_kb(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог", "🧺 Корзина")
    kb.row("📞 Поддержка", "📜 Мои заказы")
    if is_admin: kb.add("🔄 Сменить режим")
    return kb

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить", "❌ Удалить")
admin_kb.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
admin_kb.add("🔄 Сменить режим")

# ---------- FSM
class Mode(StatesGroup):  user = State(); admin = State()
class Add (StatesGroup):
    name=State(); desc=State(); cat=State(); cnt=State(); flavor=State(); qty=State()
class Edit(StatesGroup):  fid=State(); qty=State()
class Buy (StatesGroup):  fid=State(); maxq=State(); qty=State()

# ───────────────────────────── /start
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    await m.answer("Добро пожаловать!", reply_markup=user_kb(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

# ---------- смена режима
@dp.message_handler(lambda m:m.text=="🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch(m: types.Message, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb(True)); await Mode.user.set()

# ───────────────────────────── КЛИЕНТ
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m): await m.answer("Связь: @PlumbusSupport")

# --- каталог: шаг 1 — категории
@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def cat_root(m):
    kb = InlineKeyboardMarkup()
    for code, title in CATS.items():
        kb.add(InlineKeyboardButton(title, callback_data=f"C{code}"))
    await m.answer("Категории:", reply_markup=kb)

# --- каталог: список товаров категории
@dp.callback_query_handler(lambda c:c.data.startswith("C"), state=Mode.user)
async def cat_list(cb: types.CallbackQuery):
    code = cb.data[1:]
    kb   = InlineKeyboardMarkup()
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p JOIN flavors f ON f.product_id=p.id
                   WHERE p.category=? GROUP BY p.id""",(code,))
    rows = cur.fetchall()
    if not rows:
        await cb.message.edit_text("Пусто."); await cb.answer(); return
    for pid,n,q in rows:
        kb.add(InlineKeyboardButton(f"{n} ({q})", callback_data=f"P{pid}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="BACK"))
    await cb.message.edit_text(CATS[code]+":", reply_markup=kb); await cb.answer()

@dp.callback_query_handler(lambda c:c.data=="BACK", state=Mode.user)
async def back_to_root(cb):
    await cat_root(cb.message); await cb.answer()

# --- карточка товара
@dp.callback_query_handler(lambda c:c.data.startswith("P"), state=Mode.user)
async def show_prod(cb,state:FSMContext):
    pid=int(cb.data[1:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    kb=InlineKeyboardMarkup()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    for fid,fl,q in cur.fetchall():
        label=f"{fl} ({q})" if fl!="default" else f"Остаток ({q})"
        kb.add(InlineKeyboardButton(label, callback_data=("F" if q>0 else "W")+str(fid)))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="BACK_TO_CAT"))
    await state.update_data(pid=pid)     # чтобы знать категорию при Back
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c:c.data=="BACK_TO_CAT", state=Mode.user)
async def back_to_cat(cb,state:FSMContext):
    pid=(await state.get_data())['pid']
    cur.execute("SELECT category FROM products WHERE id=?", (pid,)); code=cur.fetchone()[0]
    await cat_list(types.CallbackQuery(id=cb.id, from_user=cb.from_user,
                                       message=cb.message, data="C"+code));  # костыльно пересоздаём
    await cb.answer()

# --- выбор вкуса
@dp.callback_query_handler(lambda c:c.data.startswith("F"), state=Mode.user)
async def ask_qty(cb,state:FSMContext):
    fid=int(cb.data[1:])
    cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,)); maxq=cur.fetchone()[0]
    await state.update_data(fid=fid,maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1, min(maxq,10)+1):
        kb.add(InlineKeyboardButton(str(i),callback_data=f"Q{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("Q"), state=Buy.qty)
async def add_cart(cb,state:FSMContext):
    qty=int(cb.data[1:]); d=await state.get_data()
    if qty>d['maxq']:
        await cb.answer("Столько нет на складе!", show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (cb.from_user.id,d['fid'],qty)); db.commit()
    await cb.message.answer("Добавлено ✅"); await state.finish(); await cb.answer()

# --- wait-лист
@dp.callback_query_handler(lambda c:c.data.startswith("W"), state=Mode.user)
async def wait(cb): cur.execute("INSERT INTO waitlist VALUES(?,?)",(cb.from_user.id,int(cb.data[1:]))); db.commit(); await cb.answer("Сообщу, когда появится")

# ---------- корзина / чек-аут (без изменений)
# ... код корзины и оформления заказа остаётся таким же, как в прошлой версии ...

# ───────────────────────────── АДМИН
@dp.message_handler(lambda m:m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_start(m): await m.answer("Название:"); await Add.name.set()

@dp.message_handler(state=Add.name)
async def add_desc(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def ask_cat(m,state:FSMContext):
    kb=InlineKeyboardMarkup()
    for code,title in CATS.items():
        kb.add(InlineKeyboardButton(title,callback_data=f"K{code}"))
    await state.update_data(desc=m.text)
    await m.answer("Выбери категорию:", reply_markup=kb); await Add.cat.set()

@dp.callback_query_handler(lambda c:c.data.startswith("K"), state=Add.cat)
async def add_cnt(cb,state:FSMContext):
    await state.update_data(cat=cb.data[1:])
    await cb.message.answer("Сколько вкусов? (0 — без вкуса)"); await Add.cnt.set(); await cb.answer()

# ... далее блок Add.cnt / Add.flavor / Add.qty / finish_add остаётся прежним,
# но при вставке в products добавляем category=state['cat'] ...

# ◄==== Остальной код (остаток, склад, delete, заказы) НЕ меняется.
#      просто перенесите его из предыдущей рабочей версии.

# ────────── запуск
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
