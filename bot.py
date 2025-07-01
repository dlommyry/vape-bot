import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           ReplyKeyboardMarkup, KeyboardButton)
from aiogram.utils import executor
from aiogram.dispatcher import filters

# ---------------------------------------------------------
# 1.  Конфигурация и логирование
# ---------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = {int(x) for x in os.getenv("ADMINS", "").replace(" ", "").split(",") if x}

logging.basicConfig(
    level=logging.INFO,                      # INFO → выводим всё полезное
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---------------------------------------------------------
# 2.  Работа с базой (SQLite)
# ---------------------------------------------------------
DB_FILE = "vape_shop.db"
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

def _create_base_schema(cur):
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT,
            description TEXT,
            category  TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS flavours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            name   TEXT,
            qty    INTEGER DEFAULT 0,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER,
            flavour_id INTEGER,
            qty INTEGER,
            PRIMARY KEY(user_id, flavour_id)
        );
        """
    )

def _ensure_schema(cur):
    """Добавляем недостающие колонки/таблицы при обновлении бота"""
    _create_base_schema(cur)                              # если таблиц не было вовсе

    cur.execute("PRAGMA table_info(products)")
    cols = {row[1] for row in cur.fetchall()}
    if "category" not in cols:
        logging.warning("DB-migrate: добавляем products.category")
        cur.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT 'Разное'")
        conn.commit()

_ensure_schema(cursor)

# ---------------------------------------------------------
# 3.  Клавиатуры
# ---------------------------------------------------------
CATS = ["Одноразовые системы", "Многоразовые системы", "Жидкости", "Разное"]

def main_kb(user_id: int):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
    kb.add(KeyboardButton("📄 Мои заказы"), KeyboardButton("☎️ Поддержка"))
    if user_id in ADMINS:
        kb.add(KeyboardButton("🛠 Админ-панель"))
    return kb

def admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить"), KeyboardButton("✏️ Остаток"))
    kb.add(KeyboardButton("📦 Склад"), KeyboardButton("❌ Удалить"))
    kb.add(KeyboardButton("📃 Заказы"), KeyboardButton("↩️ Назад"))
    return kb

def cat_kb():
    m = InlineKeyboardMarkup()
    for c in CATS:
        m.add(InlineKeyboardButton(c, callback_data=f"CAT_{c}"))
    return m

# ---------------------------------------------------------
# 4.  Состояния «пошагового» добавления товара
# ---------------------------------------------------------
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext

dp.storage = MemoryStorage()

class Add(StatesGroup):
    category  = State()
    name      = State()
    descr     = State()
    flavours  = State()
    qty       = State()

# ---------------------------------------------------------
# 5.  Хэндлеры
# ---------------------------------------------------------
@dp.message_handler(commands=["start"])
async def cmd_start(m: types.Message):
    await m.answer("Добро пожаловать!", reply_markup=main_kb(m.from_user.id))

# ---------- клиент ----------
@dp.message_handler(text="🛍 Каталог")
async def open_catalog(m: types.Message):
    await m.answer("Категории:", reply_markup=cat_kb())

@dp.callback_query_handler(filters.Text(startswith="CAT_"))
async def open_cat(cb: types.CallbackQuery):
    cat = cb.data[4:]
    cur = conn.cursor()
    cur.execute(
        """SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
           FROM products p LEFT JOIN flavours f ON p.id=f.product_id
           WHERE p.category=? GROUP BY p.id""",
        (cat,),
    )
    rows = cur.fetchall()
    if not rows:
        await cb.answer("Пусто")
        return
    txt = "\n".join(f"{r[1]} ({r[2]})" for r in rows)
    await cb.message.answer(txt)
    await cb.answer()

@dp.message_handler(text="🧺 Корзина")
async def show_cart(m: types.Message):
    cur.execute("""SELECT SUM(qty) FROM carts WHERE user_id=?""", (m.from_user.id,))
    total = cur.fetchone()[0]
    if not total:
        await m.answer("Ваша корзина пуста.")
    else:
        await m.answer(f"Товаров в корзине: {total}")

@dp.message_handler(text="📄 Мои заказы")
async def my_orders(m: types.Message):
    await m.answer("У вас пока нет заказов.")  # упрощённо

@dp.message_handler(text="☎️ Поддержка")
async def support(m: types.Message):
    await m.answer("Контакт: @PlumbusSupport")

# ---------- переключатель панелей ----------
@dp.message_handler(text="🛠 Админ-панель")
async def admin_panel(m: types.Message):
    if m.from_user.id not in ADMINS:
        return
    await m.answer("Админ-панель.", reply_markup=admin_kb())

@dp.message_handler(text="↩️ Назад")
async def back_to_user(m: types.Message):
    await m.answer("Клиентский режим.", reply_markup=main_kb(m.from_user.id))

# ---------- админ - склад/остаток ----------
@dp.message_handler(lambda m: m.text in {"📦 Склад", "✏️ Остаток"} and m.from_user.id in ADMINS)
async def warehouse(m: types.Message):
    cur.execute("""SELECT p.name,f.name,f.qty
                   FROM flavours f JOIN products p ON p.id=f.product_id
                   ORDER BY p.name""")
    lines = [f"{p} – {fl}: {q}" for p,fl,q in cur.fetchall()]
    await m.answer("\n".join(lines) or "Склад пуст.")

# ---------- админ - добавление ----------
@dp.message_handler(text="➕ Добавить", user_id=ADMINS)
async def add_start(m: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(*CATS)
    await m.answer("Выбери категорию:", reply_markup=kb)
    await state.set_state(Add.category)

@dp.message_handler(state=Add.category)
async def add_name(m: types.Message, state: FSMContext):
    if m.text not in CATS:
        return await m.answer("Используй кнопку категории!")
    await state.update_data(cat=m.text)
    await m.answer("Название:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Add.name)

@dp.message_handler(state=Add.name)
async def add_descr(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Описание:")
    await state.set_state(Add.descr)

@dp.message_handler(state=Add.descr)
async def add_flavour_cnt(m: types.Message, state: FSMContext):
    await state.update_data(descr=m.text)
    await m.answer("Сколько вкусов? (0 — без вкуса)")
    await state.set_state(Add.flavours)

@dp.message_handler(state=Add.flavours)
async def add_flavour_loop(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Введите число.")
    cnt = int(m.text)
    await state.update_data(cnt=cnt, flavours=[], step=0)
    if cnt == 0:
        await _save_product(state, m)
        return
    await m.answer("Вкус №1:")
    await state.set_state(Add.qty)

@dp.message_handler(state=Add.qty)
async def add_qty(m: types.Message, state: FSMContext):
    data = await state.get_data()
    step = data["step"]
    flavours = data["flavours"]
    if step % 2 == 0:      # ждём название вкуса
        flavours.append({"name": m.text})
        await state.update_data(flavours=flavours, step=step+1)
        await m.answer("Количество:")
    else:                  # ждём qty
        if not m.text.isdigit():
            return await m.answer("Введите число.")
        flavours[-1]["qty"] = int(m.text)
        await state.update_data(flavours=flavours, step=step+1)
        if len(flavours) == data["cnt"]:
            await _save_product(state, m)
        else:
            await m.answer(f"Вкус №{len(flavours)+1}:")
    # остаёмся в том же состоянии

async def _save_product(state: FSMContext, m: types.Message):
    d = await state.get_data()
    cur.execute(
        "INSERT INTO products(name,description,category) VALUES(?,?,?)",
        (d["name"], d["descr"], d["cat"]),
    )
    pid = cur.lastrowid
    cur.executemany(
        "INSERT INTO flavours(product_id,name,qty) VALUES(?,?,?)",
        [(pid, f["name"], f["qty"]) for f in d["flavours"]],
    )
    conn.commit()
    logging.info("product added id=%s cat=%s", pid, d["cat"])
    await m.answer("✅ Добавлено", reply_markup=admin_kb())
    await state.finish()

# ---------- DEBUG (видно в Deploy Logs) ----------
if os.getenv("DEBUG"):
    @dp.callback_query_handler(lambda c: True)
    async def _debug_any_cb(c: types.CallbackQuery):
        logging.warning("CALLBACK %s от %s", c.data, c.from_user.id)
        await c.answer()

# ---------------------------------------------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
