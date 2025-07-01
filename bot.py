import logging, os, sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.types import (InlineKeyboardButton as IB,
                           InlineKeyboardMarkup as IM,
                           ReplyKeyboardMarkup as RM, KeyboardButton as KB)
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# ---------------------------------------------------------------------
# 1.  Конфиг & логирование
# ---------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = {int(i) for i in os.getenv("ADMINS", "").replace(" ", "").split(",") if i}
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ---------------------------------------------------------------------
# 2.  БД + курсор
# ---------------------------------------------------------------------
conn = sqlite3.connect("vape_shop.db")
cur = conn.cursor()


def migrate():
    cur.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, description TEXT, category TEXT, created TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS flavours(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
        name TEXT,  qty INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS carts(
        user_id INTEGER, flavour_id INTEGER REFERENCES flavours(id) ON DELETE CASCADE,
        qty INTEGER, PRIMARY KEY(user_id, flavour_id)
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, created TEXT DEFAULT (datetime('now')), status TEXT DEFAULT 'new'
    );
    CREATE TABLE IF NOT EXISTS order_items(
        order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
        flavour_id INTEGER, qty INTEGER
    );
    """)
    conn.commit()


migrate()

# ---------------------------------------------------------------------
# 3.  Клавиатуры
# ---------------------------------------------------------------------
CATS = ['Одноразовые системы', 'Многоразовые системы', 'Жидкости', 'Разное']


def main_kb(uid):
    kb = RM(resize_keyboard=True)
    kb.add("🛍 Каталог", "🧺 Корзина")
    kb.add("📄 Мои заказы", "☎️ Поддержка")
    if uid in ADMINS:
        kb.add("🛠 Админ-панель")
    return kb


def admin_kb():
    kb = RM(resize_keyboard=True)
    kb.add("➕ Добавить", "✏️ Остаток")
    kb.add("📦 Склад", "❌ Удалить")
    kb.add("📃 Заказы", "↩️ Назад")
    return kb


def cats_inline():
    kb = IM()
    for c in CATS:
        kb.add(IB(c, callback_data=f"CAT_{c}"))
    return kb


def to_int(text: str):
    return text.isdigit() and int(text)


# ---------------------------------------------------------------------
# 4.  FSM добавления товара
# ---------------------------------------------------------------------
class Add(StatesGroup):
    cat = State()
    name = State()
    desc = State()
    flav_cnt = State()
    flav_loop = State()  # внутри храним step & data


# ---------------------------------------------------------------------
# 5.  Клиентские хэндлеры
# ---------------------------------------------------------------------
@dp.message_handler(commands="start")
async def cmd_start(m: types.Message):
    await m.answer("Добро пожаловать!", reply_markup=main_kb(m.from_user.id))


@dp.message_handler(text="🛍 Каталог")
async def catalog(m: types.Message):
    await m.answer("Категории:", reply_markup=cats_inline())


@dp.callback_query_handler(lambda c: c.data.startswith("CAT_"))
async def cat_list(cb: types.CallbackQuery):
    cat = cb.data[4:]
    cur.execute("""
        SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
        FROM products p LEFT JOIN flavours f ON f.product_id=p.id
        WHERE p.category=? GROUP BY p.id""", (cat,))
    rows = cur.fetchall()
    if not rows:
        await cb.answer("Пусто")
        return
    kb = IM()
    for pid, name, qty in rows:
        kb.add(IB(f"{name} ({qty})", callback_data=f"PR_{pid}"))
    await cb.message.answer("\n".join(f"{name} ({qty})" for _, name, qty in rows),
                            reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("PR_"))
async def product_card(cb: types.CallbackQuery):
    pid = int(cb.data[3:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name, desc = cur.fetchone()
    cur.execute("SELECT id,name,qty FROM flavours WHERE product_id=? AND qty>0", (pid,))
    rows = cur.fetchall()
    if not rows:
        return await cb.answer("Нет в наличии")
    kb = IM()
    for fid, fname, qty in rows:
        kb.add(IB(f"{fname} ({qty})", callback_data=f"FL_{fid}"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode='Markdown', reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("FL_"))
async def choose_flavour(cb: types.CallbackQuery):
    fid = int(cb.data[3:])
    cur.execute("SELECT name,qty FROM flavours WHERE id=?", (fid,))
    fname, qty = cur.fetchone()
    kb = IM(row_width=5)
    for i in range(1, min(qty, 10) + 1):
        kb.insert(IB(str(i), callback_data=f"ADD_{fid}_{i}"))
    await cb.message.answer(f"Сколько «{fname}»?", reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("ADD_"))
async def add_to_cart(cb: types.CallbackQuery):
    _, fid, amount = cb.data.split("_")
    fid, amount = int(fid), int(amount)
    uid = cb.from_user.id
    cur.execute("""INSERT INTO carts(user_id,flavour_id,qty)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id,flavour_id) DO UPDATE SET qty=qty+excluded.qty""",
                (uid, fid, amount))
    conn.commit()
    await cb.answer("Добавлено в корзину ✅", show_alert=True)


@dp.message_handler(text="🧺 Корзина")
async def basket(m: types.Message):
    cur.execute("""SELECT f.name, c.qty
                   FROM carts c JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""", (m.from_user.id,))
    rows = cur.fetchall()
    if not rows:
        return await m.answer("Ваша корзина пуста.")
    txt = "\n".join(f"{name} ×{qty}" for name, qty in rows)
    await m.answer(txt)


@dp.message_handler(text="☎️ Поддержка")
async def support(m: types.Message):
    await m.answer("Контакт: @PlumbusSupport")


@dp.message_handler(text="📄 Мои заказы")
async def my_orders(m: types.Message):
    cur.execute("SELECT id,created,status FROM orders WHERE user_id=?", (m.from_user.id,))
    rows = cur.fetchall()
    if not rows:
        return await m.answer("Вы ещё не сделали ни одного заказа.")
    txt = "\n".join(f"#{oid} • {created} • {status}" for oid, created, status in rows)
    await m.answer(txt)

# ---------------------------------------------------------------------
# 6.  Админ-панель
# ---------------------------------------------------------------------
@dp.message_handler(text="🛠 Админ-панель", user_id=ADMINS)
async def adm(m: types.Message):
    await m.answer("Админ-панель.", reply_markup=admin_kb())


@dp.message_handler(text="↩️ Назад", user_id=ADMINS)
async def back(m: types.Message):
    await m.answer("Клиентский режим.", reply_markup=main_kb(m.from_user.id))


@dp.message_handler(text="✏️ Остаток", user_id=ADMINS)
async def stock(m: types.Message):
    cur.execute("""SELECT p.name,f.name,f.qty
                   FROM flavours f JOIN products p ON p.id=f.product_id
                   ORDER BY p.name""")
    rows = cur.fetchall()
    txt = "\n".join(f"{p} – {fl}: {q}" for p, fl, q in rows) or "Пусто."
    await m.answer(txt)


@dp.message_handler(text="📃 Заказы", user_id=ADMINS)
async def order_list(m: types.Message):
    cur.execute("SELECT id,user_id,created,status FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    if not rows:
        return await m.answer("Заказов нет.")
    txt = "\n".join(f"#{oid} • {uid} • {created} • {st}" for oid, uid, created, st in rows)
    await m.answer(txt)


# --------  ➕ добавление товаров  --------
@dp.message_handler(text="➕ Добавить", user_id=ADMINS)
async def add_cat(m: types.Message, state: FSMContext):
    kb = RM(resize_keyboard=True).add(*CATS)
    await m.answer("Категория:", reply_markup=kb)
    await Add.cat.set()


@dp.message_handler(state=Add.cat, user_id=ADMINS)
async def add_name(m: types.Message, state: FSMContext):
    if m.text not in CATS:
        return await m.answer("Выберите кнопку.")
    await state.update_data(cat=m.text)
    await m.answer("Название:", reply_markup=types.ReplyKeyboardRemove())
    await Add.next()


@dp.message_handler(state=Add.name, user_id=ADMINS)
async def add_desc(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Описание:")
    await Add.next()


@dp.message_handler(state=Add.desc, user_id=ADMINS)
async def add_fc(m: types.Message, state: FSMContext):
    await state.update_data(desc=m.text)
    await m.answer("Сколько вкусов? (0 — без вкуса)")
    await Add.next()


@dp.message_handler(state=Add.flav_cnt, user_id=ADMINS)
async def flav_cnt(m: types.Message, state: FSMContext):
    if not to_int(m.text) and m.text != "0":
        return await m.answer("Введите число.")
    total = int(m.text)
    await state.update_data(total=total, step=0, flavours=[])
    if total == 0:
        await save_product(state, m)
    else:
        await m.answer("Вкус №1:")
        await Add.flav_loop.set()


@dp.message_handler(state=Add.flav_loop, user_id=ADMINS)
async def flav_loop(m: types.Message, state: FSMContext):
    data = await state.get_data()
    step = data["step"]
    flavours = data["flavours"]
    total = data["total"]

    if step % 2 == 0:      # ждём название вкуса
        flavours.append({"name": m.text})
        await state.update_data(flavours=flavours, step=step + 1)
        await m.answer("Количество:")
    else:                  # ждём qty
        if not to_int(m.text):
            return await m.answer("Введите число.")
        flavours[-1]["qty"] = int(m.text)
        await state.update_data(flavours=flavours, step=step + 1)
        if len(flavours) == total:
            await save_product(state, m)
        else:
            await m.answer(f"Вкус №{len(flavours) + 1}:")


async def save_product(state: FSMContext, m: types.Message):
    d = await state.get_data()
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",
                (d["name"], d["desc"], d["cat"]))
    pid = cur.lastrowid
    if d["total"]:
        cur.executemany(
            "INSERT INTO flavours(product_id,name,qty) VALUES(?,?,?)",
            [(pid, f["name"], f["qty"]) for f in d["flavours"]]
        )
    conn.commit()
    await m.answer("✅ Добавлено", reply_markup=admin_kb())
    await state.finish()
    logging.info("Added product %s (%s)", d["name"], pid)

# ---------------------------------------------------------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
