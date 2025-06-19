import logging, sqlite3, datetime, os, pathlib
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardButton, InlineKeyboardMarkup)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS           # переменные окружения

# ─────────────────────── базовая настройка
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

DB_FILE = "vape_shop.db"
FIRST_RUN = not pathlib.Path(DB_FILE).exists()
db  = sqlite3.connect(DB_FILE)
cur = db.cursor()

# ─────────────────────── новая схема БД
cur.executescript("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS flavors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    flavor TEXT,
    qty INTEGER
);
CREATE TABLE IF NOT EXISTS cart (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    flavor_id INTEGER,
    qty INTEGER
);
CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    flavor_id INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT,
    ts TEXT,
    status TEXT
);
""")
db.commit()

# ────────────── миграция старых таблиц (products.quantity → flavors)
if FIRST_RUN:
    try:
        cur.execute("SELECT id,name,description,quantity,flavors FROM products")
        old = cur.fetchall()
    except sqlite3.OperationalError:
        old = []
    if old:
        logging.info("Performing one-time migration to flavor table…")
        cur.execute("DELETE FROM flavors")
        for pid,name,desc,qty,flv in old:
            if flv:                              # уже были строкой?
                parts=[s.strip() for s in flv.split(",")]
                per_qty = qty//len(parts) if qty else 0
                for f in parts:
                    cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",
                                (pid,f,per_qty))
            else:
                cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",
                            (pid,"default",qty))
        cur.execute("ALTER TABLE products RENAME TO products_old")  # просто архив
        db.commit()
        logging.info("Migration done.")

# ─────────────────────── клавиатуры
user_kb  = ReplyKeyboardMarkup(resize_keyboard=True)
user_kb.row("🛍 Каталог","🧺 Корзина")
user_kb.row("📞 Поддержка","📜 Мои заказы")
user_kb.row("⬅️ Назад","🔄 Сменить режим")

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить","❌ Удалить")
admin_kb.row("✏️ Остаток","📦 Склад","📑 Заказы")
admin_kb.row("⬅️ Назад","🔄 Сменить режим")

# ─────────────────────── FSM
class Mode(StatesGroup):
    user=State(); admin=State()

class AddFSM(StatesGroup):
    name=State(); desc=State(); flav_num=State(); flav_iter=State()

class EditFSM(StatesGroup):
    fid=State(); qty=State()

class QtyFSM(StatesGroup):
    waiting=State()

# ─────────────────────── /start
@dp.message_handler(commands="start", state="*")
async def cmd_start(m: types.Message, state:FSMContext):
    if str(m.from_user.id) in ADMINS:
        await m.answer("Вы в клиентском режиме.", reply_markup=user_kb)
        await Mode.user.set()
    else:
        await m.answer("Добро пожаловать!", reply_markup=user_kb)

# ───── переключатель режима
@dp.message_handler(lambda m:m.text=="🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch(m: types.Message, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb)
        await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb)
        await Mode.user.set()

# ───── Назад
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def back_ad(m: types.Message): await m.answer("Админ-меню", reply_markup=admin_kb)
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.user)
async def back_us(m: types.Message): await m.answer("Клиент-меню", reply_markup=user_kb)

# ─────────────────────── пользовательская часть
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m: types.Message): await m.answer("Связь: @PlumbusSupport")

# каталог
@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def catalog(m: types.Message):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT p.id,p.name,
                SUM(f.qty) as q
                FROM products p JOIN flavors f ON f.product_id=p.id
                GROUP BY p.id""")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})",callback_data=f"s:{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

# список вкусов конкретного товара
@dp.callback_query_handler(lambda c:c.data.startswith("s:"), state=Mode.user)
async def show_flavors(cb: types.CallbackQuery):
    pid=int(cb.data[2:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    kb=InlineKeyboardMarkup()
    for fid,flv,qty in cur.fetchall():
        label=f"{flv} ({qty})"
        if qty>0: kb.add(InlineKeyboardButton(label, callback_data=f"f:{fid}"))
        else:     kb.add(InlineKeyboardButton(label+" ❌", callback_data=f"w:{fid}"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# выбор вкуса → выбор qty
@dp.callback_query_handler(lambda c:c.data.startswith("f:"), state=Mode.user)
async def choose_qty(cb: types.CallbackQuery, state:FSMContext):
    fid=int(cb.data[2:]); await state.update_data(fid=fid)
    kb=InlineKeyboardMarkup()
    for i in range(1,11): kb.add(InlineKeyboardButton(str(i), callback_data=f"q:{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await QtyFSM.waiting.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("q:"), state=QtyFSM.waiting)
async def add_cart(cb: types.CallbackQuery, state:FSMContext):
    qty=int(cb.data[2:]); fid=(await state.get_data())['fid']
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (cb.from_user.id,fid,qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# ждать вкус
@dp.callback_query_handler(lambda c:c.data.startswith("w:"), state=Mode.user)
async def wait_flavor(cb: types.CallbackQuery):
    fid=int(cb.data[2:])
    cur.execute("INSERT INTO waitlist VALUES (?,?)",(cb.from_user.id,fid)); db.commit()
    await cb.answer("Сообщу, как появится!")

# корзина
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def cart(m: types.Message):
    cur.execute("""SELECT cart.rowid, products.name, flavors.flavor, cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                              JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Корзина пуста."); return
    kb=InlineKeyboardMarkup()
    text=[]
    for rid,name,flv,qty in rows:
        text.append(f"{rid}. {name} ({flv}) ×{qty}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"del:{rid}"))
    kb.add(InlineKeyboardButton("❌ Очистить",callback_data="clr"),
           InlineKeyboardButton("✅ Оформить",callback_data="ok"))
    await m.answer("Корзина:\n"+"\n".join(text), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="clr", state=Mode.user)
async def clear_cart(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("del:"), state=Mode.user)
async def del_item(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[4:]),)); db.commit()
    await cb.answer("Удалено"); await cart(cb.message)

# checkout
async def notify_wait(fid:int, new_q:int):
    if new_q<=0: return
    cur.execute("SELECT user_id FROM waitlist WHERE flavor_id=?", (fid,))
    users=[u for (u,) in cur.fetchall()]
    if not users: return
    cur.execute("""SELECT products.name,flavors.flavor
                   FROM flavors JOIN products ON products.id=flavors.product_id
                   WHERE flavors.id=?""",(fid,))
    name,flv=cur.fetchone()
    for u in users:
        try: await bot.send_message(u,f"🔔 *{name}* ({flv}) снова в наличии!", parse_mode="Markdown")
        except: pass
    cur.execute("DELETE FROM waitlist WHERE flavor_id=?", (fid,)); db.commit()

@dp.callback_query_handler(lambda c:c.data=="ok", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    uid=cb.from_user.id
    cur.execute("""SELECT flavors.id, products.name, flavors.flavor, cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                              JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    items=cur.fetchall()
    if not items: await cb.answer("Пусто"); return
    line=[]
    for fid,name,flv,qt in items:
        label=f"{name} ({flv})"; line.append(f"{label}×{qt}")
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (qt,fid))
    text=", ".join(line)
    ts=datetime.datetime.now().isoformat(timespec='minutes')
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",(uid,text,ts,"new"))
    oid=cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,)); db.commit()
    # оповестить адм
    for adm in ADMINS:
        await bot.send_message(adm,f"🆕 Заказ #{oid}\n{text}\nUID {uid}")
    await cb.message.edit_text(f"Заказ #{oid} принят, ждите связи менеджера.")
    await cb.answer()

# мои заказы
@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def orders_user(m: types.Message):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Нет заказов."); return
    await m.answer("\n\n".join(f"№{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ─────────────────────── АДМИН
# ➕ Добавить товар с вкуcами
@dp.message_handler(lambda m:m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_name(m: types.Message): await m.answer("Название:"); await AddFSM.name.set()
@dp.message_handler(state=AddFSM.name)
async def add_desc(m: types.Message, state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await AddFSM.desc.set()
@dp.message_handler(state=AddFSM.desc)
async def add_fnum(m: types.Message, state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0-если без)"); await AddFSM.flav_num.set()
@dp.message_handler(state=AddFSM.flav_num)
async def add_iter(m: types.Message, state:FSMContext):
    try:n=int(m.text)
    except: await m.answer("Число."); return
    await state.update_data(fnum=n, fleft=n, flavors=[], qtys=[])
    await m.answer("Теперь по одному: «НАЗВАНИЕ:КОЛ-ВО», пример  🟢Манго:10  .") if n else await add_save_no(m,state)

@dp.message_handler(state=AddFSM.flav_iter)
async def add_collect(m: types.Message, state:FSMContext):
    data=await state.get_data()
    try:
        name,qty=m.text.split(":"); qty=int(qty)
    except:
        await m.answer("Формат: Вкус:количество"); return
    data['flavors'].append(name.strip())
    data['qtys'].append(qty)
    data['fleft']-=1
    await state.update_data(**data)
    if data['fleft']==0:
        await save_product(state, m)
    else:
        await m.answer(f"Осталось {data['fleft']} вкусов…")

async def add_save_no(m,state):
    # без вкусов
    data=await state.get_data()
    cur.execute("INSERT INTO products(name,description) VALUES(?,?)",(data['name'],data['desc']))
    pid=cur.lastrowid
    cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,"default",int(m.text)))
    db.commit()
    await m.answer("Добавлено.",reply_markup=admin_kb); await state.finish()

async def save_product(state,m):
    data=await state.get_data()
    cur.execute("INSERT INTO products(name,description) VALUES(?,?)",(data['name'],data['desc']))
    pid=cur.lastrowid
    for flv,qt in zip(data['flavors'],data['qtys']):
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,flv,qt))
    db.commit()
    await m.answer("Товар добавлен.",reply_markup=admin_kb); await state.finish()

# ✏️ Остаток
@dp.message_handler(lambda m:m.text=="✏️ Остаток" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def choose_flavor(m: types.Message):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT flavors.id,products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    for fid,pname,fname,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{pname}/{fname} ({qty})", callback_data=f"e:{fid}"))
    await m.answer("Выберите вкус:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("e:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def ask_newqty(cb: types.CallbackQuery, state:FSMContext):
    fid=int(cb.data[2:]); await state.update_data(fid=fid)
    await cb.message.answer("Новое количество:"); await EditFSM.qty.set(); await cb.answer()
@dp.message_handler(state=EditFSM.qty)
async def save_newqty(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число."); return
    fid=(await state.get_data())['fid']
    cur.execute("UPDATE flavors SET qty=? WHERE id=?", (q,fid)); db.commit()
    await notify_wait(fid,q)
    await m.answer("Обновлено.",reply_markup=admin_kb); await state.finish()

# 📦 склад
@dp.message_handler(lambda m:m.text=="📦 Склад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def stock(m: types.Message):
    cur.execute("""SELECT products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    rows=cur.fetchall()
    txt="\n".join(f"{n}/{f}: {q}" for n,f,q in rows) or "Пусто."
    await m.answer(txt)

# ❌Удалить товар полностью
@dp.message_handler(lambda m:m.text=="❌ Удалить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def del_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall():
        kb.add(InlineKeyboardButton(name, callback_data=f"d:{pid}"))
    await m.answer("Удалить товар:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("d:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def del_exec(cb: types.CallbackQuery):
    pid=int(cb.data[2:]); cur.execute("DELETE FROM products WHERE id=?", (pid,)); cur.execute("DELETE FROM flavors WHERE product_id=?", (pid,)); db.commit()
    await cb.message.answer("Удалено."); await cb.answer()

# 📑 заказы
@dp.message_handler(lambda m:m.text=="📑 Заказы" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def list_orders(m: types.Message):
    cur.execute("SELECT id,user_id,ts,status FROM orders ORDER BY id DESC LIMIT 20")
    rows=cur.fetchall()
    if not rows: await m.answer("Пусто."); return
    kb=InlineKeyboardMarkup()
    txt=[]
    for oid,uid,ts,st in rows:
        txt.append(f"#{oid} • {ts[:16]} • {st} • UID {uid}")
        if st!="done": kb.add(InlineKeyboardButton(f"✅ {oid}",callback_data=f"o:{oid}"))
    await m.answer("\n".join(txt),reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("o:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def mark_done(cb: types.CallbackQuery):
    oid=int(cb.data[2:]); cur.execute("UPDATE orders SET status='done' WHERE id=?", (oid,)); db.commit()
    await cb.answer("Готово."); await list_orders(cb.message)

# ─────────────────────── запуск
if __name__=="__main__":
    logging.info("Bot started")
    executor.start_polling(dp, skip_updates=True)
