"""
Plumbus-Shop bot • v0.9.0 (июль 2025)
Функции: каталог, корзина, цены, TON-оплата, карта, кешбэк, реферал, админ.
"""

import os, logging, sqlite3, random, string, json, aiohttp
from datetime import datetime
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.types import (InlineKeyboardButton as IB, InlineKeyboardMarkup as IM,
                           ReplyKeyboardMarkup as RM, KeyboardButton as KB)
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.utils import executor

# ─────────── CONFIG & LOGS ──────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN")
ADMIN_IDS   = {int(i) for i in os.getenv("ADMINS", "").replace(" ", "").split(",") if i}
WALLET_API  = os.getenv("WALLET_API_TOKEN")        # @wallet merchant token
CALLBACK_SECRET = os.getenv("TON_SECRET", "ton_secret")
BOT_USERNAME = os.getenv("BOT_USERNAME", "PlumbusShopBot")
DB_PATH = os.getenv("DB_PATH", "/data/vape_shop.db")

logging.basicConfig(
    level = logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot, storage=MemoryStorage())

# ─────────── DATABASE (auto-migrate) ───────────────────────────────
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
        name TEXT, description TEXT, category TEXT, created TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS flavours(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
        name TEXT, price REAL, stock INTEGER DEFAULT 0
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
        total REAL, pay_method TEXT, discount REAL, status TEXT
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
    # add missing columns dynamically
    need = [("flavours", "price", "REAL", "0"),
            ("orders",   "discount", "REAL", "0")]
    for table, col, typ, dft in need:
        cur.execute(f"PRAGMA table_info({table})")
        if col not in [r[1] for r in cur.fetchall()]:
            logging.warning("DB-migrate: %s.%s", table, col)
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {dft}")
    conn.commit()

migrate()

# ─────────── KEYBOARDS ─────────────────────────────────────────────
CATS = ["Одноразовые системы", "Многоразовые системы", "Жидкости", "Разное"]

def kb_main(uid):                 # клиент
    kb=RM(resize_keyboard=True)
    kb.row("🛍 Каталог","🧺 Корзина")
    kb.row("📦 Мой кешбэк","📄 Мои заказы")
    kb.add("☎️ Поддержка")
    if uid in ADMIN_IDS: kb.add("🛠 Админ-панель")
    return kb

def kb_admin():
    kb=RM(resize_keyboard=True)
    kb.row("➕ Добавить","✏️ Остаток")
    kb.row("📦 Склад","❌ Удалить")
    kb.row("📃 Заказы","↩️ Назад")
    return kb

def kb_cats():
    kb=IM()
    for c in CATS: kb.add(IB(c, callback_data=f"cat:{c}"))
    return kb

# ─────────── FSM: add product ─────────────────────────────────────
class Add(StatesGroup):
    cat=State(); name=State(); desc=State(); flav_cnt=State(); flav_loop=State()

# ─────────── HELPERS ──────────────────────────────────────────────
def cart_total(uid):
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

async def send_invoice_ton(uid, order_id, rub_amount):
    ton_rub = 0.93 * rub_amount / 100  # условно 1 TON = 100 ₽, -7 %
    params = {
        "amount": f"{ton_rub:.2f}",
        "currency_code": "TON",
        "description": f"Заказ #{order_id}",
        "callback_url": f"https://<YOUR_URL>/ton_paid?secret={CALLBACK_SECRET}&order={order_id}"
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {WALLET_API}"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://pay.wallet.tg/wpay/api/v1/createInvoice",
                             headers=headers, data=json.dumps(params)) as resp:
            js = await resp.json()
    pay_link = js["invoice_url"]
    kb = IM().add(IB("Оплатить Ton 🔗", url=pay_link))
    await bot.send_message(uid, f"Ссылка для оплаты TON (−7 %):", reply_markup=kb)

# ─────────── CLIENT HANDLERS ─────────────────────────────────────
@dp.message_handler(commands="start")
async def cmd_start(m: types.Message):
    # реф-код
    if m.get_args():
        code=m.get_args()
        if len(code)==6:
            cur.execute("SELECT owner_id FROM refs WHERE code=?",(code,))
            row=cur.fetchone()
            if row and not row[0]==m.from_user.id:
                cur.execute("INSERT OR IGNORE INTO refs(code,owner_id,used_by_id) VALUES(?,?,?)",
                            (code,row[0],m.from_user.id))
                conn.commit()
                await m.answer("Реф-код активирован! Скидка 300 ₽ на первый заказ.")
    cur.execute("INSERT OR IGNORE INTO users(id) VALUES(?)",(m.from_user.id,))
    conn.commit()
    await m.answer("Добро пожаловать!", reply_markup=kb_main(m.from_user.id))

@dp.message_handler(text="🛍 Каталог")
async def catalog(m): await m.answer("Категории:",reply_markup=kb_cats())

@dp.callback_query_handler(lambda c:c.data.startswith("cat:"))
async def cat_open(c):
    cat=c.data[4:]; cur.execute("""SELECT id,name FROM products WHERE category=?""",(cat,))
    rows=cur.fetchall()
    if not rows: return await c.answer("Пусто")
    kb=IM()
    for pid,name in rows: kb.add(IB(name,callback_data=f"prd:{pid}"))
    await c.message.answer(cat+":",reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("prd:"))
async def product_card(c):
    pid=int(c.data[4:]); cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,name,price,stock FROM flavours WHERE product_id=? AND stock>0",(pid,))
    rows=cur.fetchall()
    kb=IM()
    for fid,fname,price,stock in rows:
        kb.add(IB(f"{fname} — {price}₽ ({stock})",callback_data=f"flv:{fid}"))
    await c.message.answer(f"<b>{name}</b>\n{desc}", reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("flv:"))
async def choose_qty(c, state: FSMContext):
    fid=int(c.data[4:]); cur.execute("SELECT stock FROM flavours WHERE id=?", (fid,))
    stock=cur.fetchone()[0]
    kb=IM()
    for i in range(1, min(stock,10)+1):
        kb.insert(IB(str(i),callback_data=f"qty:{fid}:{i}"))
    await c.message.answer("Сколько штук?",reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("qty:"))
async def add_cart(c):
    _, fid, qty = c.data.split(":")
    fid, qty = int(fid), int(qty)
    uid=c.from_user.id
    cur.execute("""INSERT INTO carts(user_id,flavour_id,qty)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id,flavour_id) DO UPDATE SET qty=qty+excluded.qty""",
                (uid,fid,qty)); conn.commit()
    await c.answer("Добавлено ✅",show_alert=True)

@dp.message_handler(text="🧺 Корзина")
async def cart_show(m):
    cur.execute("""SELECT f.name,c.qty,f.price
                   FROM carts c JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: return await m.answer("Корзина пуста.")
    total = sum(q*pr for _,q,pr in rows)
    text = "\n".join(f"{n} ×{q} = {q*pr}₽" for n,q,pr in rows)
    kb=IM(row_width=2)
    kb.add(IB("Ton −7 %",callback_data="pay:ton"),
           IB("Картой",callback_data="pay:card"))
    kb.add(IB("Очистить",callback_data="cart:clr"))
    await m.answer(f"{text}\n<b>Итого: {total}₽</b>",
                   parse_mode='HTML', reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="cart:clr")
async def cart_clear(c):
    cur.execute("DELETE FROM carts WHERE user_id=?",(c.from_user.id,)); conn.commit()
    await c.answer("Корзина очищена"); await c.message.delete()

@dp.callback_query_handler(lambda c:c.data.startswith("pay:"))
async def checkout(c):
    method=c.data[4:]
    uid=c.from_user.id
    cur.execute("""SELECT f.id,f.price,c.qty FROM carts c
                   JOIN flavours f ON f.id=c.flavour_id
                   WHERE c.user_id=?""",(uid,))
    rows=cur.fetchall()
    if not rows:
        return await c.answer("Корзина пуста")
    total = sum(q*pr for _,pr,q in rows)
    # скидка TON 7 %
    discount = round(total*0.07,2) if method=="ton" else 0
    total_pay = total-discount
    # реф-скидка 300 при первом заказе
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,))
    if cur.fetchone()[0]==0:
        cur.execute("SELECT owner_id FROM refs WHERE used_by_id=?", (uid,))
        if row:=cur.fetchone():
            discount += 300
            total_pay = max(0,total_pay-300)
            # кешбэк к владельцу
            cur.execute("UPDATE users SET cashback=cashback+300 WHERE id=?", (row[0],))
    # кешбэк списание
    cur.execute("SELECT cashback FROM users WHERE id=?", (uid,))
    user_cb = cur.fetchone()[0]
    if user_cb:
        use = min(user_cb, total_pay)
        discount += use
        total_pay -= use
        cur.execute("UPDATE users SET cashback=cashback-? WHERE id=?", (use,uid))
    # создаём заказ
    cur.execute("""INSERT INTO orders(user_id,total,pay_method,discount,status)
                   VALUES(?,?,?,?,?)""",(uid,total,method,discount,"pending"))
    oid=cur.lastrowid
    cur.executemany("INSERT INTO order_items(order_id,flavour_id,qty,price) VALUES(?,?,?,?)",
                    [(oid,fid,q,pr) for fid,pr,q in rows])
    # уменьшаем stock
    for fid,pr,q in rows:
        cur.execute("UPDATE flavours SET stock=stock-? WHERE id=?", (q,fid))
    cur.execute("DELETE FROM carts WHERE user_id=?", (uid,))
    conn.commit()
    if method=="ton":
        await send_invoice_ton(uid, oid, total_pay)
    else:
        await bot.send_message(uid,"Менеджер скоро свяжется по оплате картой.")
    await c.answer("Заказ создан!")

@dp.message_handler(text="📦 Мой кешбэк")
async def my_cb(m):
    cur.execute("SELECT cashback FROM users WHERE id=?", (m.from_user.id,))
    cb=cur.fetchone()[0]
    await m.answer(f"Ваш кешбэк: {cb:.0f}₽")

@dp.message_handler(text="📄 Мои заказы")
async def my_orders(m):
    cur.execute("SELECT id,created,status,total,discount FROM orders WHERE user_id=?", (m.from_user.id,))
    rows=cur.fetchall()
    if not rows: return await m.answer("Нет заказов.")
    txt=[]
    for oid,dt,st,tot,disc in rows:
        txt.append(f"#{oid} • {dt[:16]} • {st}\n{tot-disc:.0f}₽ (скидка {disc:.0f})")
    await m.answer("\n\n".join(txt))

# ─────────── ADMIN BLOCK ─────────────────────────────────────────
@dp.message_handler(text="🛠 Админ-панель", user_id=ADMIN_IDS)
async def admin_panel(m): await m.answer("Админ-панель:",reply_markup=kb_admin())

@dp.message_handler(text="↩️ Назад", user_id=ADMIN_IDS)
async def back_user(m): await m.answer("Клиентский режим.", reply_markup=kb_main(m.from_user.id))

@dp.message_handler(text="📦 Склад", user_id=ADMIN_IDS)
async def stock(m):
    cur.execute("""SELECT p.id,p.name,f.name,f.stock FROM flavours f
                   JOIN products p ON p.id=f.product_id ORDER BY p.id""")
    rows=cur.fetchall()
    txt="\n".join(f"{fid}. {p} – {fl}: {st}" for fid,p,fl,st in rows) or "Склад пуст."
    await m.answer(txt)

@dp.message_handler(text="✏️ Остаток", user_id=ADMIN_IDS)
async def ask_edit(m,state:FSMContext):
    await m.answer("ID_вкуса новое_кол-во"); await state.set_state("edit_stock")

@dp.message_handler(state="edit_stock")
async def do_edit(m,state:FSMContext):
    try: fid,new = map(int,m.text.split()); cur.execute("UPDATE flavours SET stock=? WHERE id=?", (new,fid)); conn.commit()
    except: return await m.answer("Формат: 12 50")
    await m.answer("Готово."); await state.finish()

@dp.message_handler(text="❌ Удалить", user_id=ADMIN_IDS)
async def ask_del(m,state:FSMContext):
    await m.answer("ID продукта для удаления:"); await state.set_state("del_prod")

@dp.message_handler(state="del_prod")
async def do_del(m,state:FSMContext):
    if not to_int(m.text): return await m.answer("Число!")
    cur.execute("DELETE FROM products WHERE id=?", (int(m.text),)); conn.commit()
    await m.answer("Удалено."); await state.finish()

@dp.message_handler(text="📃 Заказы", user_id=ADMIN_IDS)
async def list_orders(m):
    cur.execute("""SELECT id,user_id,created,status,total,discount
                   FROM orders ORDER BY id DESC LIMIT 20""")
    rows=cur.fetchall()
    if not rows: return await m.answer("Нет заказов.")
    kb=IM()
    for oid,uid,_,st,tot,disc in rows:
        kb.add(IB(f"#{oid} ({st}) {tot-disc:.0f}₽",callback_data=f"ord:{oid}"))
    await m.answer("Заказы:",reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("ord:"), user_id=ADMIN_IDS)
async def order_info(c):
    oid=int(c.data[4:])
    cur.execute("""SELECT o.id,o.user_id,o.created,o.status,o.total,o.discount,
                   f.name,oi.qty,oi.price
                   FROM orders o
                   JOIN order_items oi ON oi.order_id=o.id
                   JOIN flavours f ON f.id=oi.flavour_id
                   WHERE o.id=?""",(oid,))
    rows=cur.fetchall()
    if not rows: return await c.answer("Не найдено")
    oid,uid,dt,st,tot,disc,*_ = rows[0]
    items="\n".join(f"{n} ×{q} = {q*p:.0f}" for _,_,_,_,_,_,n,q,p in rows)
    txt=f"<b>Заказ #{oid}</b> • {dt[:16]}\nПокупатель {uid}\n{items}\nИтого: {tot-disc:.0f}₽ (-{disc:.0f})\nСтатус: {st}"
    kb=IM(row_width=2)
    if st=="pending": kb.add(IB("Paid",callback_data=f"set:{oid}:paid"))
    if st!="done":    kb.add(IB("Done",callback_data=f"set:{oid}:done"))
    kb.add(IB("Cancel",callback_data=f"set:{oid}:cancel"))
    await c.message.answer(txt,parse_mode='HTML',reply_markup=kb); await c.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("set:"), user_id=ADMIN_IDS)
async def order_set(c):
    _,oid,new = c.data.split(":")
    cur.execute("UPDATE orders SET status=? WHERE id=?", (new,int(oid))); conn.commit()
    await c.answer("Обновлено")

# ─────────── CALLBACK DEBUG ─────────────────────────────────────
if os.getenv("DEBUG"):
    @dp.callback_query_handler(lambda c:True)
    async def dbg(cb): logging.warning("CB %s", cb.data); await cb.answer()

# ─────────── RUN ────────────────────────────────────────────────
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
