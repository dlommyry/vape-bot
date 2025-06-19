import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS            # ← переменные из Railway

# ─────────────────────── базовая настройка ───────────────────────
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ─────────────────────── БД SQLite ───────────────────────
db  = sqlite3.connect("vape_shop.db")
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,description TEXT,quantity INTEGER);
CREATE TABLE IF NOT EXISTS cart(user_id INTEGER, product_id INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER, items TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP);
""")
db.commit()

# ─────────────────────── клавиатуры ───────────────────────
main_kb  = ReplyKeyboardMarkup(resize_keyboard=True).row("🛍 Каталог","🧺 Корзина").add("📞 Поддержка")
admin_kb = ReplyKeyboardMarkup(resize_keyboard=True).row("➕ Добавить товар","❌ Удалить товар")\
                                                   .row("✏️ Изменить остаток","📦 Остатки").add("⬅️ Назад")
switch_kb = ReplyKeyboardMarkup(resize_keyboard=True)\
            .add("🛒 Пользовательский режим","🔧 Админ-панель")

# ─────────────────────── FSM состояния ───────────────────────
class AdminMode(StatesGroup):
    user  = State()   # режим покупателя
    admin = State()   # режим администратора

class AddProduct(StatesGroup):
    name = State(); desc = State(); qty  = State()

class EditStock(StatesGroup):
    choose = State(); qty = State()

# ─────────────────────── /start + переключатель ───────────────────────
@dp.message_handler(commands="start", state="*")
async def cmd_start(m: types.Message, state: FSMContext):
    if str(m.from_user.id) in ADMINS:
        await m.answer("Выберите режим:", reply_markup=switch_kb)
        await state.finish()
    else:
        await m.answer("Добро пожаловать!", reply_markup=main_kb)
        await AdminMode.user.set()

@dp.message_handler(lambda m: m.text == "🔧 Админ-панель", state="*")
async def to_admin(m: types.Message, state: FSMContext):
    if str(m.from_user.id) not in ADMINS:
        await m.answer("Нет прав.")
        return
    await m.answer("🔧 Режим администратора.", reply_markup=admin_kb)
    await AdminMode.admin.set()

@dp.message_handler(lambda m: m.text == "🛒 Пользовательский режим", state="*")
async def to_user(m: types.Message, state: FSMContext):
    await m.answer("🛒 Режим покупателя.", reply_markup=main_kb)
    await AdminMode.user.set()

# ─────────────────────── пользовательские функции ───────────────────────
@dp.message_handler(lambda m: m.text == "📞 Поддержка", state=AdminMode.user)
async def support(m: types.Message):
    await m.answer("Для связи: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог", state=AdminMode.user)
async def catalog(m: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT id,name,quantity FROM products")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})", callback_data=f"view:{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("view:"), state=AdminMode.user)
async def view(cb: types.CallbackQuery):
    pid = int(cb.data.split(":",1)[1])
    cur.execute("SELECT name,description,quantity FROM products WHERE id=?", (pid,))
    name,desc,qty = cur.fetchone()
    kb = InlineKeyboardMarkup()
    if qty>0:
        kb.add(InlineKeyboardButton("🛒 В корзину", callback_data=f"add:{pid}"))
    await cb.message.answer(f"*{name}*\n{desc}\nОстаток: {qty}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# -------- выбор количества и добавление в корзину
class Qty(StatesGroup): waiting = State()

@dp.callback_query_handler(lambda c: c.data.startswith("add:"), state=AdminMode.user)
async def ask_qty(cb: types.CallbackQuery, state:FSMContext):
    pid = int(cb.data.split(":",1)[1])
    await state.update_data(pid=pid)
    kb = InlineKeyboardMarkup()
    for i in range(1,11):
        kb.add(InlineKeyboardButton(str(i), callback_data=f"q:{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Qty.waiting.set(); await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("q:"), state=Qty.waiting)
async def save_qty(cb: types.CallbackQuery, state:FSMContext):
    qty = int(cb.data.split(":",1)[1])
    data = await state.get_data(); pid=data['pid']
    cur.execute("INSERT INTO cart VALUES (?,?,?)",(cb.from_user.id,pid,qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# -------- корзина
@dp.message_handler(lambda m: m.text == "🧺 Корзина", state=AdminMode.user)
async def show_cart(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,cart.qty
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows:
        await m.answer("Корзина пуста."); return
    text="\n".join(f"{rid}. {n} ×{q}" for rid,n,q in rows)
    kb = InlineKeyboardMarkup()
    for rid,_,_ in rows:
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"del:{rid}"))
    kb.add(InlineKeyboardButton("❌ Очистить", callback_data="clr"),
           InlineKeyboardButton("✅ Оформить", callback_data="ok"))
    await m.answer("Корзина:\n"+text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data=="clr", state=AdminMode.user)
async def cart_clear(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("del:"), state=AdminMode.user)
async def cart_del(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data.split(':',1)[1]),)); db.commit()
    await cb.answer("Удалено"); await show_cart(cb.message)

@dp.callback_query_handler(lambda c: c.data=="ok", state=AdminMode.user)
async def checkout(cb: types.CallbackQuery):
    cur.execute("""SELECT products.name,cart.qty FROM cart
                   JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(cb.from_user.id,))
    items = cur.fetchall()
    if not items:
        await cb.answer("Пусто"); return
    line = ", ".join(f"{n}×{q}" for n,q in items)
    cur.execute("INSERT INTO orders(user_id,items) VALUES(?,?)",(cb.from_user.id,line))
    oid = cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    for adm in ADMINS:
        await bot.send_message(adm, f"🆕 Заказ #{oid}\n{line}\nОт: {cb.from_user.get_mention()}")
    await cb.message.edit_text(f"Заказ #{oid} принят!"); await cb.answer()

# ─────────────────────── АДМИН-функции ───────────────────────
# ➕ Добавить товар
@dp.message_handler(lambda m: m.text=="➕ Добавить товар" and str(m.from_user.id) in ADMINS, state=AdminMode.admin)
async def add_1(m: types.Message): await m.answer("Название?"); await AddProduct.name.set()
@dp.message_handler(state=AddProduct.name)
async def add_2(m: types.Message, state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await AddProduct.desc.set()
@dp.message_handler(state=AddProduct.desc)
async def add_3(m: types.Message, state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Количество:"); await AddProduct.qty.set()
@dp.message_handler(state=AddProduct.qty)
async def add_save(m: types.Message, state:FSMContext):
    try: qty=int(m.text)
    except: await m.answer("Число!"); return
    d=await state.get_data()
    cur.execute("INSERT INTO products(name,description,quantity) VALUES(?,?,?)",(d['name'],d['desc'],qty)); db.commit()
    await m.answer("Добавлено.",reply_markup=admin_kb); await state.finish()

# ✏️ Изменить остаток
@dp.message_handler(lambda m: m.text=="✏️ Изменить остаток" and str(m.from_user.id) in ADMINS, state=AdminMode.admin)
async def edit_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"edit:{pid}"))
    await m.answer("Выбери товар:",reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("edit:"), state=AdminMode.admin)
async def edit_qty(cb: types.CallbackQuery, state:FSMContext):
    pid=int(cb.data.split(":",1)[1]); await state.update_data(pid=pid)
    await cb.message.answer("Новое количество:"); await EditStock.qty.set(); await cb.answer()
@dp.message_handler(state=EditStock.qty)
async def edit_save(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число!"); return
    pid=(await state.get_data())['pid']
    cur.execute("UPDATE products SET quantity=? WHERE id=?", (q,pid)); db.commit()
    await m.answer("Готово.",reply_markup=admin_kb); await state.finish()

# ❌ Удалить товар
@dp.message_handler(lambda m:m.text=="❌ Удалить товар" and str(m.from_user.id) in ADMINS, state=AdminMode.admin)
async def del_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"del:{pid}"))
    await m.answer("Удалить:",reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("del:") and str(c.from_user.id) in ADMINS, state=AdminMode.admin)
async def del_exec(cb: types.CallbackQuery):
    pid=int(cb.data.split(":",1)[1])
    cur.execute("DELETE FROM products WHERE id=?", (pid,)); db.commit()
    await cb.message.answer("Удалён."); await cb.answer()

# 📦 Остатки
@dp.message_handler(lambda m:m.text=="📦 Остатки" and str(m.from_user.id) in ADMINS, state=AdminMode.admin)
async def stock(m: types.Message):
    cur.execute("SELECT name,quantity FROM products")
    txt="\n".join(f"{n}: {q}" for n,q in cur.fetchall()) or "Склад пуст."
    await m.answer(txt)

# ⬅️ Назад
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS, state=AdminMode.admin)
async def back_admin(m: types.Message):
    await m.answer("Админ-меню", reply_markup=admin_kb)

# ─────────────────────── запуск ───────────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
