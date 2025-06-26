"""
Plumbus-Shop  •  bot.py  •  v2.3   (28 Jun 2025)
✦ Fix: category buttons
✦ /cancel для выхода из FSM-добавления
✦ '✏️ Остаток' – редактирование количества
"""

import logging, sqlite3, pathlib, datetime, asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import (ReplyKeyboardMarkup, InlineKeyboardMarkup,
                           KeyboardButton, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS          # читает из env

# ─────────────────────── БАЗА ────────────────────────────────
DB = "/data/vape_shop.db"
def ensure_db():
    fresh = not pathlib.Path(DB).exists()
    con = sqlite3.connect(DB); cur = con.cursor()
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
    if fresh: logging.info("Создана новая база")

ensure_db()
db  = sqlite3.connect(DB, check_same_thread=False)
cur = db.cursor()

# ─────────────────────── БОТ ─────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
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

# ─────────────── FSM состояния ──────────────────────────────
class Mode(StatesGroup): user=State(); admin=State()
class Add(StatesGroup):
    cat=State(); name=State(); desc=State(); cnt=State(); flav=State(); qty=State()
class StockEdit(StatesGroup): fid=State(); qty=State()
class Buy (StatesGroup): fid=State(); maxq=State(); qty=State()

# ─────────────── /start + смена режима ───────────────────────
@dp.message_handler(commands="start", state="*")
async def start(m, state:FSMContext):
    await m.answer("Добро пожаловать!", reply_markup=kb_user(str(m.from_user.id) in ADMINS))
    await Mode.user.set()

@dp.message_handler(lambda m:m.text.startswith("🔄") and str(m.from_user.id) in ADMINS, state="*")
async def switch(m, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=kb_admin); await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=kb_user(True)); await Mode.user.set()

# ─────────────── ПОДДЕРЖКА ───────────────────────────────────
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m): await m.answer("Контакт: @PlumbusSupport")

# ─────────────── /cancel  (выход из FSM) ─────────────────────
@dp.message_handler(commands="cancel", state="*")
async def cancel(m, state:FSMContext):
    if await state.get_state():
        await state.finish()
        await m.answer("Действие отменено.", reply_markup=kb_user(str(m.from_user.id) in ADMINS))

# ─────────────── КАТАЛОГ ─────────────────────────────────────
@dp.message_handler(lambda m:m.text=="🛍 Каталог", state="*")
async def cats(m):
    kb = InlineKeyboardMarkup()
    for c,t in CATS.items(): kb.add(InlineKeyboardButton(t,callback_data="CAT_"+c))
    await m.answer("Категории:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("CAT_"), state="*")
async def list_products(cb):
    cat = cb.data[4:]; cur.execute("""SELECT p.id,p.name,COALESCE(SUM(f.qty),0)
                                      FROM products p LEFT JOIN flavors f
                                      ON f.product_id=p.id
                                      WHERE p.category=? GROUP BY p.id""",(cat,))
    rows = cur.fetchall()
    kb = InlineKeyboardMarkup()
    if rows:
        for pid,n,q in rows:
            kb.add(InlineKeyboardButton(f"{n} ({q})", callback_data=f"PR_{pid}"))
    else:
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="BACK_TO_CAT"))
        await cb.message.answer("Пусто.", reply_markup=kb); await cb.answer(); return
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="BACK_TO_CAT"))
    await cb.message.answer(CATS[cat]+":", reply_markup=kb); await cb.answer()

@dp.callback_query_handler(lambda c:c.data=="BACK_TO_CAT", state="*")
async def back_cat(cb): await cats(cb.message); await cb.answer()

# ─────────────── КАРТОЧКА ТОВАРА ─────────────────────────────
@dp.callback_query_handler(lambda c:c.data.startswith("PR_"), state="*")
async def show_card(cb, state:FSMContext):
    pid=int(cb.data.split("_")[1]); await state.update_data(pid=pid)
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    kb=InlineKeyboardMarkup()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    for fid,fl,qt in cur.fetchall():
        tt = f"{fl} ({qt})" if fl!="default" else f"Остаток ({qt})"
        kb.add(InlineKeyboardButton(tt, callback_data=("FL_" if qt>0 else "WL_")+str(fid)))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="BACK_TO_CAT"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# ─────────── добавление во «В лист ожидания» ───────────────────
@dp.callback_query_handler(lambda c:c.data.startswith("WL_"), state=Mode.user)
async def wait_list(cb):
    fid=int(cb.data[3:]); cur.execute("INSERT OR IGNORE INTO waitlist VALUES(?,?)",(cb.from_user.id,fid)); db.commit()
    await cb.answer("Сообщу, когда появится!")

# ─────────── выбор кол-ва и добавление в корзину ───────────────
@dp.callback_query_handler(lambda c:c.data.startswith("FL_"), state=Mode.user)
async def choose_qty(cb,state:FSMContext):
    fid=int(cb.data[3:]); cur.execute("SELECT qty FROM flavors WHERE id=?", (fid,)); maxq=cur.fetchone()[0]
    await state.update_data(fid=fid,maxq=maxq)
    kb=InlineKeyboardMarkup()
    for i in range(1,min(maxq,10)+1):
        kb.add(InlineKeyboardButton(str(i), callback_data=f"QQ_{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb); await Buy.qty.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("QQ_"), state=Buy.qty)
async def add_cart(cb,state:FSMContext):
    qty=int(cb.data[3:]); d=await state.get_data()
    if qty>d['maxq']: await cb.answer("Нет такого количества!", show_alert=True); return
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",(cb.from_user.id,d['fid'],qty)); db.commit()
    await cb.message.answer("Добавлено ✅"); await state.finish(); await cb.answer()

# ─────────────── КОРЗИНА ──────────────────────────────────────
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def basket(m):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                   JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Ваша корзина пуста."); return
    kb=InlineKeyboardMarkup()
    lines=[]
    for rid,n,fl,q in rows:
        lines.append(f"{rid}. {n} ({fl}) ×{q}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}", callback_data=f"RM_{rid}"))
    kb.row(InlineKeyboardButton("❌ Очистить", callback_data="CLR_CART"),
           InlineKeyboardButton("✅ Оформить", callback_data="CHK_OUT"))
    await m.answer("Корзина:\n"+"\n".join(lines), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="CLR_CART", state=Mode.user)
async def clr_cart(cb):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.answer("Корзина очищена"); await basket(cb.message)

@dp.callback_query_handler(lambda c:c.data.startswith("RM_"), state=Mode.user)
async def rm_item(cb):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[3:]),)); db.commit()
    await cb.answer("Удалено"); await basket(cb.message)

# ─────────────── ЧЕК-АУТ ──────────────────────────────────────
@dp.callback_query_handler(lambda c:c.data=="CHK_OUT", state=Mode.user)
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
    text=", ".join(items)
    ts=datetime.datetime.now().strftime("%d.%m %H:%M")
    cur.execute("INSERT INTO orders(user_id,items,ts) VALUES(?,?,?)",(uid,text,ts))
    oid=cur.lastrowid
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,)); db.commit()
    for a in ADMINS: await bot.send_message(a,f"🆕 Заказ #{oid}\n{text}\nUID {uid}")
    await cb.message.answer(f"Заказ #{oid} принят!"); await cb.answer()

@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def my_orders(m):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Вы не сделали ещё ни одного заказа."); return
    await m.answer("\n\n".join(f"#{i} • {ts}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ─────────────── АДМИН: СКЛАД / ОСТАТОК ───────────────────────
@dp.message_handler(lambda m:m.text=="📦 Склад", state=Mode.admin)
async def stock(m):
    cur.execute("""SELECT p.id,p.name,f.id,f.flavor,f.qty
                   FROM products p JOIN flavors f ON f.product_id=p.id
                   ORDER BY p.id""")
    rows=cur.fetchall()
    if not rows: await m.answer("Склад пуст."); return
    msg="\n".join(f"{fid}. {n} – {fl}: {q}" for _,n,fid,fl,q in rows)
    await m.answer(msg)

@dp.message_handler(lambda m:m.text=="✏️ Остаток", state=Mode.admin)
async def edit_prompt(m):
    await m.answer("Отправьте ID вкуса и новое количество через пробел (пример `12 5`).")
    await StockEdit.fid.set()

@dp.message_handler(state=StockEdit.fid)
async def edit_qty(m,state:FSMContext):
    parts=m.text.split()
    if len(parts)!=2 or not (parts[0].isdigit() and parts[1].isdigit()):
        await m.answer("Формат: `ID  кол-во`"); return
    fid,qty=map(int,parts)
    cur.execute("UPDATE flavors SET qty=? WHERE id=?", (qty,fid)); db.commit()
    await m.answer("Обновлено."); await state.finish()

# ─────────────── АДМИН: ДОБАВЛЕНИЕ ────────────────────────────
@dp.message_handler(lambda m:m.text=="➕ Добавить", state=Mode.admin)
async def add_cat(m):            # шаг 1
    kb = InlineKeyboardMarkup()
    for c,t in CATS.items(): kb.add(InlineKeyboardButton(t,callback_data="AC_"+c))
    await m.answer("Категория:", reply_markup=kb); await Add.cat.set()

@dp.callback_query_handler(lambda c:c.data.startswith("AC_"), state=Add.cat)
async def add_name(cb,state:FSMContext):  # 2
    await state.update_data(cat=cb.data[3:]); await cb.answer()
    await cb.message.answer("Название:"); await Add.name.set()

@dp.message_handler(state=Add.name)       # 3
async def add_desc(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.desc.set()

@dp.message_handler(state=Add.desc)       # 4
async def add_cnt(m,state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0 — без вкуса)")
    await Add.cnt.set()

@dp.message_handler(state=Add.cnt)        # 5
async def add_cnt_ok(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Введите число."); return
    left=int(m.text); await state.update_data(left=left,fl=[],qy=[])
    if left==0:
        await m.answer("Количество товара:"); await Add.qty.set()
    else:
        await m.answer("Вкус №1:"); await Add.flav.set()

@dp.message_handler(state=Add.flav)       # 6
async def add_fl(m,state:FSMContext):
    await state.update_data(cur=m.text); await m.answer("Количество:"); await Add.qty.set()

@dp.message_handler(state=Add.qty)        # 7
async def add_qty(m,state:FSMContext):
    if not m.text.isdigit(): await m.answer("Введите число."); return
    q=int(m.text); d=await state.get_data()
    if d['left']==0:                      # простой товар без вкусов
        await finalize(d,m,"default",q); return
    # многовкусный
    d['fl'].append(d['cur']); d['qy'].append(q); d['left']-=1
    await state.update_data(**d)
    if d['left']==0: await finalize(d,m)
    else: await m.answer(f"Вкус №{len(d['fl'])+1}:"); await Add.flav.set()

def finalize(d,m,fl="default",q=0):
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",
                (d['name'],d['desc'],d['cat'])); pid=cur.lastrowid
    if d['left']==0 and fl=="default" and d['fl']==[]:  # одна позиция без вкуса
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,fl,q))
    else:
        for f,qt in zip(d['fl'],d['qy']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,f,qt))
    db.commit(); m.answer("Товар добавлен ✅", reply_markup=kb_admin)
    asyncio.create_task(dp.current_state().finish())

# ──────────────────────────────────────────────────────────────
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
