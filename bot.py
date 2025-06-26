"""
Plumbus Shop • bot.py • v2.4  (29 Jun 2025)

— fixed: category buttons did nothing
— fixed: add-product finished after last flavour
— fixed: stock edit show instantly
— /cancel to escape any step
"""

import logging, sqlite3, pathlib, datetime, asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, InlineKeyboardMarkup,
                           KeyboardButton, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS        # env-переменные

# ───────────── база ───────────────────────────────────────────
DB = "/data/vape_shop.db"
def db_init():
    first = not pathlib.Path(DB).exists()
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,
      name TEXT, description TEXT, category TEXT);
    CREATE TABLE IF NOT EXISTS flavors(id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_id INTEGER, flavor TEXT, qty INTEGER);
    CREATE TABLE IF NOT EXISTS cart(rowid INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, flavor_id INTEGER, qty INTEGER);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, items TEXT, ts TEXT, status TEXT DEFAULT 'new');
    """); con.commit(); con.close()
    if first: logging.info("создана новая база %s", DB)
db_init()
db  = sqlite3.connect(DB, check_same_thread=False)
cur = db.cursor()

# ───────────── бот и клавиатуры ───────────────────────────────
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
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог","🧺 Корзина")
    kb.row("📞 Поддержка","📜 Мои заказы")
    if is_admin: kb.add("🔄 Сменить режим")
    return kb
kb_admin = ReplyKeyboardMarkup(resize_keyboard=True)
kb_admin.row("➕ Добавить","❌ Удалить")
kb_admin.row("✏️ Остаток","📦 Склад","📑 Заказы")
kb_admin.add("🔄 Сменить режим")

# ───────────── FSM ────────────────────────────────────────────
class Mode(StatesGroup): user=State(); admin=State()
class Add (StatesGroup):
    cat=State(); name=State(); desc=State()
    flav_cnt=State(); flav_name=State(); flav_qty=State()
class StockEd(StatesGroup): fid=State(); qty=State()
class Buy  (StatesGroup): fid=State(); maxq=State(); qty=State()

# ───────────── helpers ────────────────────────────────────────
def safe_int(txt): return txt.isdigit()

async def send_categories(msg):
    kb=InlineKeyboardMarkup()
    for k,v in CATS.items(): kb.add(InlineKeyboardButton(v,callback_data=f"C_{k}"))
    await msg.answer("Категории:",reply_markup=kb)

# ───────────── start / смена режима ───────────────────────────
@dp.message_handler(commands="start",state="*")
async def cmd_start(m,state:FSMContext):
    await m.answer("Добро пожаловать!", reply_markup=kb_user(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

@dp.message_handler(lambda m:m.text.startswith("🔄") and str(m.from_user.id) in ADMINS,state="*")
async def mode_toggle(m,state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=kb_admin); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=kb_user(True)); await Mode.user.set()

# ───────────── /cancel ────────────────────────────────────────
@dp.message_handler(commands="cancel",state="*")
async def cancel(m,state:FSMContext):
    if await state.get_state():
        await state.finish(); await m.answer("Отменено.",reply_markup=kb_user(str(m.from_user.id) in ADMINS))

# ───────────── каталог → категории ────────────────────────────
@dp.message_handler(lambda m:m.text=="🛍 Каталог",state="*")
async def catalog(m): await send_categories(m)

@dp.callback_query_handler(lambda c:c.data.startswith("C_"),state="*")
async def show_products(cb):
    cat=cb.data[2:]
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p LEFT JOIN flavors f ON p.id=f.product_id
                   WHERE p.category=? GROUP BY p.id""",(cat,))
    rows=cur.fetchall(); kb=InlineKeyboardMarkup()
    if rows:
        for pid,name,qty in rows:
            kb.add(InlineKeyboardButton(f"{name} ({qty})",callback_data=f"P_{pid}"))
    else: kb.add(InlineKeyboardButton("Пусто",callback_data="none"))
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="CAT_BACK"))
    await cb.message.answer(CATS[cat]+":",reply_markup=kb); await cb.answer()

@dp.callback_query_handler(lambda c:c.data=="CAT_BACK",state="*")
async def back_cat(cb): await send_categories(cb.message); await cb.answer()

# ───────────── карточка товара ────────────────────────────────
@dp.callback_query_handler(lambda c:c.data.startswith("P_"),state="*")
async def card(cb,state:FSMContext):
    pid=int(cb.data[2:]); await state.update_data(pid=pid)
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    kb=InlineKeyboardMarkup()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    for fid,fl,qt in cur.fetchall():
        cap=f"{fl} ({qt})" if fl!="default" else f"Остаток ({qt})"
        kb.add(InlineKeyboardButton(cap,callback_data=("F_" if qt>0 else "W_")+str(fid)))
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="CAT_BACK"))
    await cb.message.answer(f"*{name}*\n{desc}",parse_mode="Markdown",reply_markup=kb); await cb.answer()

# лист ожидания
@dp.callback_query_handler(lambda c:c.data.startswith("W_"),state=Mode.user)
async def waitlist(cb):
    fid=int(cb.data[2:]); cur.execute("INSERT OR IGNORE INTO waitlist VALUES(?,?)",(cb.from_user.id,fid)); db.commit()
    await cb.answer("Сообщу, когда появится!")

# выбор количества
@dp.callback_query_handler(lambda c:c.data.startswith("F_"),state=Mode.user)
async def choose(cb,state:FSMContext):
    fid=int(cb.data[2:]); cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,)); maxq=cur.fetchone()[0]
    await state.update_data(fid=fid,maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1,min(maxq,10)+1): kb.add(InlineKeyboardButton(str(i),callback_data=f"Q_{i}"))
    await cb.message.answer("Сколько штук?",reply_markup=kb); await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("Q_"),state=Buy.qty)
async def add_cart(cb,state:FSMContext):
    qty=int(cb.data[2:]); d=await state.get_data()
    if qty>d['maxq']: await cb.answer("Недостаточно остатка!",show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",(cb.from_user.id,d['fid'],qty)); db.commit()
    await cb.message.answer("Добавлено ✅"); await state.finish(); await cb.answer()

# ───────────── корзина ────────────────────────────────────────
def basket_text(uid):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                   JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    return cur.fetchall()

@dp.message_handler(lambda m:m.text=="🧺 Корзина",state=Mode.user)
async def cart(m):
    rows=basket_text(m.from_user.id)
    if not rows: await m.answer("Ваша корзина пуста."); return
    kb=InlineKeyboardMarkup(); txt=[]
    for rid,n,fl,q in rows:
        txt.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"R_{rid}"))
    kb.row(InlineKeyboardButton("❌ Очистить",callback_data="CLR"),
           InlineKeyboardButton("✅ Оформить",callback_data="CH_OUT"))
    await m.answer("Корзина:\n"+"\n".join(txt),reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="CLR",state=Mode.user)
async def clr(cb):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.answer("Очищено"); await cart(cb.message)

@dp.callback_query_handler(lambda c:c.data.startswith("R_"),state=Mode.user)
async def rm(cb):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[2:]),)); db.commit()
    await cb.answer("Удалено"); await cart(cb.message)

# чек-аут
@dp.callback_query_handler(lambda c:c.data=="CH_OUT",state=Mode.user)
async def checkout(cb):
    rows=basket_text(cb.from_user.id)
    if not rows: await cb.answer("Пусто"); return
    items=[]
    for rid,n,fl,q in rows:
        cur.execute("SELECT flavor_id FROM cart WHERE rowid=?", (rid,))
        fid=cur.fetchone()[0]
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (q,fid))
        items.append(f"{n} ({fl})×{q}")
    text=", ".join(items); ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO orders(user_id,items,ts) VALUES(?,?,?)",(cb.from_user.id,text,ts))
    oid=cur.lastrowid; cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    for a in ADMINS: await bot.send_message(a,f"🆕 Заказ #{oid}\n{text}\nUID {cb.from_user.id}")
    await cb.message.answer(f"Заказ #{oid} принят!"); await cb.answer()

@dp.message_handler(lambda m:m.text=="📜 Мои заказы",state=Mode.user)
async def orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Вы не сделали ещё ни одного заказа."); return
    await m.answer("\n\n".join(f"#{i} • {ts}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ───────────── админ: склад / остаток ─────────────────────────
@dp.message_handler(lambda m:m.text=="📦 Склад",state=Mode.admin)
async def stock(m):
    cur.execute("""SELECT f.id,p.name,f.flavor,f.qty FROM flavors f
                   JOIN products p ON p.id=f.product_id ORDER BY p.id""")
    rows=cur.fetchall()
    if not rows: await m.answer("Склад пуст."); return
    await m.answer("\n".join(f"{fid}. {n} – {fl}: {q}" for fid,n,fl,q in rows))

@dp.message_handler(lambda m:m.text=="✏️ Остаток",state=Mode.admin)
async def edit_start(m):
    await m.answer("Формат: `ID  новое_кол-во`  (например `7 12`)."); await StockEd.fid.set()

@dp.message_handler(state=StockEd.fid)
async def edit_do(m,state:FSMContext):
    parts=m.text.split()
    if len(parts)!=2 or not all(p.isdigit() for p in parts):
        await m.answer("Нужно два числа."); return
    fid,qty=map(int,parts); cur.execute("UPDATE flavors SET qty=? WHERE id=?", (qty,fid)); db.commit()
    await m.answer("Обновлено."); await state.finish(); await stock(m)

# ───────────── админ: добавление товара ──────────────────────
@dp.message_handler(lambda m:m.text=="➕ Добавить",state=Mode.admin)
async def add1(m):
    kb=InlineKeyboardMarkup()
    for k,v in CATS.items(): kb.add(InlineKeyboardButton(v,callback_data=f"AC_{k}"))
    await m.answer("Категория:",reply_markup=kb); await Add.cat.set()

@dp.callback_query_handler(lambda c:c.data.startswith("AC_"),state=Add.cat)
async def add2(cb,state:FSMContext):
    await state.update_data(cat=cb.data[3:]); await cb.message.answer("Название:"); await Add.name.set(); await cb.answer()

@dp.message_handler(state=Add.name)
async def add3(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def add4(m,state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0 — без вкуса)")
    await Add.flav_cnt.set()

@dp.message_handler(state=Add.flav_cnt)
async def add5(m,state:FSMContext):
    if not safe_int(m.text): await m.answer("Число!"); return
    left=int(m.text); await state.update_data(left=left,fl=[],qt=[])
    if left==0: await m.answer("Количество товара:"); await Add.flav_qty.set()
    else: await m.answer("Вкус №1:"); await Add.flav_name.set()

@dp.message_handler(state=Add.flav_name)
async def add6(m,state:FSMContext):
    await state.update_data(curr=m.text); await m.answer("Количество:"); await Add.flav_qty.set()

@dp.message_handler(state=Add.flav_qty)
async def add7(m,state:FSMContext):
    if not safe_int(m.text): await m.answer("Число!"); return
    q=int(m.text); d=await state.get_data()
    if d['left']==0: await finalize_add(d,m,"default",q); return
    d['fl'].append(d['curr']); d['qt'].append(q); d['left']-=1
    await state.update_data(**d)
    if d['left']==0: await finalize_add(d,m); return
    await m.answer(f"Вкус №{len(d['fl'])+1}:"); await Add.flav_name.set()

def finalize_add(d,m,fl="default",q=0):
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",(d['name'],d['desc'],d['cat']))
    pid=cur.lastrowid
    if d['fl']==[]:
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,fl,q))
    else:
        for f,qt in zip(d['fl'],d['qt']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,f,qt))
    db.commit(); m.answer("Товар добавлен ✅",reply_markup=kb_admin)
    asyncio.create_task(dp.current_state().finish())

# ───────────── поддержка ─────────────────────────────────────
@dp.message_handler(lambda m:m.text=="📞 Поддержка",state="*")
async def sup(m): await m.answer("Контакт: @PlumbusSupport")

# ───────────── запуск ────────────────────────────────────────
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
