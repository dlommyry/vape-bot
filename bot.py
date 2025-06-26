"""
Plumbus Shop — Telegram-бот-магазин (SQLite + aiogram)
• путь к базе: /data/vape_shop.db  (Работает на Railway-Volume)
• если файл базы отсутствует — бот создаёт схему сам
• обычные пользователи видят только меню клиента
• у админа дополнительная кнопка «🔄 Сменить режим»
"""

import logging, sqlite3, pathlib, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS          # переменные окружения

# ───────── настройка
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

DB = "/data/vape_shop.db"                     # ФАЙЛ ХРАНИМ В VOLUME
def ensure_db():
    first = not pathlib.Path(DB).exists()
    con   = sqlite3.connect(DB); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
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

db  = sqlite3.connect(DB); cur = db.cursor()

# ───────── клавиатуры
user_kb  = ReplyKeyboardMarkup(resize_keyboard=True)
user_kb.row("🛍 Каталог", "🧺 Корзина")
user_kb.row("📞 Поддержка", "📜 Мои заказы")

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить", "❌ Удалить")
admin_kb.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
admin_kb.add("🔄 Сменить режим")

# ───────── FSM-состояния
class Mode(StatesGroup):  user = State(); admin = State()
class Add (StatesGroup):  name=State(); desc=State(); cnt=State(); flavor=State(); qty=State()
class Edit(StatesGroup):  fid=State(); qty=State()
class Buy (StatesGroup):  fid=State(); qty=State()

# ───────── /start
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    await m.answer("Добро пожаловать!", reply_markup=user_kb)
    await Mode.user.set()

# ───────── смена режима (admin only)
@dp.message_handler(lambda m:m.text=="🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch_mode(m: types.Message, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb)
        await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb)
        await Mode.user.set()

# ─────────────────────────── КЛИЕНТ ────────────────────────────
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m): await m.answer("Связь: @PlumbusSupport")

@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def catalog(m):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p JOIN flavors f ON f.product_id=p.id
                   GROUP BY p.id""")
    for pid,n,q in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{n} ({q})",callback_data=f"P{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("P"), state=Mode.user)
async def show_prod(cb):
    pid=int(cb.data[1:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    kb=InlineKeyboardMarkup()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    for fid,fl,q in cur.fetchall():
        label=f"{fl} ({q})" if fl!="default" else f"Остаток ({q})"
        cbdata="F"+str(fid) if q>0 else "W"+str(fid)
        kb.add(InlineKeyboardButton(label,callback_data=cbdata))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("F"), state=Mode.user)
async def ask_qty(cb,state:FSMContext):
    await state.update_data(fid=int(cb.data[1:]))
    kb=InlineKeyboardMarkup()
    for i in range(1,11): kb.add(InlineKeyboardButton(str(i),callback_data=f"Q{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("Q"), state=Buy.qty)
async def add_cart(cb,state:FSMContext):
    fid=(await state.get_data())['fid']; qty=int(cb.data[1:])
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",(cb.from_user.id,fid,qty)); db.commit()
    await cb.message.answer("Добавлено ✅"); await state.finish(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("W"), state=Mode.user)
async def wait(cb):
    cur.execute("INSERT INTO waitlist VALUES(?,?)",(cb.from_user.id,int(cb.data[1:]))); db.commit()
    await cb.answer("Сообщу, когда появится")

# корзина
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def cart(m):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                             JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Корзина пуста."); return
    kb=InlineKeyboardMarkup(); txt=[]
    for rid,n,fl,q in rows:
        txt.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"D{rid}"))
    kb.add(InlineKeyboardButton("❌ Очистить",callback_data="CLR"),
           InlineKeyboardButton("✅ Оформить",callback_data="OK"))
    await m.answer("Корзина:\n"+"\n".join(txt), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="CLR", state=Mode.user)
async def clr(cb):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("D"), state=Mode.user)
async def del_row(cb): cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[1:]),)); db.commit(); await cb.answer("Удалено"); await cart(cb.message)

# заказ
async def ping_wait(fid,new):
    if new<=0: return
    cur.execute("SELECT user_id FROM waitlist WHERE flavor_id=?", (fid,))
    ids=[u for (u,) in cur.fetchall()]
    if not ids: return
    cur.execute("""SELECT products.name,flavors.flavor FROM flavors
                   JOIN products ON products.id=flavors.product_id WHERE flavors.id=?""",(fid,))
    n,fl=cur.fetchone()
    for u in ids:
        try: await bot.send_message(u,f"🔔 *{n}* ({fl}) снова в наличии!", parse_mode="Markdown")
        except: pass
    cur.execute("DELETE FROM waitlist WHERE flavor_id=?", (fid,)); db.commit()

@dp.callback_query_handler(lambda c:c.data=="OK", state=Mode.user)
async def checkout(cb):
    uid=cb.from_user.id
    cur.execute("""SELECT flavors.id,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                             JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    rows=cur.fetchall()
    if not rows: await cb.answer("Пусто"); return
    items=[]
    for fid,n,fl,qt in rows:
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (qt,fid))
        items.append(f"{n} ({fl})×{qt}")
    txt=", ".join(items); ts=datetime.datetime.now().isoformat(timespec='minutes')
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",(uid,txt,ts,"new"))
    oid=cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,)); db.commit()
    for adm in ADMINS: await bot.send_message(adm,f"🆕 Заказ #{oid}\n{txt}\nUID {uid}")
    await cb.message.edit_text(f"Заказ #{oid} принят!"); await cb.answer()

@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def my(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Нет заказов."); return
    await m.answer("\n\n".join(f"#{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ─────────────────────────── АДМИН ────────────────────────────
@dp.message_handler(lambda m:m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_start(m): await m.answer("Название:"); await Add.name.set()

@dp.message_handler(state=Add.name)
async def add_desc(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def add_cnt(m,state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0 — без)"); await Add.cnt.set()

@dp.message_handler(state=Add.cnt)
async def add_loop(m,state:FSMContext):
    try:n=int(m.text)
    except: await m.answer("Число."); return
    await state.update_data(left=n,flv=[],qty=[])
    if n==0: await add_finish(state,m,default=True)
    else:    await m.answer("Название вкуса:"); await Add.flavor.set()

@dp.message_handler(state=Add.flavor)
async def add_qty(m,state:FSMContext):
    await state.update_data(curr=m.text); await m.answer("Количество:"); await Add.qty.set()

@dp.message_handler(state=Add.qty)
async def save_one(m,state:FSMContext):
    try:q=int(m.text)
    except: await m.answer("Число."); return
    d=await state.get_data(); d['flv'].append(d['curr']); d['qty'].append(q); d['left']-=1
    await state.update_data(**d)
    if d['left']==0: await add_finish(state,m)
    else: await m.answer("Название следующего вкуса:"); await Add.flavor.set()

async def add_finish(state:FSMContext,m,default=False):
    d=await state.get_data()
    cur.execute("INSERT INTO products(name,description) VALUES(?,?)",(d['name'],d['desc'])); pid=cur.lastrowid
    if default: cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,"default",0))
    else:
        for f,q in zip(d['flv'],d['qty']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,f,q))
    db.commit(); await state.finish(); await m.answer("Товар добавлен.", reply_markup=admin_kb)

# остаток
@dp.message_handler(lambda m:m.text=="✏️ Остаток" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def pick_flv(m):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT flavors.id,products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    for fid,n,f,q in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{n}/{f} ({q})",callback_data=f"E{fid}"))
    await m.answer("Выбери вкус:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("E") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def ask_new(cb,state:FSMContext):
    await state.update_data(fid=int(cb.data[1:])); await cb.message.answer("Новое количество:"); await Edit.qty.set(); await cb.answer()

@dp.message_handler(state=Edit.qty)
async def save_new(m,state:FSMContext):
    try:q=int(m.text)
    except: await m.answer("Число."); return
    fid=(await state.get_data())['fid']; cur.execute("UPDATE flavors SET qty=? WHERE id=?", (q,fid)); db.commit()
    await ping_wait(fid,q); await m.answer("Обновлено.", reply_markup=admin_kb); await state.finish()

# склад
@dp.message_handler(lambda m:m.text=="📦 Склад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def stock(m):
    cur.execute("""SELECT products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    rows=cur.fetchall()
    await m.answer("\n".join(f"{n}/{f}: {q}" for n,f,q in rows) or "Пусто.")

# удалить
@dp.message_handler(lambda m:m.text=="❌ Удалить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def del_menu(m):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,n in cur.fetchall(): kb.add(InlineKeyboardButton(n,callback_data=f"DEL{pid}"))
    await m.answer("Удалить товар:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("DEL") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def del_ok(cb):
    pid=int(cb.data[3:]); cur.execute("DELETE FROM products WHERE id=?", (pid,)); cur.execute("DELETE FROM flavors WHERE product_id=?", (pid,)); db.commit()
    await cb.message.answer("Удалено."); await cb.answer()

# заказы
@dp.message_handler(lambda m:m.text=="📑 Заказы" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def orders(m):
    cur.execute("SELECT id,user_id,ts,status FROM orders ORDER BY id DESC LIMIT 20")
    rows=cur.fetchall()
    if not rows: await m.answer("Пусто."); return
    kb=InlineKeyboardMarkup(); txt=[]
    for oid,uid,ts,st in rows:
        txt.append(f"#{oid} • {ts[:16]} • {st} • UID {uid}")
        if st!="done": kb.add(InlineKeyboardButton(f"✅ {oid}",callback_data=f"FIN{oid}"))
    await m.answer("\n".join(txt), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("FIN") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def fin(cb):
    oid=int(cb.data[3:]); cur.execute("UPDATE orders SET status='done' WHERE id=?", (oid,)); db.commit()
    await cb.answer("Закрыто."); await orders(cb.message)

# ───────── запуск
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
