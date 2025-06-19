import logging, sqlite3, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (KeyboardButton, ReplyKeyboardMarkup,
                           InlineKeyboardButton, InlineKeyboardMarkup)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS   # BOT_TOKEN и ADMINS берём из Railway Variables

# ─────────────────────── базовая настройка
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ─────────────────────── SQLite
db  = sqlite3.connect("vape_shop.db")
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT,
    quantity INTEGER,
    flavors TEXT                 -- NULL или "Манго, Виноград, Кола"
);
CREATE TABLE IF NOT EXISTS cart (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_id INTEGER,
    flavor TEXT,
    qty INTEGER
);
CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    product_id INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT,                  -- "Waka×2 (Манго), Elf×1"
    ts TEXT,                     -- ISO 8601
    status TEXT
);
""")
db.commit()

# ─────────────────────── клавиатуры
user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
user_kb.row("🛍 Каталог", "🧺 Корзина")
user_kb.row("📞 Поддержка", "📜 Мои заказы")
user_kb.row("⬅️ Назад", "🔄 Сменить режим")

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить", "❌ Удалить")
admin_kb.row("✏️ Остаток", "📦 Склад")
admin_kb.row("📑 Заказы")
admin_kb.row("⬅️ Назад", "🔄 Сменить режим")

# ─────────────────────── FSM-состояния
class Mode(StatesGroup):
    user  = State()
    admin = State()

class AddFSM(StatesGroup):
    name = State(); desc = State(); qty = State(); flavors = State()

class EditFSM(StatesGroup):
    pid = State(); qty = State()

class QtyFSM(StatesGroup):
    waiting = State()   # ждём количество

# ─────────────────────── /start
@dp.message_handler(commands="start", state="*")
async def cmd_start(m: types.Message, state:FSMContext):
    if str(m.from_user.id) in ADMINS:
        await m.answer("Вы в клиентском режиме.", reply_markup=user_kb)
        await Mode.user.set()
    else:
        await m.answer("Добро пожаловать!", reply_markup=user_kb)

# ───────── 🔄 переключатель режима
@dp.message_handler(lambda m: m.text == "🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch_mode(m: types.Message, state:FSMContext):
    if await state.get_state() == Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb); await Mode.user.set()

# ───────── кнопка «Назад» (просто повторяет соответствующее меню)
@dp.message_handler(lambda m: m.text == "⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def back_admin(m: types.Message): await m.answer("Админ-меню", reply_markup=admin_kb)
@dp.message_handler(lambda m: m.text == "⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.user)
async def back_user(m: types.Message):  await m.answer("Клиент-меню", reply_markup=user_kb)

# ─────────────────────── пользовательские функции
@dp.message_handler(lambda m: m.text == "📞 Поддержка", state=Mode.user)
async def support(m: types.Message): await m.answer("Для связи: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог", state=Mode.user)
async def catalog(m: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT id,name,quantity FROM products")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})", callback_data=f"v:{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("v:"), state=Mode.user)
async def view(cb: types.CallbackQuery):
    pid = int(cb.data[2:])
    cur.execute("SELECT name,description,quantity,flavors FROM products WHERE id=?", (pid,))
    name,desc,qty,flavors = cur.fetchone()
    kb = InlineKeyboardMarkup()
    if qty > 0:
        if flavors:                               # есть вкусы
            for idx,flv in enumerate(flavors.split(","), 1):
                flv = flv.strip()
                kb.add(InlineKeyboardButton(f"{flv}", callback_data=f"a:{pid}:{idx}"))
        else:                                    # нет вкусов
            kb.add(InlineKeyboardButton("🛒 В корзину", callback_data=f"a:{pid}:0"))
    else:
        kb.add(InlineKeyboardButton("🔔 Ждать", callback_data=f"w:{pid}"))
    txt = f"*{name}*\n{desc}\nОстаток: {qty}"
    await cb.message.answer(txt, parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# Добавление в корзину (шаг 1 — выбор количества)
@dp.callback_query_handler(lambda c: c.data.startswith("a:"), state=Mode.user)
async def choose_qty(cb: types.CallbackQuery, state:FSMContext):
    _, pid, flav_idx = cb.data.split(":")
    await state.update_data(pid=int(pid), flav_idx=int(flav_idx))
    kb = InlineKeyboardMarkup()
    for i in range(1,11):
        kb.add(InlineKeyboardButton(str(i), callback_data=f"q:{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await QtyFSM.waiting.set()
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("q:"), state=QtyFSM.waiting)
async def save_qty(cb: types.CallbackQuery, state:FSMContext):
    qty = int(cb.data[2:])
    data = await state.get_data()
    pid, flav_idx = data['pid'], data['flav_idx']
    # flavour text
    cur.execute("SELECT flavors FROM products WHERE id=?", (pid,))
    flist = cur.fetchone()[0]
    flavor = None
    if flist and flav_idx:
        flavor = flist.split(",")[flav_idx-1].strip()
    cur.execute("INSERT INTO cart(user_id, product_id, flavor, qty) VALUES (?,?,?,?)",
                (cb.from_user.id, pid, flavor, qty))
    db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# Ждать товар
@dp.callback_query_handler(lambda c: c.data.startswith("w:"), state=Mode.user)
async def wait_item(cb: types.CallbackQuery):
    pid = int(cb.data[2:])
    cur.execute("INSERT INTO waitlist VALUES (?,?)", (cb.from_user.id, pid)); db.commit()
    await cb.answer("Сообщу, когда появится!")

# Покупательская корзина
@dp.message_handler(lambda m: m.text == "🧺 Корзина", state=Mode.user)
async def show_cart(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,cart.flavor,cart.qty
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    items = cur.fetchall()
    if not items:
        await m.answer("Корзина пуста."); return
    kb = InlineKeyboardMarkup()
    lines=[]
    for rid,name,flav,qty in items:
        label = f"{name}"
        if flav: label += f" ({flav})"
        lines.append(f"{rid}. {label} ×{qty}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"del:{rid}"))
    kb.add(
        InlineKeyboardButton("❌ Очистить", callback_data="clr"),
        InlineKeyboardButton("✅ Оформить", callback_data="ok")
    )
    await m.answer("Корзина:\n" + "\n".join(lines), reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data=="clr", state=Mode.user)
async def cart_clear(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("del:"), state=Mode.user)
async def cart_del(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data.split(':')[1]),)); db.commit()
    await cb.answer("Удалено"); await show_cart(cb.message)

# Оформление заказа
async def notify_waitlist(pid:int, new_qty:int):
    if new_qty<=0: return
    cur.execute("SELECT user_id FROM waitlist WHERE product_id=?", (pid,))
    users = [u for (u,) in cur.fetchall()]
    if not users: return
    cur.execute("SELECT name FROM products WHERE id=?", (pid,)); name=cur.fetchone()[0]
    for uid in users:
        try: await bot.send_message(uid, f"🔔 *{name}* снова в наличии!", parse_mode="Markdown")
        except: pass
    cur.execute("DELETE FROM waitlist WHERE product_id=?", (pid,)); db.commit()

@dp.callback_query_handler(lambda c: c.data=="ok", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    cur.execute("""SELECT products.name,cart.flavor,cart.qty,cart.product_id
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(cb.from_user.id,))
    rows=cur.fetchall()
    if not rows: await cb.answer("Пусто"); return
    # формирование строки и уменьшение остатков
    parts=[]
    for name,flav,qty,pid in rows:
        lbl = f"{name}" + (f" ({flav})" if flav else "")
        parts.append(f"{lbl}×{qty}")
        cur.execute("UPDATE products SET quantity = quantity - ? WHERE id=?", (qty,pid))
        # уведомить waitlist, если остаток стал >0 — во время редактирования уже делаем
    items_text = ", ".join(parts)
    ts = datetime.datetime.now().isoformat(timespec='minutes')
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",
                (cb.from_user.id, items_text, ts, "new"))
    oid = cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()

    # уведомление админам
    for adm in ADMINS:
        await bot.send_message(adm, f"🆕 Заказ #{oid}\n{items_text}\nОт: {cb.from_user.get_mention(cb.from_user.id)}")

    await cb.message.edit_text(f"Заказ #{oid} принят! Менеджер свяжется.")
    await cb.answer()

# История заказов пользователя
@dp.message_handler(lambda m: m.text=="📜 Мои заказы", state=Mode.user)
async def my_orders(m: types.Message):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (m.from_user.id,))
    rows = cur.fetchall()
    if not rows:
        await m.answer("У вас ещё нет заказов."); return
    text = "\n\n".join(f"№{oid} • {ts[:16]}\n{it}\nСтатус: {st}" for oid,ts,it,st in rows)
    await m.answer(text)

# ─────────────────────── АДМИН-ФУНКЦИИ
# ➕ Добавить товар (с вкуcами)
@dp.message_handler(lambda m: m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_1(m: types.Message): await m.answer("Название:"); await AddFSM.name.set()
@dp.message_handler(state=AddFSM.name)
async def add_2(m: types.Message, state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await AddFSM.desc.set()
@dp.message_handler(state=AddFSM.desc)
async def add_3(m: types.Message, state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Количество (число):"); await AddFSM.qty.set()
@dp.message_handler(state=AddFSM.qty)
async def add_4(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Нужно число."); return
    await state.update_data(qty=q)
    await m.answer("Вкусы через запятую (или «-», если без вкусов):"); await AddFSM.flavors.set()
@dp.message_handler(state=AddFSM.flavors)
async def add_save(m: types.Message, state:FSMContext):
    data=await state.get_data()
    flavors = None if m.text.strip()=="-" else m.text
    cur.execute("INSERT INTO products(name,description,quantity,flavors) VALUES(?,?,?,?)",
                (data['name'], data['desc'], data['qty'], flavors))
    db.commit()
    await m.answer("Добавлено ✅", reply_markup=admin_kb)
    await state.finish()

# ✏️ Остаток
@dp.message_handler(lambda m: m.text=="✏️ Остаток" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def edit_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"chg:{pid}"))
    await m.answer("Выбери товар:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("chg:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def edit_qty(cb: types.CallbackQuery, state:FSMContext):
    pid=int(cb.data[4:]); await state.update_data(pid=pid)
    await cb.message.answer("Новое количество:"); await EditFSM.qty.set(); await cb.answer()
@dp.message_handler(state=EditFSM.qty)
async def edit_save(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число!"); return
    pid=(await state.get_data())['pid']
    cur.execute("UPDATE products SET quantity=? WHERE id=?", (q,pid)); db.commit()
    await m.answer("Обновлено.", reply_markup=admin_kb)
    await notify_waitlist(pid, q)  # уведомить ожидающих
    await state.finish()

# ❌ Удалить
@dp.message_handler(lambda m: m.text=="❌ Удалить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def del_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"del:{pid}"))
    await m.answer("Удалить:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("del:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def del_exec(cb: types.CallbackQuery):
    pid=int(cb.data[4:]); cur.execute("DELETE FROM products WHERE id=?", (pid,)); db.commit()
    await cb.message.answer("Удалено."); await cb.answer()

# 📦 Склад
@dp.message_handler(lambda m: m.text=="📦 Склад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def show_stock(m: types.Message):
    cur.execute("SELECT id,name,quantity FROM products")
    txt="\n".join(f"{pid}. {n} — {q}" for pid,n,q in cur.fetchall()) or "Пусто."
    await m.answer(txt)

# 📑 Заказы CRM
@dp.message_handler(lambda m: m.text=="📑 Заказы" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def crm(m: types.Message):
    cur.execute("SELECT id,user_id,ts,status FROM orders ORDER BY id DESC LIMIT 20")
    rows=cur.fetchall()
    if not rows: await m.answer("Заказов нет."); return
    kb=InlineKeyboardMarkup()
    out=[]
    for oid,uid,ts,st in rows:
        out.append(f"#{oid} • {ts[:16]} • {st} • UID {uid}")
        if st!="done": kb.add(InlineKeyboardButton(f"✅ #{oid}", callback_data=f"done:{oid}"))
    await m.answer("\n".join(out), reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("done:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def mark_done(cb: types.CallbackQuery):
    oid=int(cb.data[5:]); cur.execute("UPDATE orders SET status='done' WHERE id=?", (oid,)); db.commit()
    await cb.answer("Закрыт."); await crm(cb.message)

# ─────────────────────── запуск
if __name__=="__main__":
    logging.info("Bot started")
    executor.start_polling(dp, skip_updates=True)
