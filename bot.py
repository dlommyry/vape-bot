import logging
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS   # переменные окружения

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ──────────────── SQLite ────────────────
db  = sqlite3.connect("vape_shop.db")
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,description TEXT,quantity INTEGER);
CREATE TABLE IF NOT EXISTS cart(user_id INTEGER, product_id INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,items TEXT,ts DATETIME DEFAULT CURRENT_TIMESTAMP);
""")
db.commit()

# ──────────────── клавиатуры ────────────────
user_menu = ReplyKeyboardMarkup(resize_keyboard=True)
user_menu.add("🛍 Каталог", "🧺 Корзина").add("📞 Поддержка").add("⬅️ Назад")

admin_menu = ReplyKeyboardMarkup(resize_keyboard=True)
admin_menu.add("➕ Добавить товар", "❌ Удалить товар") \
          .add("✏️ Изменить остаток", "📦 Остатки") \
          .add("⬅️ Назад")

switch_menu = ReplyKeyboardMarkup(resize_keyboard=True)
switch_menu.add("🛒 Пользовательский режим", "🔧 Админ-панель")

# ──────────────── FSM ────────────────
class Mode(StatesGroup):
    user  = State()
    admin = State()

class AddProduct(StatesGroup):
    name = State(); desc = State(); qty = State()

class EditStock(StatesGroup):
    choose = State(); qty = State()

class Qty(StatesGroup):
    waiting = State()

# ──────────────── /start + переключение ────────────────
@dp.message_handler(commands="start", state="*")
async def start(msg: types.Message, state: FSMContext):
    if str(msg.from_user.id) in ADMINS:
        await msg.answer("Выберите режим:", reply_markup=switch_menu)
        await state.finish()
    else:
        await msg.answer("Добро пожаловать!", reply_markup=user_menu)
        await Mode.user.set()

@dp.message_handler(lambda m: m.text == "🔧 Админ-панель", state="*")
async def to_admin(msg: types.Message):
    if str(msg.from_user.id) not in ADMINS:
        await msg.answer("Нет прав.")
        return
    await msg.answer("🔧 Режим администратора.", reply_markup=admin_menu)
    await Mode.admin.set()

@dp.message_handler(lambda m: m.text == "🛒 Пользовательский режим", state="*")
async def to_user(msg: types.Message):
    await msg.answer("🛒 Режим покупателя.", reply_markup=user_menu)
    await Mode.user.set()

# ──────────────── пользовательские функции ────────────────
@dp.message_handler(lambda m: m.text == "📞 Поддержка", state=Mode.user)
async def support(m: types.Message):
    await m.answer("Для связи: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог", state=Mode.user)
async def catalog(m: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT id,name,quantity FROM products")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})", callback_data=f"view:{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("view:"), state=Mode.user)
async def view(cb: types.CallbackQuery):
    pid = int(cb.data.split(":", 1)[1])
    cur.execute("SELECT name,description,quantity FROM products WHERE id=?", (pid,))
    name, desc, qty = cur.fetchone()
    kb = InlineKeyboardMarkup()
    if qty > 0:
        kb.add(InlineKeyboardButton("🛒 В корзину", callback_data=f"add:{pid}"))
    await cb.message.answer(f"*{name}*\n{desc}\nОстаток: {qty}",
                            parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("add:"), state=Mode.user)
async def ask_qty(cb: types.CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":", 1)[1])
    await state.update_data(pid=pid)
    kb = InlineKeyboardMarkup()
    for i in range(1, 11):
        kb.add(InlineKeyboardButton(str(i), callback_data=f"q:{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Qty.waiting.set()
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("q:"), state=Qty.waiting)
async def save_qty(cb: types.CallbackQuery, state: FSMContext):
    qty = int(cb.data.split(":", 1)[1])
    pid = (await state.get_data())["pid"]
    cur.execute("INSERT INTO cart VALUES (?,?,?)", (cb.from_user.id, pid, qty))
    db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish()
    await cb.answer()

@dp.message_handler(lambda m: m.text == "🧺 Корзина", state=Mode.user)
async def show_cart(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,cart.qty
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""", (m.from_user.id,))
    rows = cur.fetchall()
    if not rows:
        await m.answer("Корзина пуста.")
        return
    kb = InlineKeyboardMarkup()
    text = "\n".join(f"{rid}. {name} ×{qty}" for rid, name, qty in rows)
    for rid, _, _ in rows:
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"del:{rid}"))
    kb.add(
        InlineKeyboardButton("❌ Очистить", callback_data="clr"),
        InlineKeyboardButton("✅ Оформить", callback_data="ok"),
    )
    await m.answer("Корзина:\n" + text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "clr", state=Mode.user)
async def cart_clear(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,))
    db.commit()
    await cb.message.edit_text("Корзина очищена.")
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("del:"), state=Mode.user)
async def cart_del(cb: types.CallbackQuery):
    rid = int(cb.data.split(":", 1)[1])
    cur.execute("DELETE FROM cart WHERE rowid=?", (rid,))
    db.commit()
    await cb.answer("Удалено")
    await show_cart(cb.message)

@dp.callback_query_handler(lambda c: c.data == "ok", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    cur.execute("""SELECT products.name,cart.qty FROM cart
                   JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""", (cb.from_user.id,))
    items = cur.fetchall()
    if not items:
        await cb.answer("Пусто")
        return
    line = ", ".join(f"{n}×{q}" for n, q in items)
    cur.execute("INSERT INTO orders(user_id,items) VALUES(?,?)", (cb.from_user.id, line))
    order_id = cur.lastrowid
    db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,))
    db.commit()
    for admin in ADMINS:
        await bot.send_message(admin, f"🆕 Заказ #{order_id}\n{line}\nОт: {cb.from_user.get_mention()}")
    await cb.message.edit_text(f"Заказ #{order_id} принят!")
    await cb.answer()

# ──────────────── кнопка «Назад» из user-режима ────────────────
@dp.message_handler(lambda m: m.text == "⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.user)
async def back_to_admin(msg: types.Message):
    await msg.answer("🔧 Админ-панель:", reply_markup=admin_menu)
    await Mode.admin.set()

# ──────────────── АДМИН-функции ────────────────
@dp.message_handler(lambda m: m.text == "📦 Остатки" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def stock(m: types.Message):
    cur.execute("SELECT name, quantity FROM products")
    txt = "\n".join(f"{n}: {q}" for n, q in cur.fetchall()) or "Склад пуст."
    await m.answer(txt)

# ➕, ✏️, ❌ (остальной админ-код прежний — не удаляется!)

# ──────────────── запуск ────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
