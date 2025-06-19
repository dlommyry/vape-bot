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
from config import BOT_TOKEN, ADMINS  # переменные окружения

# ──────────── базовая настройка ────────────
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ──────────── БД SQLite ────────────
conn = sqlite3.connect("vape_shop.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT,
    quantity INTEGER,
    category TEXT
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    product_name TEXT
)""")
conn.commit()

# ──────────── клавиатуры ────────────
main_kb = ReplyKeyboardMarkup(resize_keyboard=True)
main_kb.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
main_kb.add(KeyboardButton("📞 Поддержка"))

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.add("➕ Добавить товар", "❌ Удалить товар")
admin_kb.add("✏️ Изменить остаток", "📦 Остатки")
admin_kb.add("⬅️ Назад")

# ──────────── FSM-классы ────────────
class AddProduct(StatesGroup):
    name = State()
    desc = State()
    qty = State()

class EditStock(StatesGroup):
    choose = State()
    qty = State()

class DeleteProduct(StatesGroup):
    choose = State()

# ──────────── Хэндлеры пользователя ────────────
user_cart = {}

@dp.message_handler(commands="start")
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) in ADMINS:
        await msg.answer("Админ-панель открыта:", reply_markup=admin_kb)
    else:
        await msg.answer("Добро пожаловать в Plumbus Shop!", reply_markup=main_kb)

@dp.message_handler(lambda m: m.text == "📞 Поддержка")
async def support(msg: types.Message):
    await msg.answer("Для связи с поддержкой: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог")
async def catalog(msg: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT name, quantity FROM products")
    for name, qty in cur.fetchall():
        kb.add(InlineKeyboardButton(
            f"{name} ({'✅' if qty > 0 else '❌'})", callback_data=f"view:{name}"))
    await msg.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("view:"))
async def view(cb: types.CallbackQuery):
    name = cb.data.split("view:", 1)[1]
    cur.execute("SELECT description, quantity FROM products WHERE name=?", (name,))
    desc, qty = cur.fetchone()
    kb = InlineKeyboardMarkup()
    if qty > 0:
        kb.add(InlineKeyboardButton("🛒 В корзину", callback_data=f"buy:{name}"))
    else:
        kb.add(InlineKeyboardButton("🔔 Уведомить", callback_data=f"wait:{name}"))
    await cb.message.answer(f"📝 *{name}*\n{desc}\nОстаток: {qty}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("buy:"))
async def buy(cb: types.CallbackQuery):
    name = cb.data.split("buy:", 1)[1]
    uid = cb.from_user.id
    user_cart.setdefault(uid, []).append(name)
    await cb.answer("Добавлено!")

@dp.callback_query_handler(lambda c: c.data.startswith("wait:"))
async def wait(cb: types.CallbackQuery):
    name = cb.data.split("wait:", 1)[1]
    cur.execute("INSERT INTO waitlist VALUES (?,?)", (cb.from_user.id, name))
    conn.commit()
    await cb.answer("Сообщу, как появится!")

@dp.message_handler(lambda m: m.text == "🧺 Корзина")
async def cart(msg: types.Message):
    items = user_cart.get(msg.from_user.id, [])
    if not items:
        await msg.answer("Корзина пуста.")
        return
    await msg.answer("Ваша корзина:\n• " + "\n• ".join(items))

# ──────────── Админ: добавить товар ────────────
@dp.message_handler(lambda m: m.text == "➕ Добавить товар" and str(m.from_user.id) in ADMINS)
async def add_start(msg: types.Message):
    await msg.answer("Название товара?")
    await AddProduct.name.set()

@dp.message_handler(state=AddProduct.name)
async def add_desc(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("Описание:")
    await AddProduct.desc.set()

@dp.message_handler(state=AddProduct.desc)
async def add_qty(msg: types.Message, state: FSMContext):
    await state.update_data(desc=msg.text)
    await msg.answer("Количество (число):")
    await AddProduct.qty.set()

@dp.message_handler(state=AddProduct.qty)
async def add_save(msg: types.Message, state: FSMContext):
    try:
        qty = int(msg.text)
    except ValueError:
        await msg.answer("Нужно число. Введите снова:")
        return
    data = await state.get_data()
    cur.execute("INSERT INTO products(name,description,quantity,category) VALUES(?,?,?,?)",
                (data['name'], data['desc'], qty, "default"))
    conn.commit()
    await msg.answer("Товар добавлен!", reply_markup=admin_kb)
    await state.finish()

# ──────────── Админ: изменить остаток ────────────
@dp.message_handler(lambda m: m.text == "✏️ Изменить остаток" and str(m.from_user.id) in ADMINS)
async def edit_choose(msg: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT name FROM products")
    for (name,) in cur.fetchall():
        kb.add(InlineKeyboardButton(name, callback_data=f"edit:{name}"))
    await msg.answer("Выберите товар:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edit:"))
async def edit_qty_prompt(cb: types.CallbackQuery, state: FSMContext):
    name = cb.data.split("edit:", 1)[1]
    await state.update_data(name=name)
    await cb.message.answer(f"Новое количество для {name}:")
    await EditStock.qty.set()
    await cb.answer()

@dp.message_handler(state=EditStock.qty)
async def edit_qty_save(msg: types.Message, state: FSMContext):
    try:
        qty = int(msg.text)
    except ValueError:
        await msg.answer("Введите число.")
        return
    data = await state.get_data()
    cur.execute("UPDATE products SET quantity=? WHERE name=?", (qty, data['name']))
    conn.commit()
    await msg.answer("Остаток обновлён!", reply_markup=admin_kb)
    await state.finish()

# ──────────── Админ: удалить товар ────────────
@dp.message_handler(lambda m: m.text == "❌ Удалить товар" and str(m.from_user.id) in ADMINS)
async def del_choose(msg: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT name FROM products")
    for (name,) in cur.fetchall():
        kb.add(InlineKeyboardButton(name, callback_data=f"del:{name}"))
    await msg.answer("Что удалить?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del:") and str(c.from_user.id) in ADMINS)
async def del_confirm(cb: types.CallbackQuery):
    name = cb.data.split("del:", 1)[1]
    cur.execute("DELETE FROM products WHERE name=?", (name,))
    conn.commit()
    await cb.message.answer(f"❌ {name} удалён.")
    await cb.answer()

# ──────────── Склад ────────────
@dp.message_handler(lambda m: m.text == "📦 Остатки" and str(m.from_user.id) in ADMINS)
async def stock(msg: types.Message):
    cur.execute("SELECT name, quantity FROM products")
    lines = [f"{name}: {qty}" for name, qty in cur.fetchall()]
    await msg.answer("Склад:\n" + "\n".join(lines) if lines else "Склад пуст.")

# ──────────── Назад (для админа) ────────────
@dp.message_handler(lambda m: m.text == "⬅️ Назад" and str(m.from_user.id) in ADMINS)
async def back_admin(msg: types.Message):
    await msg.answer("Главное админ-меню.", reply_markup=admin_kb)

# ──────────── Старт поллинга ────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
