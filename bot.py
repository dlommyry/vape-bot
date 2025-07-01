import os, logging, sqlite3, asyncio, datetime, pathlib
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils import executor
from textwrap import dedent

# ──────────── ENV / CONFIG ─────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS    = [i.strip() for i in os.getenv("ADMINS","").split(",") if i.strip()]

# ──────────── DB init ───────────────────
DB = "/data/vape_shop.db"
first = not pathlib.Path(DB).exists()
con  = sqlite3.connect(DB); cur = con.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,description TEXT,category TEXT);
CREATE TABLE IF NOT EXISTS flavors(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INT,flavor TEXT,qty INT);
CREATE TABLE IF NOT EXISTS cart(rowid INTEGER PRIMARY KEY AUTOINCREMENT,user_id INT,flavor_id INT,qty INT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INT,items TEXT,ts TEXT,status TEXT DEFAULT 'new');
"""); con.commit(); con.close()
db  = sqlite3.connect(DB,check_same_thread=False); cur = db.cursor()
if first: logging.warning("🆕  database created")

# ──────────── Bot / dispatcher ──────────
logging.basicConfig(level=logging.INFO,format="%(levelname).1s | %(message)s")
bot = Bot(BOT_TOKEN); dp = Dispatcher(bot,storage=MemoryStorage())

# ──────────── UI helpers ────────────────
CATS = {"one":"Одноразовые системы","pod":"Многоразовые системы","juice":"Жидкости","other":"Разное"}

def kb_user(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог","🧺 Корзина")
    kb.row("📞 Поддержка","📜 Мои заказы")
    if is_admin: kb.add("🔄 Сменить режим")
    return kb

kb_admin = ReplyKeyboardMarkup(resize_keyboard=True)
kb_admin.row("➕ Добавить","❌ Удалить")
kb_admin.row("✏️ Остаток","📦 Склад","📑 Заказы")
kb_admin.add("🔄 Сменить режим")

def cart_rows(uid):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty,flavors.id
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                   JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    return cur.fetchall()

# ──────────── FSM states ────────────────
class Mode(StatesGroup): user=State(); admin=State()
class Add(StatesGroup): cat=State(); name=State(); desc=State(); cnt=State(); flav=State(); qty=State()
class StockEdit(StatesGroup): fid=State(); qty=State()
class Buy(StatesGroup): fid=State(); maxq=State(); qty=State()

# ──────────── /start ────────────────────
@dp.message_handler(commands="start",state="*")
async def _start(m:types.Message,state:FSMContext):
    await state.finish()
    await m.answer("Добро пожаловать!",reply_markup=kb_user(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

# ──────────── SUPPORT (ловится первым) ──
@dp.message_handler(lambda m:m.text=="📞 Поддержка",state="*")
async def support(m): await m.answer("Контакт: @PlumbusSupport")

# ──────────── switch mode ───────────────
@dp.message_handler(lambda m:m.text.startswith("🔄") and str(m.from_user.id) in ADMINS,state="*")
async def switch(m,state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.",reply_markup=kb_admin); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.",reply_markup=kb_user(True)); await Mode.user.set()

# ──────────── CATALOG (root) ────────────
async def show_categories(msg):
    kb = InlineKeyboardMarkup()
    for k,v in CATS.items(): kb.add(InlineKeyboardButton(v,callback_data=f"CAT_{k}"))
    await msg.answer("Категории:",reply_markup=kb)

@dp.message_handler(lambda m:m.text=="🛍 Каталог",state="*")
async def catalog_root(m): await show_categories(m)

@dp.callback_query_handler(lambda c:c.data=="CAT_BACK",state="*")
async def cat_back(c): await show_categories(c.message); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("CAT_"),state="*")
async def open_cat(c,state:FSMContext):
    cat=c.data[4:]
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p LEFT JOIN flavors f ON p.id=f.product_id
                   WHERE p.category=? GROUP BY p.id""",(cat,)); rows=cur.fetchall()
    kb=InlineKeyboardMarkup()
    if rows:
        for pid,n,q in rows:
            kb.add(InlineKeyboardButton(f"{n} ({q})",callback_data=f"PR_{pid}"))
    else:
        kb.add(InlineKeyboardButton("— пусто —",callback_data="EMPTY"))
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="CAT_BACK"))
    await c.message.answer(CATS[cat]+":",reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data=="EMPTY")
async def _empty(c): await c.answer()

# ──────────── карточка товара ───────────
@dp.callback_query_handler(lambda c:c.data.startswith("PR_"),state="*")
async def product(c,state:FSMContext):
    pid=int(c.data[3:]); await state.update_data(pid=pid)
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    kb=InlineKeyboardMarkup()
    for fid,fl,qt in cur.fetchall():
        cap=f"{fl} ({qt})"
        cb = f"FL_{fid}" if qt>0 else "EMPTY"
        kb.add(InlineKeyboardButton(cap,callback_data=cb))
    kb.add(InlineKeyboardButton("⬅️ Назад",callback_data="CAT_BACK"))
    await c.message.answer(f"*{name}*\n{desc}",parse_mode="Markdown",reply_markup=kb); await c.answer()

# ──────────── покупка ▶ количество ───────
@dp.callback_query_handler(lambda c:c.data.startswith("FL_"),state=Mode.user)
async def how_many(c,state:FSMContext):
    fid=int(c.data[3:]); cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,))
    maxq=cur.fetchone()[0]
    if not maxq: await c.answer("Нет в наличии"); return
    await state.update_data(fid=fid,maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1,min(maxq,10)+1): kb.add(InlineKeyboardButton(str(i),callback_data=f"QQ_{i}"))
    await c.message.answer("Сколько штук?",reply_markup=kb); await Buy.qty.set(); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("QQ_"),state=Buy.qty)
async def add_to_cart(c,state:FSMContext):
    qty=int(c.data[3:]); d=await state.get_data()
    if qty>d['maxq']: await c.answer("Столько нет!",show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (c.from_user.id,d['fid'],qty)); db.commit()
    await state.finish(); await c.message.answer("Добавлено ✅"); await c.answer()

# ──────────── КОШИК  (корзина) ───────────
@dp.message_handler(lambda m:m.text=="🧺 Корзина",state="*")
async def show_cart(m):
    rows=cart_rows(m.from_user.id)
    if not rows: await m.answer("Ваша корзина пуста."); return
    txt, kb = [], InlineKeyboardMarkup()
    for rid,n,fl,q,_ in rows:
        txt.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"DEL_{rid}"))
    kb.row(InlineKeyboardButton("❌ Очистить",callback_data="CLR"),
           InlineKeyboardButton("✅ Оформить",callback_data="CHECK"))
    await m.answer("Корзина:\n"+'\n'.join(txt),reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("DEL_") or c.data in ("CLR","CHECK"),state="*")
async def cart_cb(c):
    uid=c.from_user.id
    if c.data.startswith("DEL_"):
        cur.execute("DELETE FROM cart WHERE rowid=?",(int(c.data[4:]),))
    elif c.data=="CLR":
        cur.execute("DELETE FROM cart WHERE user_id=?",(uid,))
    elif c.data=="CHECK":
        rows=cart_rows(uid)
        if not rows: await c.answer("Корзина пуста"); return
        items=[]; ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for _,n,fl,q,fid in rows:
            cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (q,fid))
            items.append(f"{n} ({fl})×{q}")
        cur.execute("INSERT INTO orders(user_id,items,ts) VALUES(?,?,?)",
                    (uid,', '.join(items),ts)); oid=cur.lastrowid
        cur.execute("DELETE FROM cart WHERE user_id=?", (uid,))
        for adm in ADMINS: await bot.send_message(adm,f"🆕 Заказ #{oid}\n{', '.join(items)}")
        await c.message.answer(f"Заказ #{oid} принят!")
    db.commit(); await c.answer(); await show_cart(c.message)

# ──────────── МОИ ЗАКАЗЫ ────────────────
@dp.message_handler(lambda m:m.text=="📜 Мои заказы",state="*")
async def my_orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Вы не сделали ещё ни одного заказа."); return
    await m.answer('\n\n'.join(f"#{i} • {ts}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ──────────── А Д М И Н  Блок ───────────
@dp.message_handler(lambda m:m.text=="📦 Склад",state=Mode.admin)
async def warehouse(m):
    cur.execute("""SELECT f.id,p.name,f.flavor,f.qty FROM flavors f
                   JOIN products p ON p.id=f.product_id ORDER BY p.id""")
    rows=cur.fetchall()
    txt="Склад пуст." if not rows else '\n'.join(f"{fid}. {n} – {fl}: {q}" for fid,n,fl,q in rows)
    await m.answer(txt)

@dp.message_handler(lambda m:m.text=="✏️ Остаток",state=Mode.admin)
async def stock_edit(m): await m.answer("ID новое_кол-во"); await StockEdit.fid.set()

@dp.message_handler(state=StockEdit.fid)
async def stock_apply(m,state:FSMContext):
    try: fid,new = map(int,m.text.split())
    except: await m.answer("Нужно два числа"); return
    cur.execute("UPDATE flavors SET qty=? WHERE id=?", (new,fid)); db.commit()
    await m.answer("Обновлено."); await state.finish(); await warehouse(m)

@dp.message_handler(lambda m:m.text=="📑 Заказы",state=Mode.admin)
async def all_orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders ORDER BY id DESC LIMIT 10")
    rows=cur.fetchall()
    if not rows: await m.answer("Нет заказов."); return
    await m.answer('\n\n'.join(f"#{i} • {ts}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ──────────── ДОБАВИТЬ ТОВАР ───────────
@dp.message_handler(lambda m:m.text=="➕ Добавить",state=Mode.admin)
async def add_0(m):
    kb=InlineKeyboardMarkup()
    for k,v in CATS.items(): kb.add(InlineKeyboardButton(v,callback_data=f"AC_{k}"))
    await m.answer("Категория:",reply_markup=kb); await Add.cat.set()

@dp.callback_query_handler(lambda c:c.data.startswith("AC_"),state=Add.cat)
async def add_1(c,state:FSMContext):
    await state.update_data(cat=c.data[3:]); await c.message.answer("Название:")
    await Add.name.set(); await c.answer()

@dp.message_handler(state=Add.name)
async def add_2(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)
async def add_3(m,state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0 — без вкуса)"); await Add.cnt.set()

@dp.message_handler(state=Add.cnt)
async def add_4(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Число!"); return
    cnt=int(m.text); await state.update_data(left=cnt,fl=[],qt=[])
    if cnt==0: await m.answer("Количество товара:"); await Add.qty.set()
    else: await m.answer("Вкус №1:"); await Add.flav.set()

@dp.message_handler(state=Add.flav)
async def add_5(m,state:FSMContext):
    d=await state.get_data(); await state.update_data(curr=m.text)
    await m.answer("Количество:"); await Add.qty.set()

@dp.message_handler(state=Add.qty)
async def add_6(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Введите число."); return
    d=await state.get_data(); q=int(m.text)
    if d['left']==0:                     # товар без вкусов
        await finish_product(d,m,"default",q); return
    d['fl'].append(d['curr']); d['qt'].append(q); d['left']-=1
    await state.update_data(left=d['left'],fl=d['fl'],qt=d['qt'])
    if d['left']==0: await finish_product(await state.get_data(),m); return
    await m.answer(f"Вкус №{len(d['fl'])+1}:"); await Add.flav.set()

async def finish_product(d,m,one_fl="default",one_q=0):
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",
                (d['name'],d['desc'],d['cat'])); pid=cur.lastrowid
    if d['fl']==[]:
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,one_fl,one_q))
    else:
        for f,q in zip(d['fl'],d['qt']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,f,q))
    db.commit()
    await m.answer("Товар добавлен ✅",reply_markup=kb_admin)
    await dp.current_state(chat=m.chat.id,user=m.from_user.id).finish()

# ──────────── run ───────────────────────
@dp.callback_query_handler(lambda c: True, state="*")
async def _debug_all_callbacks(c: types.CallbackQuery):
    logging.warning(f"CALLBACK {c.data!r} от {c.from_user.id}")
    await dp.skip_updates()   # передаём дальше к другим хэндлерам

if __name__ == "__main__":
    executor.start_polling(dp,skip_updates=True)
