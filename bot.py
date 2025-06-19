import logging, sqlite3, datetime, pathlib
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS

# ───────── базовая настройка
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

DB = "vape_shop.db"
if not pathlib.Path(DB).exists():
    raise SystemExit("База не найдена. Сначала запусти migrate_once.py")

db  = sqlite3.connect(DB)
cur = db.cursor()

# ───────── клавиатуры
user_kb  = ReplyKeyboardMarkup(resize_keyboard=True)
user_kb.row("🛍 Каталог", "🧺 Корзина")
user_kb.row("📞 Поддержка", "📜 Мои заказы")

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.row("➕ Добавить", "❌ Удалить")
admin_kb.row("✏️ Остаток", "📦 Склад", "📑 Заказы")
admin_kb.add("🔄 Сменить режим")                     # только эта «служебная» кнопка

# ───────── FSM
class Mode(StatesGroup):
    user=State(); admin=State()

class AddFSM(StatesGroup):
    name=State(); desc=State(); flav_left=State(); flavor=State(); qty=State()

class EditFSM(StatesGroup):
    fid=State(); qty=State()

class QtyFSM(StatesGroup):
    waiting=State()

# ───────── /start
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    if str(m.from_user.id) in ADMINS:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb)
        await Mode.user.set()
    else:
        await m.answer("Добро пожаловать!", reply_markup=user_kb)
        await Mode.user.set()

# ───────── переключатель режима
@dp.message_handler(lambda m:m.text=="🔄 Сменить режим" and str(m.from_user.id) in ADMINS, state="*")
async def switch(m: types.Message, state:FSMContext):
    if await state.get_state()==Mode.user.state:
        await m.answer("🔧 Админ-панель.", reply_markup=admin_kb)
        await Mode.admin.set()
    else:
        await m.answer("🛒 Клиентский режим.", reply_markup=user_kb)
        await Mode.user.set()

# ───────── пользователь: поддержка
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def sup(m: types.Message): await m.answer("Связь: @PlumbusSupport")

# ───────── каталог
@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def catalog(m: types.Message):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT p.id,p.name,SUM(f.qty)
                   FROM products p JOIN flavors f ON f.product_id=p.id
                   GROUP BY p.id""")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})",callback_data=f"s:{pid}"))
    await m.answer("Каталог:", reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("s:"), state=Mode.user)
async def show_flavors(cb: types.CallbackQuery):
    pid=int(cb.data[2:])
    cur.execute("SELECT name,description FROM products WHERE id=?", (pid,))
    name,desc=cur.fetchone()
    cur.execute("SELECT id,flavor,qty FROM flavors WHERE product_id=?", (pid,))
    kb=InlineKeyboardMarkup()
    for fid,flv,qty in cur.fetchall():
        label=f"{flv} ({qty})"
        if qty>0: kb.add(InlineKeyboardButton(label,callback_data=f"f:{fid}"))
        else:     kb.add(InlineKeyboardButton(label+" ❌",callback_data=f"w:{fid}"))
    await cb.message.answer(f"*{name}*\n{desc}", parse_mode="Markdown", reply_markup=kb)
    await cb.answer()

# ───────── выбор количества
@dp.callback_query_handler(lambda c:c.data.startswith("f:"), state=Mode.user)
async def ask_qty(cb: types.CallbackQuery, state:FSMContext):
    fid=int(cb.data[2:]); await state.update_data(fid=fid)
    kb=InlineKeyboardMarkup()
    for i in range(1,11): kb.add(InlineKeyboardButton(str(i),callback_data=f"q:{i}"))
    await cb.message.answer("Сколько штук?", reply_markup=kb)
    await QtyFSM.waiting.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("q:"), state=QtyFSM.waiting)
async def add_cart(cb: types.CallbackQuery, state:FSMContext):
    qty=int(cb.data[2:]); fid=(await state.get_data())['fid']
    cur.execute("INSERT INTO cart(user_id,flavor_id,qty) VALUES(?,?,?)",
                (cb.from_user.id,fid,qty)); db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish(); await cb.answer()

# ───────── wait-лист
@dp.callback_query_handler(lambda c:c.data.startswith("w:"), state=Mode.user)
async def wlist(cb: types.CallbackQuery):
    fid=int(cb.data[2:])
    cur.execute("INSERT INTO waitlist VALUES(?,?)",(cb.from_user.id,fid)); db.commit()
    await cb.answer("Сообщу, когда появится")

# ───────── корзина
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def basket(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                   JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Корзина пуста."); return
    kb=InlineKeyboardMarkup()
    text=[]
    for rid,name,flv,qt in rows:
        text.append(f"{rid}. {name} ({flv}) ×{qt}")
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"d:{rid}"))
    kb.add(InlineKeyboardButton("❌ Очистить",callback_data="clr"),
           InlineKeyboardButton("✅ Оформить",callback_data="ok"))
    await m.answer("Корзина:\n"+"\n".join(text), reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="clr", state=Mode.user)
async def clr(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("d:"), state=Mode.user)
async def d_one(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data[2:]),)); db.commit()
    await cb.answer("Удалено"); await basket(cb.message)

# ───────── чек-аут
async def notify_wait(fid,newq):
    if newq<=0: return
    cur.execute("SELECT user_id FROM waitlist WHERE flavor_id=?", (fid,))
    uids=[u for (u,) in cur.fetchall()]
    if not uids: return
    cur.execute("""SELECT products.name,flavors.flavor
                   FROM flavors JOIN products ON products.id=flavors.product_id
                   WHERE flavors.id=?""",(fid,))
    name,flv=cur.fetchone()
    for u in uids:
        try: await bot.send_message(u,f"🔔 *{name}* ({flv}) снова в наличии!", parse_mode="Markdown")
        except: pass
    cur.execute("DELETE FROM waitlist WHERE flavor_id=?", (fid,)); db.commit()

@dp.callback_query_handler(lambda c:c.data=="ok", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    uid=cb.from_user.id
    cur.execute("""SELECT flavors.id,products.name,flavors.flavor,cart.qty
                   FROM cart JOIN flavors ON flavors.id=cart.flavor_id
                   JOIN products ON products.id=flavors.product_id
                   WHERE cart.user_id=?""",(uid,))
    rows=cur.fetchall()
    if not rows: await cb.answer("Пусто"); return
    items=[]
    for fid,name,flv,qt in rows:
        cur.execute("UPDATE flavors SET qty=qty-? WHERE id=?", (qt,fid))
        items.append(f"{name} ({flv})×{qt}")
    txt=", ".join(items); ts=datetime.datetime.now().isoformat(timespec='minutes')
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",(uid,txt,ts,"new"))
    oid=cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,)); db.commit()
    for adm in ADMINS:
        await bot.send_message(adm,f"🆕 Заказ #{oid}\n{txt}\nUID {uid}")
    await cb.message.edit_text(f"Заказ #{oid} принят!"); await cb.answer()

# ───────── мои заказы
@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def my(m: types.Message):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Нет заказов."); return
    await m.answer("\n\n".join(f"#{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows))

# ─────────────────────── АДМИН
# ➕ добавить товар
@dp.message_handler(lambda m:m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_start(m: types.Message): await m.answer("Название:"); await AddFSM.name.set()
@dp.message_handler(state=AddFSM.name)
async def add_desc(m: types.Message, state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await AddFSM.desc.set()
@dp.message_handler(state=AddFSM.desc)
async def add_nflav(m: types.Message, state:FSMContext):
    await state.update_data(desc=m.text)
    await m.answer("Сколько вкусов? (0 — без вкуса)"); await AddFSM.flav_left.set()
@dp.message_handler(state=AddFSM.flav_left)
async def add_loop(m: types.Message, state:FSMContext):
    try:n=int(m.text)
    except: await m.answer("Число."); return
    await state.update_data(fl_left=n, flavors=[], qtys=[])
    if n==0:
        await add_save(state, m, default_qty=True)
    else:
        await m.answer("Название вкуса:"); await AddFSM.flavor.set()

@dp.message_handler(state=AddFSM.flavor)
async def add_flv_name(m: types.Message, state:FSMContext):
    data=await state.get_data()
    data['curr_flavor']=m.text
    await state.update_data(**data)
    await m.answer("Количество:"); await AddFSM.qty.set()

@dp.message_handler(state=AddFSM.qty)
async def add_flv_qty(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число."); return
    data=await state.get_data()
    data['flavors'].append(data['curr_flavor'])
    data['qtys'].append(q); data['fl_left']-=1
    await state.update_data(**data)
    if data['fl_left']==0:
        await add_save(state, m)
    else:
        await m.answer("Название следующего вкуса:"); await AddFSM.flavor.set()

async def add_save(state:FSMContext, m: types.Message, default_qty=False):
    data=await state.get_data()
    cur.execute("INSERT INTO products(name,description) VALUES(?,?)",(data['name'],data['desc']))
    pid=cur.lastrowid
    if default_qty:
        cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,"default",0))
    else:
        for flv,qt in zip(data['flavors'],data['qtys']):
            cur.execute("INSERT INTO flavors(product_id,flavor,qty) VALUES(?,?,?)",(pid,flv,qt))
    db.commit()
    await m.answer("Товар добавлен.", reply_markup=admin_kb); await state.finish()

# ✏️ остаток
@dp.message_handler(lambda m:m.text=="✏️ Остаток" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def choose(m: types.Message):
    kb=InlineKeyboardMarkup()
    cur.execute("""SELECT flavors.id,products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    for fid,pn,fl,qt in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{pn}/{fl} ({qt})",callback_data=f"e:{fid}"))
    await m.answer("Выбери:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("e:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def ask_q(cb: types.CallbackQuery, state:FSMContext):
    fid=int(cb.data[2:]); await state.update_data(fid=fid)
    await cb.message.answer("Новое количество:"); await EditFSM.qty.set(); await cb.answer()
@dp.message_handler(state=EditFSM.qty)
async def save_q(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число."); return
    fid=(await state.get_data())['fid']
    cur.execute("UPDATE flavors SET qty=? WHERE id=?", (q,fid)); db.commit()
    await notify_wait(fid,q)
    await m.answer("Обновлено.",reply_markup=admin_kb); await state.finish()

# 📦 склад
@dp.message_handler(lambda m:m.text=="📦 Склад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def skl(m: types.Message):
    cur.execute("""SELECT products.name,flavors.flavor,flavors.qty
                   FROM flavors JOIN products ON products.id=flavors.product_id""")
    await m.answer("\n".join(f"{n}/{f}: {q}" for n,f,q in cur.fetchall()) or "Пусто.")

# ❌ удалить товар
@dp.message_handler(lambda m:m.text=="❌ Удалить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def del_choose(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"d:{pid}"))
    await m.answer("Удалить целиком:", reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("d:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def del_ok(cb: types.CallbackQuery):
    pid=int(cb.data[2:]); cur.execute("DELETE FROM products WHERE id=?", (pid,)); cur.execute("DELETE FROM flavors WHERE product_id=?", (pid,)); db.commit()
    await cb.message.answer("Удалено."); await cb.answer()

# 📑 заказы
@dp.message_handler(lambda m:m.text=="📑 Заказы" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def adm_orders(m: types.Message):
    cur.execute("SELECT id,user_id,ts,status FROM orders ORDER BY id DESC LIMIT 20")
    rows=cur.fetchall()
    if not rows: await m.answer("Пусто."); return
    kb=InlineKeyboardMarkup()
    txt=[]
    for oid,uid,ts,st in rows:
        txt.append(f"#{oid} • {ts[:16]} • {st} • UID {uid}")
        if st!="done": kb.add(InlineKeyboardButton(f"✅ {oid}",callback_data=f"o:{oid}"))
    await m.answer("\n".join(txt), reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("o:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def done(cb: types.CallbackQuery):
    oid=int(cb.data[2:]); cur.execute("UPDATE orders SET status='done' WHERE id=?", (oid,)); db.commit()
    await cb.answer("Закрыто."); await adm_orders(cb.message)

# ───────── запуск
if __name__=="__main__":
    logging.info("Bot started")
    executor.start_polling(dp, skip_updates=True)
