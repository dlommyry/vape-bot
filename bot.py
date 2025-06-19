import logging, sqlite3, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS      # ← переменные идут из Railway

# ─────────── базовая настройка
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ─────────── базы
db  = sqlite3.connect('vape_shop.db')
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products  (id INTEGER PRIMARY KEY,name TEXT,description TEXT,quantity INTEGER);
CREATE TABLE IF NOT EXISTS cart      (user_id INTEGER, product_id INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS waitlist  (user_id INTEGER, product_id INTEGER);
CREATE TABLE IF NOT EXISTS orders    (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER, items TEXT, ts TEXT, status TEXT);
""")
db.commit()

# ─────────── клавиатуры
user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
user_kb.add("🛍 Каталог","🧺 Корзина").add("📞 Поддержка").add("📜 Мои заказы").add("⬅️ Назад")

admin_kb = ReplyKeyboardMarkup(resize_keyboard=True)
admin_kb.add("➕ Добавить","❌ Удалить").add("✏️ Остаток","📦 Склад") \
        .add("📑 Заказы").add("⬅️ Назад")

switch_kb = ReplyKeyboardMarkup(resize_keyboard=True)
switch_kb.add("🛒 Пользовательский режим","🔧 Админ-панель")

# ─────────── FSM
class Mode(StatesGroup):
    user=State(); admin=State()
class Add(StatesGroup):
    name=State(); desc=State(); qty=State()
class EditQty(StatesGroup):
    pid=State(); qty=State()
class QtySel(StatesGroup):
    waiting=State()

# ─────────── /start и переключатель
@dp.message_handler(commands="start", state="*")
async def start(m: types.Message, state:FSMContext):
    if str(m.from_user.id) in ADMINS:
        await m.answer("Выберите режим:",reply_markup=switch_kb); await state.finish()
    else:
        await m.answer("Добро пожаловать!",reply_markup=user_kb); await Mode.user.set()

@dp.message_handler(lambda m:m.text=="🔧 Админ-панель", state="*")
async def to_admin(m: types.Message): 
    if str(m.from_user.id) not in ADMINS: return
    await m.answer("🔧 Режим администратора.",reply_markup=admin_kb); await Mode.admin.set()

@dp.message_handler(lambda m:m.text=="🛒 Пользовательский режим", state="*")
async def to_user(m: types.Message):
    await m.answer("🛒 Режим покупателя.",reply_markup=user_kb); await Mode.user.set()

# ─────────── пользовательские функции
@dp.message_handler(lambda m:m.text=="📞 Поддержка", state=Mode.user)
async def support(m: types.Message): await m.answer("Связь: @PlumbusSupport")

@dp.message_handler(lambda m:m.text=="🛍 Каталог", state=Mode.user)
async def catalog(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name,quantity FROM products")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})",callback_data=f"view:{pid}"))
    await m.answer("Каталог:",reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("view:"), state=Mode.user)
async def view(cb: types.CallbackQuery):
    pid=int(cb.data.split(":")[1])
    cur.execute("SELECT name,description,quantity FROM products WHERE id=?", (pid,))
    name,desc,qty=cur.fetchone()
    kb=InlineKeyboardMarkup(); 
    if qty>0: kb.add(InlineKeyboardButton("🛒 В корзину",callback_data=f"add:{pid}"))
    else:     kb.add(InlineKeyboardButton("🔔 Ждать",callback_data=f"wait:{pid}"))
    await cb.message.answer(f"*{name}*\n{desc}\nОстаток: {qty}",parse_mode="Markdown",reply_markup=kb)
    await cb.answer()

# выбор количества
@dp.callback_query_handler(lambda c:c.data.startswith("add:"), state=Mode.user)
async def choose_qty(cb: types.CallbackQuery, state:FSMContext):
    pid=int(cb.data.split(":")[1]); await state.update_data(pid=pid)
    kb=InlineKeyboardMarkup(); [kb.add(InlineKeyboardButton(str(i),callback_data=f"q:{i}")) for i in range(1,11)]
    await cb.message.answer("Сколько штук?",reply_markup=kb); await QtySel.waiting.set(); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("q:"), state=QtySel.waiting)
async def save_qty(cb: types.CallbackQuery, state:FSMContext):
    qty=int(cb.data.split(":")[1]); pid=(await state.get_data())['pid']
    cur.execute("INSERT INTO cart VALUES(?,?,?)",(cb.from_user.id,pid,qty)); db.commit()
    await cb.message.answer("Добавлено ✅"); await state.finish(); await cb.answer()

# wait-лист
@dp.callback_query_handler(lambda c:c.data.startswith("wait:"), state=Mode.user)
async def add_wait(cb: types.CallbackQuery):
    pid=int(cb.data.split(":")[1])
    cur.execute("INSERT INTO waitlist VALUES (?,?)",(cb.from_user.id,pid)); db.commit()
    await cb.answer("Сообщу, как появится!")

# корзина и оформление
@dp.message_handler(lambda m:m.text=="🧺 Корзина", state=Mode.user)
async def cart(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,cart.qty
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("Корзина пуста."); return
    kb=InlineKeyboardMarkup()
    txt="\n".join(f"{rid}. {n}×{q}" for rid,n,q in rows)
    for rid,_,_ in rows: kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"del:{rid}"))
    kb.add(InlineKeyboardButton("❌ Очистить",callback_data="clr"),
           InlineKeyboardButton("✅ Оформить",callback_data="checkout"))
    await m.answer("Корзина:\n"+txt,reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data=="clr", state=Mode.user)
async def clear_cart(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    await cb.message.edit_text("Корзина очищена."); await cb.answer()

@dp.callback_query_handler(lambda c:c.data.startswith("del:"), state=Mode.user)
async def del_item(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE rowid=?", (int(cb.data.split(':')[1]),)); db.commit()
    await cb.answer("Удалено"); await cart(cb.message)

@dp.callback_query_handler(lambda c:c.data=="checkout", state=Mode.user)
async def checkout(cb: types.CallbackQuery):
    cur.execute("""SELECT products.name,cart.qty FROM cart
                   JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(cb.from_user.id,))
    items=cur.fetchall()
    if not items: await cb.answer("Пусто"); return
    text=", ".join(f"{n}×{q}" for n,q in items)
    cur.execute("INSERT INTO orders(user_id,items,ts,status) VALUES(?,?,?,?)",
                (cb.from_user.id,text,datetime.datetime.now().isoformat(),"new"))
    oid=cur.lastrowid; db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,)); db.commit()
    # уведомление админам
    for adm in ADMINS:
        await bot.send_message(adm,f"🆕 Заказ #{oid}\n{text}\nОт: {cb.from_user.id}")
    await cb.message.edit_text(f"Заказ #{oid} принят! Менеджер свяжется."); await cb.answer()

# история заказов
@dp.message_handler(lambda m:m.text=="📜 Мои заказы", state=Mode.user)
async def my_orders(m: types.Message):
    cur.execute("SELECT id,ts,items,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (m.from_user.id,))
    rows=cur.fetchall()
    if not rows: await m.answer("У вас пока нет заказов."); return
    txt="\n\n".join(f"№{i} • {ts[:16]}\n{it}\nСтатус: {st}" for i,ts,it,st in rows)
    await m.answer(txt)

# ─────────── уведомление waitlist при пополнении склада
async def notify_waitlist(pid:int, new_qty:int):
    if new_qty<=0: return
    cur.execute("SELECT user_id FROM waitlist WHERE product_id=?", (pid,))
    users=[u for u,*_ in cur.fetchall()]
    if not users: return
    cur.execute("SELECT name FROM products WHERE id=?", (pid,)); name=cur.fetchone()[0]
    for uid in users:
        try: await bot.send_message(uid,f"🔔 *{name}* снова в наличии!",parse_mode="Markdown")
        except: pass
    cur.execute("DELETE FROM waitlist WHERE product_id=?", (pid,)); db.commit()

# ─────────── админ-функции
@dp.message_handler(lambda m:m.text=="📦 Склад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def stock(m: types.Message):
    cur.execute("SELECT id,name,quantity FROM products")
    await m.answer("\n".join(f"{pid}. {n} — {q}" for pid,n,q in cur.fetchall()) or "Пусто.")

# ➕ Добавить
class AddFSM(StatesGroup): name=State(); desc=State(); qty=State()
@dp.message_handler(lambda m:m.text=="➕ Добавить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def add_1(m: types.Message): await m.answer("Название:"); await AddFSM.name.set()
@dp.message_handler(state=AddFSM.name)
async def add_2(m: types.Message, state:FSMContext):
    await state.update_data(name=m.text); await m.answer("Описание:"); await AddFSM.desc.set()
@dp.message_handler(state=AddFSM.desc)
async def add_3(m: types.Message, state:FSMContext):
    await state.update_data(desc=m.text); await m.answer("Количество:"); await AddFSM.qty.set()
@dp.message_handler(state=AddFSM.qty)
async def add_save(m: types.Message, state:FSMContext):
    try:q=int(m.text)
    except: await m.answer("Число!"); return
    d=await state.get_data()
    cur.execute("INSERT INTO products(name,description,quantity) VALUES(?,?,?)",(d['name'],d['desc'],q)); db.commit()
    await m.answer("Добавлено.",reply_markup=admin_kb); await state.finish()

# ✏️ Остаток
class EditFSM(StatesGroup): pid=State(); qty=State()
@dp.message_handler(lambda m:m.text=="✏️ Остаток" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def edit_1(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"chg:{pid}"))
    await m.answer("Выбери:",reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("chg:"), state=Mode.admin)
async def edit_2(cb: types.CallbackQuery, state:FSMContext):
    pid=int(cb.data.split(":")[1]); await state.update_data(pid=pid)
    await cb.message.answer("Новое количество:" ); await EditFSM.qty.set(); await cb.answer()
@dp.message_handler(state=EditFSM.qty)
async def edit_save(m: types.Message, state:FSMContext):
    try: q=int(m.text)
    except: await m.answer("Число!"); return
    pid=(await state.get_data())['pid']
    cur.execute("UPDATE products SET quantity=? WHERE id=?", (q,pid)); db.commit()
    # оповещение waitlist
    await notify_waitlist(pid, q)
    await m.answer("Остаток обновлён.",reply_markup=admin_kb); await state.finish()

# ❌ Удалить
@dp.message_handler(lambda m:m.text=="❌ Удалить" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def delete_start(m: types.Message):
    kb=InlineKeyboardMarkup(); cur.execute("SELECT id,name FROM products")
    for pid,name in cur.fetchall(): kb.add(InlineKeyboardButton(name,callback_data=f"del:{pid}"))
    await m.answer("Удалить:",reply_markup=kb)
@dp.callback_query_handler(lambda c:c.data.startswith("del:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def delete_exec(cb: types.CallbackQuery):
    pid=int(cb.data.split(":")[1])
    cur.execute("DELETE FROM products WHERE id=?", (pid,)); db.commit()
    await cb.message.answer("Удалён."); await cb.answer()

# 📑 Заказы
@dp.message_handler(lambda m:m.text=="📑 Заказы" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def orders_list(m: types.Message):
    cur.execute("SELECT id,user_id,ts,items,status FROM orders ORDER BY id DESC LIMIT 15")
    rows=cur.fetchall()
    if not rows: await m.answer("Заказов нет."); return
    kb=InlineKeyboardMarkup()
    txt=[]
    for oid,uid,ts,it,st in rows:
        txt.append(f"#{oid} • {ts[:16]} • {st}")
        if st!="done":
            kb.add(InlineKeyboardButton(f"✅ {oid}",callback_data=f"done:{oid}"))
    await m.answer("\n".join(txt),reply_markup=kb)

@dp.callback_query_handler(lambda c:c.data.startswith("done:") and str(c.from_user.id) in ADMINS, state=Mode.admin)
async def order_done(cb: types.CallbackQuery):
    oid=int(cb.data.split(":")[1])
    cur.execute("UPDATE orders SET status='done' WHERE id=?", (oid,)); db.commit()
    await cb.answer("Закрыто."); await orders_list(cb.message)

# кнопка назад в admin
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.admin)
async def back_admin(m: types.Message):
    await m.answer("Админ-меню",reply_markup=admin_kb)

# назад из user-режима
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS, state=Mode.user)
async def back_to_admin(m: types.Message):
    await m.answer("🔧 Админ-панель",reply_markup=admin_kb); await Mode.admin.set()

# ─────────── запуск
if __name__=="__main__":
    executor.start_polling(dp, skip_updates=True)
