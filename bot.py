import logging, sqlite3, pathlib, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS            # переменные окружения

# ───────── база
DB = "/data/vape_shop.db"                       # укажите свой путь, если без volume
def ensure_db():
    new = not pathlib.Path(DB).exists()
    con = sqlite3.connect(DB); cur = con.cursor()
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
    if new: logging.info("Создана новая база %s", DB)
ensure_db()
db  = sqlite3.connect(DB)
cur = db.cursor()

# ───────── бот
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ▼ клавиатуры ----------------------------------------------------
def build_user_kb(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛍 Каталог", "🧺 Корзина")
    kb.row("📞 Поддержка", "📜 Мои заказы")
    if is_admin: kb.add("🔄 Сменить режим")
    return kb

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить", "❌ Удалить")
admin_kb.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
admin_kb.add("🔄 Сменить режим")

# ▼ FSM -----------------------------------------------------------
class Mode(StatesGroup):  user = State(); admin = State()
class Add (StatesGroup):  name=State(); desc=State(); cnt=State(); flavor=State(); qty=State()
class Edit(StatesGroup):  fid=State(); qty=State()
class Buy (StatesGroup):  fid=State(); qty=State(); maxq=State()

# ▼ /start --------------------------------------------------------
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    is_admin = str(m.from_user.id) in ADMINS
    await m.answer("Добро пожаловать!", reply_markup=build_user_kb(is_admin))
    await Mode.user.set()

# переключатель
@dp.message_handler(lambda m:m.text=="🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch(m: types.Message, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb)
        await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=build_user_kb(True))
        await Mode.user.set()

# ─────────── КЛИЕНТ ──────────────────────────────────────────────
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m): await m.answer("Связь: @PlumbusSupport")

@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def catalog(m):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                   FROM products p JOIN flavors f ON f.product_id=p.id GROUP BY p.id""")
    for pid,n,q in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{n} ({q})",callback_data=f"P{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("P"), state=Mode.user)
async def show_product(cb):
    pid=int(cb.data[1:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    kb=InlineKeyboardMarkup()
    for fid,fl,q in cur.fetchall():
        lbl=f"{fl} ({q})" if fl!="default" else f"Остаток ({q})"
        cbdata="F"+str(fid) if q>0 else "W"+str(fid)
        kb.add(InlineKeyboardButton(lbl,callback_data=cbdata))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# выбор вкуса
@dp.callback_query_handler(lambda c:c.data.startswith("F"), state=Mode.user)
async def ask_qty(cb,state:FSMContext):
    fid=int(cb.data[1:])
    cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,)); maxq=cur.fetchone()[0]
    await state.update_data(fid=fid, maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1, min(maxq,10)+1):
        kb.add(InlineKeyboardButton(str(i),callback_data=f"Q{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("Q"), state=Buy.qty)
async def add_cart(cb,state:FSMContext):
    qty=int(cb.data[1:])
    data=await state.get_data()
    if qty>data['maxq']:
        await cb.answer("Столько нет на складе!", show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (cb.from_user.id,data['fid'],qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# wait-лист
@dp.callback_query_handler(lambda c:c.data.startswith("W"), state=Mode.user)
async def waitlist(cb):
    cur.execute("INSERT INTO waitlist VALUES(?,?)",(cb.from_user.id,int(cb.data[1:]))); db.commit()
    await cb.answer("Сообщу, когда появится")

# корзина
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def basket(m):
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
async def clr(cb): cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit(); await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("D"), state=Mode.user)
async def del_row(cb): cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[1:]),)); db.commit(); await cb.answer("Удалено"); await basket(cb.message)

# чек-аут
async def ping_waiters(fid,new):
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
async def my_orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Нет заказов."); return
    await m.answer("\n\n".join(f"#{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ─────────────────────────── АДМИН (без изменений логики) ─────────────────────────
# … код админ-блока остаётся тем же, что был в предыдущей версии …

# ───────── запуск
if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, skip_updates=True)
