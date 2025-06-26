"""
Plumbus Shop   bot.py   v2.2  (28 Jun 2025)
— Исправлена «вечная загрузка» после выбора категории
— Кнопка «Очистить» корзину обновляет сообщение
— Админ-кнопки обрабатываются стабильнее
"""

import logging, sqlite3, pathlib, datetime, asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS            # env-переменные

# ────────────────────────── БАЗА ───────────────────────────────
DB = "/data/vape_shop.db"                       # Railway volume
def init_db():
    fresh = not pathlib.Path(DB).exists()
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,name TEXT,description TEXT,category TEXT);
    CREATE TABLE IF NOT EXISTS flavors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,flavor TEXT,qty INTEGER);
    CREATE TABLE IF NOT EXISTS cart(
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,flavor_id INTEGER,qty INTEGER);
    CREATE TABLE IF NOT EXISTS waitlist(user_id INTEGER,flavor_id INTEGER);
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,items TEXT,ts TEXT,status TEXT);
    """); con.commit(); con.close()
    if fresh: logging.info("Создана новая база %s", DB)
init_db()
db  = sqlite3.connect(DB)
cur = db.cursor()

# ────────────────────────── БОТ ────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s • %(message)s")
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

CATS = {
    "one_time": "Одноразовые системы",
    "pod":      "Многоразовые системы",
    "juice":    "Жидкости",
    "other":    "Разное"
}

def kb_user(admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог", "🧺 Корзина")
    kb.row("📞 Поддержка", "📜 Мои заказы")
    if admin: kb.add("🔄 Сменить режим")
    return kb

kb_admin = ReplyKeyboardMarkup(resize_keyboard=True)
kb_admin.row("➕ Добавить", "❌ Удалить")
kb_admin.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
kb_admin.add("🔄 Сменить режим")

# ────────── FSM ────────────────────────────────────────────────
class Mode(StatesGroup):   user = State(); admin = State()
class Add (StatesGroup):
    cat=State(); name=State(); desc=State(); cnt=State(); flavor=State(); qty=State()
class Buy (StatesGroup): fid=State(); maxq=State(); qty=State()

# ────────── СТАРТ / РЕЖИМЫ ────────────────────────────────────
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    await m.answer("Добро пожаловать!",
                   reply_markup=kb_user(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

@dp.message_handler(lambda m:m.text.startswith("🔄") and str(m.from_user.id) in ADMINS, state="*")
async def switch_mode(m, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=kb_admin); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=kb_user(True)); await Mode.user.set()

# ────────── ПОДДЕРЖКА ──────────────────────────────────────────
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m): await m.answer("Связь: @PlumbusSupport")

# ────────── КАТАЛОГ → КАТЕГОРИИ ────────────────────────────────
@dp.message_handler(lambda m:m.text=="🛍 Каталог", state="*")
async def show_categories(m):
    kb = InlineKeyboardMarkup()
    for code,title in CATS.items():
        kb.add(InlineKeyboardButton(title,callback_data=f"C{code}"))
    await m.answer("Категории:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("C"), state="*")
async def list_products(cb):
    await cb.answer()                                   # мгновенный ACK
    code = cb.data[1:]
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p LEFT JOIN flavors f ON f.product_id=p.id
                   WHERE p.category=? GROUP BY p.id""",(code,))
    rows = cur.fetchall()
    kb = InlineKeyboardMarkup()
    text = CATS[code]+":"
    if rows:
        for pid,n,q in rows:
            kb.add(InlineKeyboardButton(f"{n} ({q})", callback_data=f"P{pid}"))
    else:
        text = "В этой категории пока пусто."
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="BACK"))
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except:                                             # не получилось отредактировать
        await cb.message.answer(text, reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="BACK", state="*")
async def back_root(cb):
    await show_categories(cb.message); await cb.answer()

# ────────── КАРТОЧКА ТОВАРА ────────────────────────────────────
@dp.callback_query_handler(lambda c:c.data.startswith("P"), state="*")
async def card(cb,state:FSMContext):
    pid=int(cb.data[1:])
    cur.execute("SELECT name,description,category FROM products WHERE id=?", (pid,))
    name,desc,cat=cur.fetchone()
    await state.update_data(pid=pid,cat=cat)
    kb=InlineKeyboardMarkup()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    for fid,fl,q in cur.fetchall():
        label=f"{fl} ({q})" if fl!="default" else f"Остаток ({q})"
        kb.add(InlineKeyboardButton(label,callback_data=("F" if q>0 else "W")+str(fid)))
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="BACKCAT"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

@dp.callback_query_handler(lambda c:c.data=="BACKCAT", state="*")
async def back_cat(cb,state:FSMContext):
    cat=(await state.get_data())['cat']
    fake = types.CallbackQuery(id=cb.id,data="C"+cat,from_user=cb.from_user,message=cb.message)
    await list_products(fake)

# ────────── ВЫБОР ВКУСА / КОЛ-ВА ───────────────────────────────
@dp.callback_query_handler(lambda c:c.data.startswith("F"), state=Mode.user)
async def choose_qty(cb,state:FSMContext):
    fid=int(cb.data[1:]); cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,)); maxq=cur.fetchone()[0]
    await state.update_data(fid=fid,maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1,min(maxq,10)+1):
        kb.add(InlineKeyboardButton(str(i),callback_data=f"Q{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("Q"), state=Buy.qty)
async def add_to_cart(cb,state:FSMContext):
    qty=int(cb.data[1:]); d=await state.get_data()
    if qty>d['maxq']:
        await cb.answer("Столько нет на складе!", show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (cb.from_user.id,d['fid'],qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("W"), state=Mode.user)
async def to_wait(cb):
    cur.execute("INSERT OR IGNORE INTO waitlist VALUES(?,?)",(cb.from_user.id,int(cb.data[1:])))
    db.commit(); await cb.answer("Сообщу, когда появится")

# ────────── КОРЗИНА ────────────────────────────────────────────
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def cart(m):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                             JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows:
        await m.answer("Ваша корзина пуста."); return
    txt, kb = [], InlineKeyboardMarkup()
    for rid,n,fl,q in rows:
        txt.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"D{rid}"))
    kb.row(InlineKeyboardButton("❌ Очистить",callback_data="CLR"),
           InlineKeyboardButton("✅ Оформить",callback_data="OK"))
    await m.answer("Корзина:\n"+"\n".join(txt), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="CLR", state=Mode.user)
async def cart_clear(cb):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.answer("Очищено")
    await cart(cb.message)

@dp.callback_query_handler(lambda c:c.data.startswith("D"), state=Mode.user)
async def cart_del(cb):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[1:]),)); db.commit()
    await cb.answer("Удалено")
    await cart(cb.message)

# ────────── ЧЕК-АУТ ────────────────────────────────────────────
@dp.callback_query_handler(lambda c:c.data=="OK", state=Mode.user)
async def checkout(cb):
    uid=cb.from_user.id
    cur.execute("""SELECT flavors.flavor,products.name,cart.qty,flavors.id
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                             JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    rows=cur.fetchall()
    if not rows: await cb.answer("Пусто"); return
    items=[]
    for fl,n,qt,fid in rows:
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (qt,fid))
        items.append(f"{n} ({fl})×{qt}")
    txt=", ".join(items)
    ts=datetime.datetime.now().isoformat(timespec='minutes')
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",
                (uid,txt,ts,"new")); oid=cur.lastrowid
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,)); db.commit()
    for admin in ADMINS:
        await bot.send_message(admin,f"🆕 Заказ #{oid}\n{txt}\nUID {uid}")
    await cb.message.answer(f"Заказ #{oid} принят!")
    await cb.answer()

@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def my_orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows:
        await m.answer("Вы не сделали ещё ни одного заказа."); return
    await m.answer("\n\n".join(f"#{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ────────── АДМИН-ПАНЕЛЬ: КОМАНДЫ (только Mode.admin) ───────────
@dp.message_handler(lambda m:str(m.from_user.id) in ADMINS, state=Mode.admin)
async def admin_router(m, state:FSMContext):
    txt=m.text.strip()
    if txt.startswith("➕"): await add_cat(m)
    elif txt.startswith("📦"): await stock(m)
    # далее: удаление, остаток, заказы – реализуйте по необходимости

# ----- добавление товара (категория → название → …) -------------
async def add_cat(m):
    kb=InlineKeyboardMarkup()
    for code,title in CATS.items():
        kb.add(InlineKeyboardButton(title,callback_data=f"AC{code}"))
    await m.answer("Категория:", reply_markup=kb); await Add.cat.set()

@dp.callback_query_handler(lambda c:c.data.startswith("AC"), state=Add.cat)
async def add_name(cb,state:FSMContext):
    await state.update_data(cat=cb.data[2:])
    await cb.message.answer("Название:"); await Add.name.set(); await cb.answer()

@dp.message_handler(state=Add.name)
async def add_desc(m,state:FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def add_cnt(m,state:FSMContext):
    await state.update_data(desc=m.text)
    await m.answer("Сколько вкусов? (0 — без вкуса)"); await Add.cnt.set()

@dp.message_handler(state=Add.cnt)
async def add_cnt_ok(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Введите число."); return
    left=int(m.text); await state.update_data(left=left,flv=[],qty=[])
    if left==0:
        await state.update_data(single=True)
        await m.answer("Количество товара (число):"); await Add.qty.set()
    else:
        await m.answer("Вкус №1:"); await Add.flavor.set()

@dp.message_handler(state=Add.flavor)
async def add_flavor(m,state:FSMContext):
    await state.update_data(curr_fl=m.text)
    await m.answer("Количество (число):"); await Add.qty.set()

@dp.message_handler(state=Add.qty)
async def add_qty(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Введите число."); return
    q=int(m.text); d=await state.get_data()
    if d.get("single"):
        await finish_add(d,m,"default",q); return
    d['flv'].append(d['curr_fl']); d['qty'].append(q); d['left']-=1
    await state.update_data(**d)
    if d['left']==0:
        await finish_add(d,m)
    else:
        await m.answer(f"Вкус №{len(d['flv'])+1}:"); await Add.flavor.set()

def finish_add(d,m,fl="default",q=0):
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",
                (d['name'],d['desc'],d['cat'])); pid=cur.lastrowid
    if d.get("single"):
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,fl,q))
    else:
        for f,qt in zip(d['flv'],d['qty']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,f,qt))
    db.commit(); m.answer("Товар добавлен ✅", reply_markup=kb_admin)
    asyncio.create_task(dp.current_state().finish())

# ----- склад (простой вывод остатков)
async def stock(m):
    cur.execute("""SELECT p.name,f.flavor,f.qty FROM products p
                   JOIN flavors f ON f.product_id=p.id""")
    rows=cur.fetchall()
    if not rows:
        await m.answer("Склад пуст."); return
    await m.answer("\n".join(f"{n} – {fl}: {q}" for n,fl,q in rows))

# ────────────────────────────────────────────────────────────────
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
