"""
Plumbus Shop • bot.py • v2.4.1  (26 Jun 2025)

✓ «пустая» категория больше не крутится: обрабатываю callback_data == "none"
✓ добавление товара безопасно завершается; три неверных ввода подряд → /cancel
✓ после ✏️ Остаток склад перерисовывается мгновенно
"""

import logging, sqlite3, pathlib, datetime, asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import (
    ReplyKeyboardMarkup, InlineKeyboardMarkup,
    KeyboardButton, InlineKeyboardButton
)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS

# ──────────────────── DB ─────────────────────
DB_PATH = "/data/vape_shop.db"
def init_db():
    fresh = not pathlib.Path(DB_PATH).exists()
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products(
      id INTEGER PRIMARY KEY, name TEXT, description TEXT, category TEXT);
    CREATE TABLE IF NOT EXISTS flavors(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_id INTEGER, flavor TEXT, qty INTEGER);
    CREATE TABLE IF NOT EXISTS cart(
      rowid INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, flavor_id INTEGER, qty INTEGER);
    CREATE TABLE IF NOT EXISTS orders(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, items TEXT, ts TEXT, status TEXT DEFAULT 'new');
    """); con.commit(); con.close()
    if fresh: logging.info("🆕 database created")
init_db()
db  = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = db.cursor()

# ──────────────────── BOT & UI ───────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s • %(message)s")
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

CATS = {
    "one":  "Одноразовые системы",
    "pod":  "Многоразовые системы",
    "juice":"Жидкости",
    "other":"Разное"
}

def kb_user(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог", "🧺 Корзина")
    kb.row("📞 Поддержка", "📜 Мои заказы")
    if is_admin: kb.add("🔄 Сменить режим")
    return kb

kb_admin = ReplyKeyboardMarkup(resize_keyboard=True)
kb_admin.row("➕ Добавить", "❌ Удалить")
kb_admin.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
kb_admin.add("🔄 Сменить режим")

# ──────────────────── STATES ────────────────
class Mode(StatesGroup):   user = State(); admin = State()
class Add(StatesGroup):
    cat = State(); name = State(); desc = State()
    flav_cnt = State(); flav_name = State(); flav_qty = State()
class StockEd(StatesGroup): fid = State(); qty = State()
class Buy(StatesGroup):     fid = State(); maxq = State(); qty = State()

# ──────────────────── HELPERS ───────────────
async def show_categories(msg: types.Message):
    kb = InlineKeyboardMarkup()
    for k, v in CATS.items():
        kb.add(InlineKeyboardButton(v, callback_data=f"C_{k}"))
    await msg.answer("Категории:", reply_markup=kb)

def basket_rows(uid: int):
    cur.execute("""
      SELECT cart.rowid, products.name, flavors.flavor, cart.qty, flavors.id
      FROM cart
      JOIN flavors  ON flavors.id  = cart.flavor_id
      JOIN products ON products.id = flavors.product_id
      WHERE cart.user_id=?""", (uid,))
    return cur.fetchall()

# ──────────────────── /start & switch ───────
@dp.message_handler(commands="start", state="*")
async def cmd_start(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Добро пожаловать!",
                   reply_markup=kb_user(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

@dp.message_handler(lambda m: m.text.startswith("🔄") and str(m.from_user.id) in ADMINS,
                    state="*")
async def switch_mode(m: types.Message, state: FSMContext):
    if await state.get_state() == Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=kb_admin)
        await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=kb_user(True))
        await Mode.user.set()

# ──────────────────── CANCEL ────────────────
@dp.message_handler(commands="cancel", state="*")
async def cancel(m: types.Message, state: FSMContext):
    if await state.get_state():
        await state.finish()
        await m.answer("Действие отменено.", reply_markup=kb_user(str(m.from_user.id) in ADMINS))

# ──────────────────── КАТАЛОГ ───────────────
@dp.message_handler(lambda m: m.text == "🛍 Каталог", state="*")
async def catalog_entry(m: types.Message): await show_categories(m)

@dp.callback_query_handler(lambda c: c.data.startswith("C_"), state="*")
async def cat_selected(cb: types.CallbackQuery):
    cat = cb.data[2:]
    cur.execute("""
      SELECT p.id, p.name, COALESCE(SUM(f.qty), 0)
      FROM products p LEFT JOIN flavors f ON p.id=f.product_id
      WHERE p.category=? GROUP BY p.id""", (cat,))
    rows = cur.fetchall()
    kb = InlineKeyboardMarkup()
    if rows:
        for pid, name, qty in rows:
            kb.add(InlineKeyboardButton(f"{name} ({qty})", callback_data=f"P_{pid}"))
    else:
        kb.add(InlineKeyboardButton("— пусто —", callback_data="none"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="CAT_BACK"))
    await cb.message.answer(CATS[cat] + ":", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data == "CAT_BACK", state="*")
async def cat_back(cb: types.CallbackQuery):
    await show_categories(cb.message); await cb.answer()

@dp.callback_query_handler(lambda c: c.data == "none", state="*")
async def none_answer(cb: types.CallbackQuery):
    # просто отвечаем, чтобы Telegram убрал «крутилку»
    await cb.answer()

# ───────────── карточка товара ──────────────
@dp.callback_query_handler(lambda c: c.data.startswith("P_"), state="*")
async def product_card(cb: types.CallbackQuery, state: FSMContext):
    pid = int(cb.data[2:])
    await state.update_data(pid=pid)
    cur.execute("SELECT name, description FROM products WHERE id=?", (pid,))
    name, desc = cur.fetchone()
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT id, flavor, qty FROM flavors WHERE product_id=?", (pid,))
    for fid, fl, qt in cur.fetchall():
        cap = f"{fl} ({qt})" if fl != "default" else f"Остаток ({qt})"
        kb.add(InlineKeyboardButton(cap, callback_data=("F_" if qt > 0 else "W_") + str(fid)))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="CAT_BACK"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# лист ожидания
@dp.callback_query_handler(lambda c: c.data.startswith("W_"), state=Mode.user)
async def wait_list(cb: types.CallbackQuery):
    fid = int(cb.data[2:])
    cur.execute("INSERT OR IGNORE INTO waitlist VALUES(?,?)", (cb.from_user.id, fid))
    db.commit()
    await cb.answer("Сообщу, когда появится!")

# выбор вкуса -> количество
@dp.callback_query_handler(lambda c: c.data.startswith("F_"), state=Mode.user)
async def choose_qty(cb: types.CallbackQuery, state: FSMContext):
    fid = int(cb.data[2:])
    cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,))
    maxq = cur.fetchone()[0]
    await state.update_data(fid=fid, maxq=maxq)
    kb = InlineKeyboardMarkup()
    for i in range(1, min(maxq, 10) + 1):
        kb.add(InlineKeyboardButton(str(i), callback_data=f"Q_{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("Q_"), state=Buy.qty)
async def add_to_cart(cb: types.CallbackQuery, state: FSMContext):
    qty = int(cb.data[2:])
    d   = await state.get_data()
    if qty > d['maxq']:
        await cb.answer("Столько нет!", show_alert=True); return
    cur.execute("INSERT INTO cart(user_id, flavor_id, qty) VALUES(?,?,?)",
                (cb.from_user.id, d['fid'], qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# ───────────── корзина ──────────────────────
@dp.message_handler(lambda m: m.text == "🧺 Корзина", state=Mode.user)
async def cart_view(m: types.Message):
    rows = basket_rows(m.from_user.id)
    if not rows:
        await m.answer("Ваша корзина пуста."); return
    kb = InlineKeyboardMarkup(); txt = []
    for rid, n, fl, q, _ in rows:
        txt.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"RM_{rid}"))
    kb.row(InlineKeyboardButton("❌ Очистить", callback_data="CLR_CART"),
           InlineKeyboardButton("✅ Оформить", callback_data="CHK_OUT"))
    await m.answer("Корзина:\n" + "\n".join(txt), reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "CLR_CART", state=Mode.user)
async def clear_cart(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.answer("Очищено"); await cart_view(cb.message)

@dp.callback_query_handler(lambda c: c.data.startswith("RM_"), state=Mode.user)
async def remove_item(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[3:]),)); db.commit()
    await cb.answer("Удалено"); await cart_view(cb.message)

# чек-аут
@dp.callback_query_handler(lambda c: c.data == "CHK_OUT", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    rows = basket_rows(cb.from_user.id)
    if not rows: await cb.answer("Корзина пуста"); return
    items = []
    for _, n, fl, q, fid in rows:
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (q, fid))
        items.append(f"{n} ({fl})×{q}")
    text = ", ".join(items)
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO orders(user_id, items, ts) VALUES(?,?,?)",
                (cb.from_user.id, text, ts))
    oid = cur.lastrowid
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    for adm in ADMINS: await bot.send_message(adm, f"🆕 Заказ #{oid}\n{text}\nUID {cb.from_user.id}")
    await cb.message.answer(f"Заказ #{oid} принят!"); await cb.answer()

@dp.message_handler(lambda m: m.text == "📜 Мои заказы", state=Mode.user)
async def my_orders(m: types.Message):
    cur.execute("SELECT id, ts, items, status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (m.from_user.id,))
    rows = cur.fetchall()
    if not rows:
        await m.answer("Вы не сделали ещё ни одного заказа."); return
    await m.answer("\n\n".join(f"#{i} • {ts}\n{it}\nСтатус: {st}" for i, ts, it, st in rows))

# ───────────── админ: Склад / Остаток ────────
@dp.message_handler(lambda m: m.text == "📦 Склад", state=Mode.admin)
async def stock(m: types.Message):
    cur.execute("""SELECT f.id, p.name, f.flavor, f.qty
                   FROM flavors f JOIN products p ON p.id = f.product_id
                   ORDER BY p.id""")
    rows = cur.fetchall()
    if not rows: await m.answer("Склад пуст."); return
    await m.answer("\n".join(f"{fid}. {n} – {fl}: {q}" for fid, n, fl, q in rows))

@dp.message_handler(lambda m: m.text == "✏️ Остаток", state=Mode.admin)
async def stock_edit_prompt(m: types.Message):
    await m.answer("Формат: `ID  новое_кол-во`"); await StockEd.fid.set()

@dp.message_handler(state=StockEd.fid)
async def stock_edit_do(m: types.Message, state: FSMContext):
    parts = m.text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await m.answer("Нужно два числа"); return
    fid, qty = map(int, parts)
    cur.execute("UPDATE flavors SET qty=? WHERE id=?", (qty, fid)); db.commit()
    await m.answer("Обновлено."); await state.finish(); await stock(m)

# ───────────── админ: Добавить товар ────────
@dp.message_handler(lambda m: m.text == "➕ Добавить", state=Mode.admin)
async def add_start(m: types.Message):
    kb = InlineKeyboardMarkup()
    for k, v in CATS.items():
        kb.add(InlineKeyboardButton(v, callback_data=f"AC_{k}"))
    await m.answer("Категория:", reply_markup=kb)
    await Add.cat.set()

@dp.callback_query_handler(lambda c: c.data.startswith("AC_"), state=Add.cat)
async def add_name(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(cat=cb.data[3:])
    await cb.message.answer("Название:"); await Add.name.set(); await cb.answer()

@dp.message_handler(state=Add.name)
async def add_desc(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def add_flavor_cnt(m: types.Message, state: FSMContext):
    await state.update_data(desc=m.text)
    await m.answer("Сколько вкусов? (0 — без вкуса)")
    await Add.flav_cnt.set()

@dp.message_handler(state=Add.flav_cnt)
async def flav_cnt(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        await m.answer("Введите число."); return
    left = int(m.text)
    await state.update_data(left=left, fl=[], qt=[], err=0)
    if left == 0:
        await m.answer("Количество товара:"); await Add.flav_qty.set()
    else:
        await m.answer("Вкус №1:"); await Add.flav_name.set()

@dp.message_handler(state=Add.flav_name)
async def flav_name(m: types.Message, state: FSMContext):
    await state.update_data(curr=m.text); await m.answer("Количество:"); await Add.flav_qty.set()

@dp.message_handler(state=Add.flav_qty)
async def flav_qty(m: types.Message, state: FSMContext):
    d = await state.get_data()
    if not m.text.isdigit():
        err = d.get("err", 0) + 1
        if err >= 3:
            await m.answer("Слишком много ошибок. /cancel")
            await state.finish(); return
        await state.update_data(err=err)
        await m.answer("Введите число."); return
    qty = int(m.text)
    if d['left'] == 0:                 # товар без вкусов
        await finalize_product(d, m, "default", qty); return
    # многовкусный
    d['fl'].append(d['curr']); d['qt'].append(qty); d['left'] -= 1
    d['err'] = 0
    await state.update_data(**d)
    if d['left'] == 0:
        await finalize_product(d, m); return
    await m.answer(f"Вкус №{len(d['fl'])+1}:"); await Add.flav_name.set()

def finalize_product(d, m: types.Message, fl="default", q=0):
    cur.execute("INSERT INTO products(name, description, category) VALUES(?,?,?)",
                (d['name'], d['desc'], d['cat']))
    pid = cur.lastrowid
    if d['fl'] == []:
        cur.execute("INSERT INTO flavors(product_id, flavor, qty) VALUES(?,?,?)",
                    (pid, fl, q))
    else:
        for f, qt in zip(d['fl'], d['qt']):
            cur.execute("INSERT INTO flavors(product_id, flavor, qty) VALUES(?,?,?)",
                        (pid, f, qt))
    db.commit()
    asyncio.create_task(dp.current_state().finish())
    m.answer("Товар добавлен ✅", reply_markup=kb_admin)

# ────────────────────────────────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
