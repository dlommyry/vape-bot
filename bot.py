"""
Plumbus-Shop bot • v0.9.1  (июль 2025)

Функции:
• Каталог → товар → вкусы → количество → корзина → оплата TON – 7 % или «картой»
• Цена у каждого вкуса, расчёт итога
• Кешбэк 0 ,5 – 7 % + списание, реферальная скидка 300 ₽
• Админ-панель: добавить/удалить, склад, остаток, список заказов, статусы
• Автомиграция SQLite, логирование в Deploy Logs
"""

import os, logging, sqlite3, random, string, json, aiohttp
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    InlineKeyboardButton as IB,
    InlineKeyboardMarkup  as IM,
    ReplyKeyboardMarkup   as RM,
    KeyboardButton        as KB,
)
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor


# ─────────────── CONFIG & LOGS ────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN")
ADMIN_IDS   = {int(i) for i in os.getenv("ADMINS", "").replace(" ", "").split(",") if i}
WALLET_API  = os.getenv("WALLET_API_TOKEN")           # @wallet merchant token
CALLBACK_SECRET = os.getenv("TON_SECRET", "ton_secret")
BOT_USERNAME    = os.getenv("BOT_USERNAME", "PlumbusShopBot")

DB_PATH   = os.getenv("DB_PATH", "/data/vape_shop.db")

logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot, storage=MemoryStorage())


# ─────────────── DATABASE (SQLite + миграция) ─────────────────
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur  = conn.cursor()

def migrate():
    cur.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        cashback REAL DEFAULT 0,
        total_spent REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, description TEXT, category TEXT,
        created TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS flavours(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
        name TEXT, price REAL DEFAULT 0, stock INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS carts(
        user_id INTEGER,
        flavour_id INTEGER REFERENCES flavours(id) ON DELETE CASCADE,
        qty INTEGER,
        PRIMARY KEY(user_id, flavour_id)
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, created TEXT DEFAULT (datetime('now')),
        total REAL, pay_method TEXT, discount REAL DEFAULT 0,
        status TEXT
    );
    CREATE TABLE IF NOT EXISTS order_items(
        order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
        flavour_id INTEGER, qty INTEGER, price REAL
    );
    CREATE TABLE IF NOT EXISTS refs(
        code TEXT PRIMARY KEY,
        owner_id INTEGER,
        used_by_id INTEGER
    );
    """)
    conn.commit()
migrate()


# ─────────────── KEYBOARDS ───────────────────────────────────
CATS = ['Одноразовые системы', 'Многоразовые системы', 'Жидкости', 'Разное']

def kb_main(uid:int):
    kb = RM(resize_keyboard=True)
    kb.row("🛍 Каталог", "🧺 Корзина")
    kb.row("📦 Мой кешбэк", "📄 Мои заказы")
    kb.add("☎️ Поддержка")
    if uid in ADMIN_IDS: kb.add("🛠 Админ-панель")
    return kb

def kb_admin():
    kb = RM(resize_keyboard=True)
    kb.row("➕ Добавить", "✏️ Остаток")
    kb.row("📦 Склад", "❌ Удалить")
    kb.row("📃 Заказы", "↩️ Назад")
    return kb

def kb_cats():
    kb = IM()
    for c in CATS: kb.add(IB(c, callback_data=f"cat:{c}"))
    return kb

def cart_total(uid:int):
    cur.execute("""SELECT SUM(c.qty*f.price)
                   FROM carts c JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""",(uid,))
    return cur.fetchone()[0] or 0

def cashback_rate(spent):
    if spent < 10000:   return 0.005
    if spent < 15000:   return 0.01
    if spent < 25000:   return 0.02
    if spent < 35000:   return 0.04
    return 0.07

def rand_code(n=6):
    return ''.join(random.choices(string.ascii_uppercase+string.digits, k=n))


# ─────────────── TON-invoice helper ──────────────────────────
async def send_invoice_ton(uid:int, order_id:int, rub_amount:float):
    ton_amount = (rub_amount/100)*0.93         # 1 TON~100₽, −7 %
    params = {
        "amount": f"{ton_amount:.2f}",
        "currency_code": "TON",
        "description": f"Order #{order_id}",
        "callback_url": f"https://<YOUR_URL>/ton_paid?secret={CALLBACK_SECRET}&order={order_id}"
    }
    headers = {"Content-Type":"application/json","Authorization":f"Bearer {WALLET_API}"}
    async with aiohttp.ClientSession() as cs:
        async with cs.post("https://pay.wallet.tg/wpay/api/v1/createInvoice",
                           headers=headers, data=json.dumps(params)) as r:
            js=await r.json()
    url=js["invoice_url"]
    kb=IM().add(IB("Оплатить TON 🔗", url=url))
    await bot.send_message(uid, "Ссылка на оплату TON (−7 %)", reply_markup=kb)


# ─────────────── FSM для добавления товара ───────────────────
class Add(StatesGroup):
    cat=State(); name=State(); desc=State(); flav_cnt=State(); flav_loop=State()

def to_int(text): return text.isdigit() and int(text)


# ─────────────── Универсальный выход (/cancel, ↩️) ───────────
@dp.message_handler(commands="cancel", state="*")
@dp.message_handler(text="↩️ Назад", state="*")
async def any_cancel(m: types.Message, state: FSMContext):
    if await state.get_state():
        await state.finish()
    await m.answer("Главное меню.", reply_markup=kb_main(m.from_user.id))


# ─────────────── CLIENT HANDLERS ─────────────────────────────
@dp.message_handler(commands="start", state="*")
async def cmd_start(m: types.Message):
    # реф-код
    if m.get_args():
        code=m.get_args()
        if len(code)==6:
            cur.execute("SELECT owner_id FROM refs WHERE code=?",(code,))
            row=cur.fetchone()
            if row and row[0]!=m.from_user.id:
                cur.execute("""INSERT OR IGNORE INTO refs(code,owner_id,used_by_id)
                               VALUES(?,?,?)""",(code,row[0],m.from_user.id))
                conn.commit()
                await m.answer("Реф-код активирован! Скидка 300 ₽ на первый заказ.")
    cur.execute("INSERT OR IGNORE INTO users(id) VALUES(?)",(m.from_user.id,)); conn.commit()
    await m.answer("Добро пожаловать!", reply_markup=kb_main(m.from_user.id))

@dp.message_handler(text="🛍 Каталог", state="*")
async def catalog(m): await m.answer("Категории:", reply_markup=kb_cats())

@dp.callback_query_handler(lambda c:c.data.startswith("cat:"), state="*")
async def cat_open(c):
    cat=c.data[4:]
    cur.execute("SELECT id,name FROM products WHERE category=?",(cat,))
    rows=cur.fetchall()
    if not rows: return await c.answer("Категория пуста")
    kb=IM()
    for pid,name in rows: kb.add(IB(name,callback_data=f"prd:{pid}"))
    await c.message.answer(cat+":", reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("prd:"), state="*")
async def product_card(c):
    pid=int(c.data[4:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,name,price,stock FROM flavours WHERE product_id=? AND stock>0",(pid,))
    rows=cur.fetchall()
    if not rows: return await c.answer("Нет в наличии")
    kb=IM()
    for fid,fname,price,stock in rows:
        kb.add(IB(f"{fname} — {price}₽ ({stock})",callback_data=f"flv:{fid}"))
    await c.message.answer(f"<b>{name}</b>\n{desc}", reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("flv:"), state="*")
async def choose_qty(c):
    fid=int(c.data[4:])
    cur.execute("SELECT name,stock FROM flavours WHERE id=?", (fid,))
    fname,stock=cur.fetchone()
    kb=IM(row_width=5)
    for i in range(1, min(stock,10)+1):
        kb.insert(IB(str(i),callback_data=f"qty:{fid}:{i}"))
    await c.message.answer(f"Сколько «{fname}»?", reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("qty:"), state="*")
async def add_cart(c):
    _, fid, qty = c.data.split(":")
    fid, qty = int(fid), int(qty)
    cur.execute("""INSERT INTO carts(user_id,flavour_id,qty)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id,flavour_id) DO UPDATE SET qty=qty+excluded.qty""",
                (c.from_user.id, fid, qty)); conn.commit()
    await c.answer("Добавлено ✅", show_alert=True)

@dp.message_handler(text="🧺 Корзина", state="*")
async def cart_show(m):
    cur.execute("""SELECT f.name,c.qty,f.price
                   FROM carts c JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: return await m.answer("Корзина пуста.")
    total=sum(q*p for _,q,p in rows)
    txt="\n".join(f"{n} ×{q} = {q*p:.0f}₽" for n,q,p in rows)
    kb=IM(row_width=2)
    kb.add(IB("Ton −7 %", callback_data="pay:ton"),
           IB("Картой",    callback_data="pay:card"))
    kb.add(IB("Очистить", callback_data="cart:clr"))
    await m.answer(f"{txt}\n<b>Итого: {total}₽</b>", parse_mode='HTML', reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="cart:clr", state="*")
async def cart_clr(c):
    cur.execute("DELETE FROM carts WHERE user_id=?", (c.from_user.id,)); conn.commit()
    await c.answer("Корзина очищена"); await c.message.delete()

@dp.callback_query_handler(lambda c:c.data.startswith("pay:"), state="*")
async def checkout(c):
    method=c.data[4:]; uid=c.from_user.id
    cur.execute("""SELECT f.id,f.price,c.qty
                   FROM carts c JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""",(uid,))
    items=cur.fetchall()
    if not items: return await c.answer("Корзина пуста")
    total=sum(q*p for _,p,q in items); discount=0
    # TON скидка
    if method=="ton": discount+=round(total*0.07,2)
    # реф-скидка
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,))
    if cur.fetchone()[0]==0:
        cur.execute("SELECT owner_id FROM refs WHERE used_by_id=?", (uid,))
        if row:=cur.fetchone():
            discount+=300
            cur.execute("UPDATE users SET cashback=cashback+300 WHERE id=?", (row[0],))
    # кешбэк списание
    cur.execute("SELECT cashback FROM users WHERE id=?", (uid,)); cb=cur.fetchone()[0]
    if cb:
        use=min(cb,total-discount)
        discount+=use; cur.execute("UPDATE users SET cashback=cashback-? WHERE id=?", (use,uid))
    total_pay=max(0,total-discount)
    cur.execute("INSERT INTO orders(user_id,total,pay_method,discount,status) VALUES(?,?,?,?,?)",
                (uid,total,method,discount,"pending")); oid=cur.lastrowid
    cur.executemany("INSERT INTO order_items(order_id,flavour_id,qty,price) VALUES(?,?,?,?)",
                    [(oid,f,q,p) for f,p,q in items])
    for f,p,q in items: cur.execute("UPDATE flavours SET stock=stock-? WHERE id=?", (q,f))
    cur.execute("DELETE FROM carts WHERE user_id=?", (uid,))
    conn.commit()
    if method=="ton":
        await send_invoice_ton(uid, oid, total_pay)
    else:
        await bot.send_message(uid,"Менеджер свяжется для оплаты картой.")
    await c.answer("Заказ создан!")

@dp.message_handler(regexp="^📦 Мой кешбэк$", state="*")
async def my_cb(m):
    cur.execute("SELECT cashback FROM users WHERE id=?", (m.from_user.id,))
    cb=cur.fetchone()[0]
    await m.answer(f"Ваш кешбэк: {cb:.0f} ₽")

@dp.message_handler(text="📄 Мои заказы", state="*")
async def my_orders(m):
    cur.execute("""SELECT id,created,status,total,discount
                   FROM orders WHERE user_id=? ORDER BY id DESC""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: return await m.answer("У вас ещё нет заказов.")
    txt=[]
    for oid,dt,st,tot,disc in rows:
        txt.append(f"#{oid} • {dt[:16]} • {st}\nСумма: {tot-disc:.0f}₽ (скидка {disc:.0f})")
    await m.answer("\n\n".join(txt))

@dp.message_handler(text="☎️ Поддержка", state="*")
async def support(m): await m.answer("Контакт: @PlumbusSupport")


# ─────────────── ADMIN HANDLERS ──────────────────────────────
@dp.message_handler(text="🛠 Админ-панель", user_id=ADMIN_IDS, state="*")
async def admin_menu(m): await m.answer("Админ-панель:", reply_markup=kb_admin())

@dp.message_handler(text="📦 Склад", user_id=ADMIN_IDS, state="*")
async def warehouse(m):
    cur.execute("""SELECT f.id,p.name,f.name,f.stock,f.price
                   FROM flavours f JOIN products p ON p.id=f.product_id
                   ORDER BY p.name""")
    rows=cur.fetchall()
    if not rows: return await m.answer("Склад пуст.")
    txt="\n".join(f"{fid}. {pn} – {fn}: {stk} шт • {pr}₽" for fid,pn,fn,stk,pr in rows)
    await m.answer(txt)

@dp.message_handler(text="✏️ Остаток", user_id=ADMIN_IDS, state="*")
async def ask_edit(m,state:FSMContext):
    kb=RM(resize_keyboard=True).add("✖️ Отмена")
    await m.answer("ID_вкуса новое_кол-во",reply_markup=kb)
    await state.set_state("edit_stock")

@dp.message_handler(text="✖️ Отмена", state="edit_stock", user_id=ADMIN_IDS)
async def edit_cancel(m,state:FSMContext):
    await state.finish(); await m.answer("Отменено.", reply_markup=kb_admin())

@dp.message_handler(state="edit_stock", user_id=ADMIN_IDS)
async def do_edit(m,state:FSMContext):
    try: fid,new = map(int,m.text.split()); cur.execute("UPDATE flavours SET stock=? WHERE id=?", (new,fid)); conn.commit()
    except: return await m.answer("Формат: 12 50")
    await m.answer("Обновлено.", reply_markup=kb_admin()); await state.finish()

@dp.message_handler(text="❌ Удалить", user_id=ADMIN_IDS, state="*")
async def ask_del(m,state:FSMContext):
    await m.answer("ID продукта для удаления:"); await state.set_state("del_prod")

@dp.message_handler(state="del_prod", user_id=ADMIN_IDS)
async def do_del(m,state:FSMContext):
    if not to_int(m.text): return await m.answer("Число!")
    cur.execute("DELETE FROM products WHERE id=?", (int(m.text),)); conn.commit()
    await m.answer("Удалено.", reply_markup=kb_admin()); await state.finish()

@dp.message_handler(text="📃 Заказы", user_id=ADMIN_IDS, state="*")
async def list_orders(m):
    cur.execute("""SELECT id,user_id,status,total,discount
                   FROM orders ORDER BY id DESC LIMIT 20""")
    rows=cur.fetchall()
    if not rows: return await m.answer("Заказов нет.")
    kb=IM()
    for oid,uid,st,tot,disc in rows:
        kb.add(IB(f"#{oid} • {uid} • {st} • {tot-disc:.0f}₽", callback_data=f"ord:{oid}"))
    await m.answer("Заказы:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("ord:"), user_id=ADMIN_IDS, state="*")
async def ord_view(c):
    oid=int(c.data[4:])
    cur.execute("""SELECT o.user_id,o.created,o.status,o.total,o.discount,
                   f.name,oi.qty,oi.price
                   FROM orders o
                   JOIN order_items oi ON oi.order_id=o.id
                   JOIN flavours f ON f.id=oi.flavour_id
                   WHERE o.id=?""",(oid,))
    rows=cur.fetchall()
    if not rows: return await c.answer("Не найдено")
    uid,dt,st,tot,disc, *_ = rows[0]
    items="\n".join(f"{n} ×{q} = {q*p:.0f}₽" for *_,n,q,p in rows)
    txt=(f"<b>Заказ #{oid}</b> • {dt[:16]}\nПокупатель {uid}\n{items}\n"
         f"<b>Итого: {tot-disc:.0f}₽</b> (скидка {disc:.0f})\nСтатус: {st}")
    kb=IM(row_width=3)
    if st=="pending": kb.add(IB("Paid",   callback_data=f"set:{oid}:paid"))
    if st!="done":    kb.add(IB("Done",   callback_data=f"set:{oid}:done"))
    if st!="cancel":  kb.add(IB("Cancel", callback_data=f"set:{oid}:cancel"))
    await c.message.edit_text(txt, reply_markup=kb, parse_mode='HTML')
    await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("set:"), user_id=ADMIN_IDS, state="*")
async def ord_set(c):
    _, oid, new = c.data.split(":")
    cur.execute("UPDATE orders SET status=? WHERE id=?", (new,int(oid))); conn.commit()
    await c.answer("Обновлено")
    # перерисовать
    fake=types.CallbackQuery(id=c.id, from_user=c.from_user, data=f"ord:{oid}", message=c.message)
    await ord_view(fake)

# ─────────────── ADD PRODUCT (FSM) ───────────────────────────
@dp.message_handler(text="➕ Добавить", user_id=ADMIN_IDS, state="*")
async def add_cat(m,state:FSMContext):
    kb=RM(resize_keyboard=True).add(*CATS)
    await m.answer("Категория:", reply_markup=kb); await Add.cat.set()

@dp.message_handler(state=Add.cat, user_id=ADMIN_IDS)
async def add_name(m,state:FSMContext):
    if m.text not in CATS: return await m.answer("Кнопка!")
    await state.update_data(cat=m.text)
    await m.answer("Название:", reply_markup=types.ReplyKeyboardRemove()); await Add.next()

@dp.message_handler(state=Add.name, user_id=ADMIN_IDS)
async def add_desc(m,state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await Add.next()

@dp.message_handler(state=Add.desc, user_id=ADMIN_IDS)
async def add_cnt(m,state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Сколько вкусов? (0 — без вкуса)"); await Add.next()

@dp.message_handler(state=Add.flav_cnt, user_id=ADMIN_IDS)
async def cnt_ok(m,state:FSMContext):
    if not to_int(m.text): return await m.answer("Число!")
    total=int(m.text)
    await state.update_data(total=total,step=0,fl=[])
    if total==0:
        await save_prod(state,m)                     # простой товар
    else:
        await m.answer("Вкус №1:"); await Add.flav_loop.set()

@dp.message_handler(state=Add.flav_loop, user_id=ADMIN_IDS)
async def loop(m,state:FSMContext):
    d=await state.get_data(); step=d["step"]; fl=d["fl"]; total=d["total"]
    if step%2==0:                                   # ждём имя
        fl.append({"name":m.text}); d.update(fl=fl,step=step+1)
        await state.update_data(**d); await m.answer("Цена,₽:")
    elif step%2==1 and step//2 < len(fl):           # ждём price
        if not to_int(m.text): return await m.answer("Введите число.")
        fl[-1]["price"]=int(m.text); d.update(step=step+1)
        await state.update_data(**d); await m.answer("Количество:")
    else:                                           # ждём qty
        if not to_int(m.text): return await m.answer("Введите число.")
        fl[-1]["qty"]=int(m.text); d.update(step=step+1)
        await state.update_data(**d)
        if len(fl)==total: await save_prod(state,m)
        else: await m.answer(f"Вкус №{len(fl)+1}:")

async def save_prod(state,m):
    d=await state.get_data()
    cur.execute("INSERT INTO products(name,description,category) VALUES(?,?,?)",
                (d["name"],d["desc"],d["cat"]))
    pid=cur.lastrowid
    if d["total"]:
        cur.executemany("INSERT INTO flavours(product_id,name,price,stock) VALUES(?,?,?,?)",
                        [(pid,f["name"],f["price"],f["qty"]) for f in d["fl"]])
    conn.commit()
    await m.answer("✅ Добавлено", reply_markup=kb_admin()); await state.finish()

# ─────────────── DEBUG CALLBACKS ─────────────────────────────
if os.getenv("DEBUG"):
    @dp.callback_query_handler(lambda c:True, state="*")
    async def dbg(cb): logging.warning("CALLBACK %s", cb.data); await cb.answer()

# ─────────────── RUN LOOP ───────────────────────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
