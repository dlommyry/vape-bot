# bot.py  – версия с постоянной корзиной
import logging, os, sqlite3, itertools
from aiogram import Bot, Dispatcher, types
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from config import BOT_TOKEN, ADMINS

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ───────────────────  БД
db = sqlite3.connect("vape_shop.db")
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,description TEXT,quantity INTEGER);
CREATE TABLE IF NOT EXISTS cart(user_id INTEGER, product_id INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER, items TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP);
""")
db.commit()

# ───────────────────  Клавиатуры
main_kb  = ReplyKeyboardMarkup(resize_keyboard=True).row("🛍 Каталог","🧺 Корзина").add("📞 Поддержка")
admin_kb = ReplyKeyboardMarkup(resize_keyboard=True).row("➕ Добавить товар","❌ Удалить товар")\
                                                   .row("✏️ Изменить остаток","📦 Остатки").add("⬅️ Назад")

# ───────────────────  FSM для добавления количества
class ChooseQty(StatesGroup):
    waiting = State()

# ───────────────────  start
@dp.message_handler(commands="start")
async def start(m: types.Message):
    kb = admin_kb if str(m.from_user.id) in ADMINS else main_kb
    await m.answer("Админ-панель открыта:" if kb==admin_kb else "Добро пожаловать!",
                   reply_markup=kb)

# ───────────────────  каталог
@dp.message_handler(lambda m: m.text=="🛍 Каталог")
async def catalog(m: types.Message):
    kb = InlineKeyboardMarkup()
    cur.execute("SELECT id,name,quantity FROM products")
    for pid,name,qty in cur.fetchall():
        kb.add(InlineKeyboardButton(f"{name} ({qty})",callback_data=f"view:{pid}"))
    await m.answer("Каталог:",reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("view:"))
async def view(cb: types.CallbackQuery):
    pid=int(cb.data.split(":",1)[1])
    cur.execute("SELECT name,description,quantity FROM products WHERE id=?", (pid,))
    name,desc,qty = cur.fetchone()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🛒 В корзину",callback_data=f"add:{pid}")) if qty>0 else InlineKeyboardMarkup()
    await cb.message.answer(f"*{name}*\n{desc}\nОстаток: {qty}",
                            parse_mode="Markdown",reply_markup=kb)
    await cb.answer()

# ───────────────────  добавить → выбор количества
@dp.callback_query_handler(lambda c: c.data.startswith("add:"))
async def choose_qty(cb: types.CallbackQuery,state:FSMContext):
    pid=int(cb.data.split(":",1)[1])
    await state.update_data(pid=pid)
    kb=InlineKeyboardMarkup()
    for i in range(1,11):
        kb.add(InlineKeyboardButton(str(i),callback_data=f"qty:{i}"))
    await cb.message.answer("Сколько штук?",reply_markup=kb)
    await ChooseQty.waiting.set()
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("qty:"), state=ChooseQty.waiting)
async def save_qty(cb: types.CallbackQuery,state:FSMContext):
    qty=int(cb.data.split(":",1)[1])
    data=await state.get_data()
    pid=data['pid']
    cur.execute("INSERT INTO cart VALUES(?,?,?)",(cb.from_user.id,pid,qty))
    db.commit()
    await cb.message.answer("Добавлено ✅")
    await state.finish()
    await cb.answer()

# ───────────────────  корзина
@dp.message_handler(lambda m:m.text=="🧺 Корзина")
async def show_cart(m: types.Message):
    cur.execute("""SELECT cart.rowid,products.name,cart.qty
                   FROM cart JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(m.from_user.id,))
    rows=cur.fetchall()
    if not rows:
        await m.answer("Корзина пуста.")
        return
    text="\n".join(f"{rid}. {name} ×{qty}" for rid,name,qty in rows)
    kb=InlineKeyboardMarkup().add(
        InlineKeyboardButton("❌ Очистить",callback_data="clr"))
    for rid,_,_ in rows:
        kb.add(InlineKeyboardButton(f"🗑 {rid}",callback_data=f"del:{rid}"))
    kb.add(InlineKeyboardButton("✅ Оформить",callback_data="checkout"))
    await m.answer("Корзина:\n"+text,reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data=="clr")
async def clear_cart(cb: types.CallbackQuery):
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,))
    db.commit()
    await cb.message.edit_text("Корзина очищена.")
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("del:"))
async def del_item(cb: types.CallbackQuery):
    rid=int(cb.data.split(":",1)[1])
    cur.execute("DELETE FROM cart WHERE rowid=?", (rid,))
    db.commit()
    await cb.answer("Удалено")
    await show_cart(cb.message)  # перерисовать

# ──────────────── оформить
@dp.callback_query_handler(lambda c: c.data=="checkout")
async def checkout(cb: types.CallbackQuery):
    cur.execute("""SELECT products.name,cart.qty FROM cart
                   JOIN products ON products.id=cart.product_id
                   WHERE cart.user_id=?""",(cb.from_user.id,))
    items=cur.fetchall()
    if not items:
        await cb.answer("Пусто")
        return
    order_lines=[f"{n}×{q}" for n,q in items]
    cur.execute("INSERT INTO orders(user_id,items) VALUES(?,?)",
                (cb.from_user.id, ", ".join(order_lines)))
    order_id=cur.lastrowid
    db.commit()
    cur.execute("DELETE FROM cart WHERE user_id=?", (cb.from_user.id,))
    db.commit()
    for admin in ADMINS:
        await bot.send_message(admin,f"🆕 Заказ #{order_id}\n"+ "\n".join(order_lines)+
                               f"\nПользователь: {cb.from_user.get_mention()}")
    await cb.message.edit_text(f"Заказ #{order_id} принят! Менеджер свяжется 🙌")
    await cb.answer()

# ──────────────── admin «назад»
@dp.message_handler(lambda m:m.text=="⬅️ Назад" and str(m.from_user.id) in ADMINS)
async def back(m: types.Message):
    await m.answer("Админ-панель",reply_markup=admin_kb)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
